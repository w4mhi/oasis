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

  var _ctx = null;
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

  function oasisSpeakFallback(text) {
    if (!text || !root.speechSynthesis || !root.SpeechSynthesisUtterance) return false;
    try {
      var u = new root.SpeechSynthesisUtterance(text);
      u.rate = 0.9; u.pitch = 1.0; u.volume = 1.0;
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
  // Resolves true if SOMETHING spoke.
  function oasisSpeak(text) {
    if (!text) return Promise.resolve(false);
    // Only `fetch` is checked here. Web Audio is checked INSIDE, where a
    // missing context throws into the same catch as every other failure — if
    // this bailed early on no-AudioContext, the fetch path would be skipped
    // wholesale in any environment without Web Audio, including the test
    // harness, and the fallback tests would pass without ever exercising it.
    if (!root.fetch) return Promise.resolve(oasisSpeakFallback(text));
    return root.fetch(SAY_URL + encodeURIComponent(text))
      .then(function (res) {
        if (!res.ok) throw new Error('speech ' + res.status);
        return res.arrayBuffer();
      })
      .then(function (buf) {
        var ctx = oasisAudioContext();
        if (!ctx) throw new Error('no audio context');
        return new Promise(function (resolve, reject) {
          // Callback form, not the promise form: older Chromium on Pi OS only
          // implements the callbacks.
          ctx.decodeAudioData(buf, resolve, reject);
        }).then(function (audio) {
          var src = ctx.createBufferSource();
          src.buffer = audio;
          src.connect(ctx.destination);
          src.start();
          return true;
        });
      })
      .catch(function () { return oasisSpeakFallback(text); });
  }

  root.oasisAudioContext = oasisAudioContext;
  root.oasisPickVoice = oasisPickVoice;
  root.oasisSpeakFallback = oasisSpeakFallback;
  root.oasisSpeak = oasisSpeak;
  if (typeof module !== 'undefined' && module.exports) module.exports = root;
})(typeof window !== 'undefined' ? window : this);
