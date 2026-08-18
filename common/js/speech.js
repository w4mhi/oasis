// speech.js
// -----------------------------------------------------------------------------
// How OASIS says things out loud, for any page that needs to.
//
// The station synthesises the audio (Piper, if the operator installed it) and
// this plays the bytes through the SAME AudioContext that plays the pass chime.
// That is the whole point of the design: that context is already unlocked on
// the kiosk by --autoplay-policy=no-user-gesture-required and on a laptop by
// the operator's first gesture, so if you can hear the chime you can hear the
// voice. There is no second audio path to get working.
//
// The previous design routed the voice through speech-dispatcher so the
// browser's speechSynthesis would sound better. That put a second process in
// charge of the sound card, and it lost: `aplay: audio open error: Device or
// resource busy`. Nothing here owns a device.
//
// Falls back to speechSynthesis on ANY failure — endpoint absent, 4xx, decode
// error, no engine installed — so a station without Piper behaves exactly as it
// always did.
(function (root) {
  'use strict';

  var SAY_URL = '/api/speech/say?text=';

  // An optional readable label for the SERVER's cache filename. The hour bell
  // and the greeting pass one; a pass alert deliberately does not, and neither
  // does the recital — its text is base64 on disk for spoiler protection, and a
  // readable cache name would undo that. The server whitelists this before it
  // reaches a filename, so a bad value costs the label, never the announcement.
  function sayUrl(text, kind) {
    return SAY_URL + encodeURIComponent(text) +
           (kind ? '&kind=' + encodeURIComponent(kind) : '');
  }

  var _ctx = null;
  // What is sounding right now, so the mute bell can cut it off mid-sentence —
  // {src, settle}. A mute that only stops the NEXT announcement is not a mute:
  // the operator taps it because of the noise they can hear.
  var _playing = null;
  // Bumped by oasisSpeakStop. Every announcement carries the generation it was
  // QUEUED in, and a step whose generation is stale drops itself instead of
  // playing. That is what stops the two birds still queued behind the one being
  // silenced, and the fetch/decode already in flight for them.
  var _gen = 0;
  // One AudioContext per page, created lazily. Browsers start it SUSPENDED
  // until a user gesture; the kiosk boots unattended and is never touched,
  // which is why enable-autostart-pi.py passes the autoplay flag. Both paths
  // are needed: the flag for the kiosk, the gesture for an ordinary browser.
  function oasisAudioContext() {
    try {
      if (!_ctx) _ctx = new (root.AudioContext || root.webkitAudioContext)();
      if (_ctx.state === 'suspended') _ctx.resume();
    } catch (e) { /* no Web Audio here — pages still work, silently */ }
    return _ctx;
  }

  // Which voice, in order of preference, for the FALLBACK engine. Taking
  // whatever the engine listed first is not a choice, it's a lottery:
  // speech-dispatcher reports every espeak-ng variant crossed with every
  // language — 14,805 entries on a stock Pi — so the winner was whatever
  // sorted first, which is a male formant preset that reads as a 1985
  // answering machine. macOS happened to sort a good voice first and hid the
  // problem for a long time.
  //
  // Matched as a lower-cased SUBSTRING, first hit wins, English only:
  //   piper/jenny — a Piper voice exposed through the OS, if there is one
  //   samantha    — macOS's recorded voice, so a dev box sounds as it always did
  //   +steph2     — espeak-ng's female variant, the floor every Pi has for free.
  //                 Chromium names it "English (America)+Steph2" — capitalised,
  //                 while espeak's own form is lower case, hence the fold.
  var VOICE_PREFS = ['piper', 'jenny', 'samantha', '+steph2'];

  function oasisPickVoice(voices) {
    var english = [], i, j;
    for (i = 0; i < (voices || []).length; i++) {
      var lang = voices[i] && voices[i].lang;
      if (lang && String(lang).toLowerCase().indexOf('en') === 0) english.push(voices[i]);
    }
    for (i = 0; i < VOICE_PREFS.length; i++) {
      for (j = 0; j < english.length; j++) {
        var name = String(english[j].name || '').toLowerCase();
        if (name.indexOf(VOICE_PREFS[i]) !== -1) return english[j];
      }
    }
    return english[0] || null;
  }

  // Chromium builds its voice list ASYNCHRONOUSLY: the first getVoices() call
  // returns [] and only populates once the engine has enumerated. Touching it at
  // load — and again on the change event — means the first spoken announcement of
  // the session isn't the one that discovers there are no voices yet and falls
  // past the whole ladder to the engine's raw default. addEventListener, not
  // onvoiceschanged, so this never stomps a handler a page has set for its own
  // reasons.
  try {
    if (root.speechSynthesis) {
      root.speechSynthesis.getVoices();
      if (root.speechSynthesis.addEventListener) {
        root.speechSynthesis.addEventListener('voiceschanged', function () {
          root.speechSynthesis.getVoices();
        });
      }
    }
  } catch (e) { /* no speech synthesis here — pages still work, silently */ }

  // A caller's onstart must never be able to silence the station: a throw here
  // would land in oasisSpeakOne's catch and be "recovered" by speaking the same
  // words again through the fallback engine.
  function _fire(fn) {
    try { fn(); } catch (e) { /* the caller's problem, not the voice's */ }
  }

  // Stop talking, now, and drop everything queued behind it. Returns true so a
  // caller can chain it; there is no failure mode worth reporting — a station
  // with nothing playing is already in the state being asked for.
  function oasisSpeakStop() {
    _gen++;
    var p = _playing;
    _playing = null;
    if (p) {
      // stop() normally fires `ended` on its own, which would settle the promise
      // for us. Settling it explicitly as well is what keeps a browser that
      // skips the event from wedging the queue — and with it jennySpeak's busy
      // counter, which would leave the avatar animating at nobody forever.
      try { p.src.stop(); } catch (e) { /* never started, or already stopped */ }
      p.settle();
    }
    // The fallback engine keeps a queue of its own. Leaving it running would
    // silence Piper and let espeak read the backlog, which is a worse mute than
    // no mute at all.
    try {
      if (root.speechSynthesis && root.speechSynthesis.cancel) root.speechSynthesis.cancel();
    } catch (e) { /* no speech synthesis here */ }
    return true;
  }

  // Synthesise `text` on the station NOW and keep nothing — the point is the
  // server-side cache it leaves behind, so the real request moments later is a
  // hit instead of a wait. On a Pi 5 that is the difference between 3.6 s and
  // 1.5 ms, which is the difference between an avatar that mouths at silence
  // and one that starts talking when the sound does.
  //
  // HEAD, not GET, and not negotiable: a fetch() whose body is never read pins a
  // 2 MiB /dev/shm data pipe per request for the life of the page. That leak
  // cost ~500 MB/h on a kiosk once already.
  function oasisSpeakWarm(text, kind) {
    if (!text || !root.fetch) return Promise.resolve(false);
    return root.fetch(sayUrl(text, kind), { method: 'HEAD' })
      .then(function (res) { return !!(res && res.ok); }, function () { return false; });
  }

  function oasisSpeakFallback(text, onstart) {
    if (!text || !root.speechSynthesis || !root.SpeechSynthesisUtterance) return false;
    try {
      var u = new root.SpeechSynthesisUtterance(text);
      u.rate = 0.9; u.pitch = 1.0; u.volume = 1.0;
      if (onstart) u.onstart = function () { _fire(onstart); };
      // An EMPTY voice list is not proof there is no voice: Chromium on Linux
      // enumerates asynchronously and can report [] while speak() works fine.
      // Bailing here once left a Pi with espeak installed completely silent.
      var picked = oasisPickVoice(root.speechSynthesis.getVoices());
      if (picked) u.voice = picked;
      root.speechSynthesis.speak(u);
      return true;
    } catch (e) { return false; }
  }

  // Say it with the station's own voice, falling back to the browser's.
  // Resolves true if SOMETHING spoke. This is the UNSERIALISED worker —
  // oasisSpeak (below) is the public entry point and queues calls to this.
  function oasisSpeakOne(text, gen, onstart, kind) {
    // Only `fetch` is checked here. Web Audio is checked INSIDE, where a
    // missing context throws into the same catch as every other failure — if
    // this bailed early on no-AudioContext, the fetch path would be skipped
    // wholesale in any environment without Web Audio, including the test
    // harness, and the fallback tests would pass without ever exercising it.
    if (!root.fetch) return Promise.resolve(gen === _gen && oasisSpeakFallback(text, onstart));
    return root.fetch(sayUrl(text, kind))
      .then(function (res) {
        // The body is read on EVERY path, ok or not -- oasisSpeakWarm above says
        // why an unread one is a leak. arrayBuffer() is the drain, and a body we
        // could not read is as unspeakable as a bad status, so both take the
        // same throw rather than leaving the pipe pinned on the way out.
        return res.arrayBuffer().catch(function () { return null; })
          .then(function (buf) {
            if (!res.ok || !buf) throw new Error('speech ' + res.status);
            return buf;
          });
      })
      .then(function (buf) {
        var ctx = oasisAudioContext();
        if (!ctx) throw new Error('no audio context');
        return new Promise(function (resolve, reject) {
          // Callback form, not the promise form: older Chromium on Pi OS only
          // implements the callbacks.
          ctx.decodeAudioData(buf, resolve, reject);
        }).then(function (audio) {
          // Silenced while this was still fetching or decoding. Returning here
          // rather than throwing keeps it out of the catch below, which would
          // otherwise "recover" by speaking the same words through the fallback
          // engine — the one thing a mute must never do.
          if (gen !== _gen) return false;
          return new Promise(function (resolve) {
            var src = ctx.createBufferSource();
            src.buffer = audio;
            src.connect(ctx.destination);
            var settled = false;
            // Resolve on `onended`, not on `start()`: the whole point of
            // oasisSpeak's queue below is that the NEXT announcement must
            // not begin until THIS one has actually finished playing, not
            // merely been handed to the audio graph. `done` reports whether it
            // was allowed to finish — false means oasisSpeakStop cut it off.
            var settle = function (done) {
              if (settled) return;
              settled = true;
              if (_playing && _playing.src === src) _playing = null;
              resolve(!!done);
            };
            src.onended = function () { settle(true); };
            // Fired HERE, one statement before the first sample, not when the
            // announcement was requested. jennySpeak used to wake the avatar at
            // call time and then wait out the synthesis: on a cache miss that is
            // 3.6 s of a mouth moving at silence, which reads as broken.
            if (onstart) _fire(onstart);
            src.start();
            _playing = { src: src, settle: function () { settle(false); } };
          });
        });
      })
      // speechSynthesis's own queue means this branch, unlike the audio one
      // above, is allowed to resolve immediately rather than waiting for the
      // utterance to finish — the browser will not start it early. That
      // asymmetry is deliberate, not an oversight.
      .catch(function () { return gen === _gen && oasisSpeakFallback(text, onstart); });
  }

  // Announcements are SERIALISED behind this module-level chain so that
  // several birds crossing their T-10 threshold on the same tick do not play
  // on top of each other. On `main` this fell out for free: every call ended
  // in speechSynthesis.speak(), and the Web Speech API queues utterances. The
  // Piper path has no queue of its own — each call starts playing as soon as
  // its own decode resolves — so without this, N simultaneous announcements
  // become N-part mush. common/speech_play.py serialises the Pi's own
  // speaker for exactly the same reason.
  var _queue = Promise.resolve();

  // opts.onstart — called once, immediately before the first sample, and NOT at
  // all if the announcement is silenced or fails before reaching the speaker.
  // Anything that has to look like it is talking hangs off this rather than off
  // the call, because between the two sits a synthesis that can take seconds.
  function oasisSpeak(text, opts) {
    if (!text) return Promise.resolve(false);
    var onstart = opts && opts.onstart;
    var kind = opts && opts.kind;
    // Captured HERE, at enqueue time, not inside oasisSpeakOne. Read when the
    // queue finally reaches it, the generation would already have caught up with
    // the stop and the announcement would speak anyway — which is precisely the
    // bug: the birds queued behind the one being silenced are the loudest part.
    var gen = _gen;
    var result = _queue.then(function () {
      if (gen !== _gen) return false;
      return oasisSpeakOne(text, gen, onstart, kind);
    });
    // Reset the chain on EITHER outcome. If a step were ever left to reject
    // through, every announcement queued behind it would wait on a promise
    // that never resolves and would simply never speak.
    _queue = result.then(function () {}, function () {});
    return result;
  }

  root.oasisAudioContext = oasisAudioContext;
  root.oasisPickVoice = oasisPickVoice;
  root.oasisSpeakFallback = oasisSpeakFallback;
  root.oasisSpeak = oasisSpeak;
  root.oasisSpeakStop = oasisSpeakStop;
  root.oasisSpeakWarm = oasisSpeakWarm;
  if (typeof module !== 'undefined' && module.exports) module.exports = root;
})(typeof window !== 'undefined' ? window : this);
