// sat-alerts.js
// -----------------------------------------------------------------------------
// Shared satellite pass-alert engine for the Satellites page and the kiosk
// dashboard, so the shack hears the same thing whichever screen is awake.
//
// An armed bird (roster `bell` — see sat-bells.js) chimes Morse "V" (· · · —)
// twice: at T-10 min, then again at T-5 min, with the second one pitched higher.
// There is deliberately NO alert at AOS — by the time the bird is over the
// horizon it is too late to get the rig on frequency, which is the entire point
// of the warning. The T-10 chime is followed by a spoken heads-up naming the bird
// and its maximum elevation.
//
// Everything here is synthesised or spoken by the browser — no audio file, no
// network, nothing to install beyond the TTS voice itself
// (services/satellites/install-voice.py). Offline by construction.
//
// The decision half is pure and unit-tested because its failures are silent: a
// missed edge means the operator simply never hears the pass, and a re-fire
// bug means the chime nags every 2 s until the bird rises.
(function (root) {
  'use strict';

  var T10_MS = 10 * 60 * 1000;
  var T5_MS = 5 * 60 * 1000;
  var FREQ_T10 = 620;          // first warning
  var FREQ_T5 = 780;           // closer → higher, reads as more urgent

  // (birds, nowMs, state) → { fire, freq, announce } for THIS tick.
  //
  //   birds — [{norad, name, rise, max_el}] for ARMED birds only, `rise` being an
  //           ISO string or epoch ms. Callers filter by bell; this does not.
  //   state — norad → {key, t10, t5}, MUTATED in place. It is what makes each
  //           threshold fire once per pass and re-arm for the next one: `key` is
  //           the rise time, so a new pass resets the flags on its own.
  //
  // Returns fire=true if anything crossed a threshold (the caller chimes once for
  // the whole tick, not once per bird), the pitch to use, and the birds that just
  // crossed T-10 and therefore get spoken.
  //
  // A bird already risen (tMinus <= 0) never fires: catching up on a warning for
  // a pass in progress would be noise, not information.
  function oasisSatAlertsDue(birds, nowMs, state) {
    var fire = false, freq = FREQ_T10, announce = [];
    state = state || {};
    (birds || []).forEach(function (b) {
      if (!b || b.rise == null || b.norad == null) return;
      var rise = typeof b.rise === 'number' ? b.rise : Date.parse(b.rise);
      if (!isFinite(rise)) return;
      var key = String(b.rise);
      var st = state[b.norad];
      if (!st || st.key !== key) st = state[b.norad] = { key: key, t10: false, t5: false };
      var tMinus = rise - nowMs;
      if (tMinus <= 0) return;
      if (!st.t10 && tMinus <= T10_MS) { st.t10 = true; fire = true; announce.push(b); }
      // Not an `else`: a page opened inside the last 5 minutes crosses BOTH
      // thresholds on its first tick and should get the urgent pitch, not the
      // relaxed one it already missed.
      if (!st.t5 && tMinus <= T5_MS) { st.t5 = true; fire = true; freq = FREQ_T5; }
    });
    return { fire: fire, freq: freq, announce: announce };
  }

  // The spoken heads-up. Shared so both screens say the same sentence — and so
  // the wording is testable without a speech synthesiser attached.
  function oasisSatSpeech(bird) {
    if (!bird || !bird.name) return '';
    var el = Math.round(Number(bird.max_el));
    var base = bird.name + ', in ten minutes';
    return isFinite(el) ? base + ', maximum elevation ' + el + ' degrees' : base;
  }

  // ── Audio (browser only) ───────────────────────────────────────────────────
  // One AudioContext per page, created lazily. Browsers start it SUSPENDED until
  // a user gesture, which is fine on a laptop but useless on a kiosk that boots
  // unattended and is never touched — that case is handled by launching Chromium
  // with --autoplay-policy=no-user-gesture-required (scripts/enable-autostart-pi.py).
  // Both paths are needed: the flag for the untouched kiosk, the gesture unlock
  // for an ordinary browser.
  var _ctx = null;
  function oasisSatAudioUnlock() {
    try {
      if (!_ctx) _ctx = new (root.AudioContext || root.webkitAudioContext)();
      if (_ctx.state === 'suspended') _ctx.resume();
    } catch (e) { /* no Web Audio here — the page still works, silently */ }
    return _ctx;
  }

  // Morse "V" = dit dit dit dah, `reps` times at `freq` Hz. dah = 3 x dit.
  function oasisSatChime(reps, freq) {
    var ctx = oasisSatAudioUnlock();
    if (!ctx) return false;
    var unit = 0.075, pattern = [1, 1, 1, 3];
    var t = ctx.currentTime + 0.03;
    for (var r = 0; r < (reps || 1); r++) {
      for (var i = 0; i < pattern.length; i++) {
        var dur = pattern[i] * unit;
        var osc = ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.value = freq || FREQ_T10;
        var g = ctx.createGain();
        // Ramped rather than switched: a square-edged gate on a sine puts a click
        // at both ends of every element.
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.3, t + 0.006);
        g.gain.setValueAtTime(0.3, t + dur - 0.008);
        g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
        osc.connect(g); g.connect(ctx.destination);
        osc.start(t); osc.stop(t + dur + 0.02);
        t += dur + unit;
      }
      t += unit * 3;                       // gap between repeated V's
    }
    return true;
  }

  // How long oasisSatChime(3) runs, so a caller can let the voice follow the
  // chime instead of talking over it.
  var CHIME_3_MS = 2600;

  // Speak a line, if this box has a voice at all. Chromium on Pi OS exposes none
  // unless speech-dispatcher + espeak-ng are installed
  // (services/satellites/install-voice.py) AND it was launched with
  // --enable-speech-dispatcher. Absent either, this no-ops and the chime still
  // carries the alert — the voice is an enhancement, never the whole signal.
  function oasisSatSpeak(text) {
    if (!text || !root.speechSynthesis) return false;
    try {
      var voices = root.speechSynthesis.getVoices() || [];
      if (!voices.length) return false;
      var u = new root.SpeechSynthesisUtterance(text);
      u.rate = 0.9; u.pitch = 1.0; u.volume = 1.0;
      for (var i = 0; i < voices.length; i++) {
        if (voices[i].lang && voices[i].lang.toLowerCase().indexOf('en') === 0) {
          u.voice = voices[i];
          break;
        }
      }
      root.speechSynthesis.speak(u);
      return true;
    } catch (e) { return false; }
  }

  // Chromium builds its voice list ASYNCHRONOUSLY: the first getVoices() call
  // returns [] and only populates once the engine has enumerated. Touching it at
  // load — and again on the change event — means the first pass alert of the
  // session isn't the one that discovers there are no voices yet and stays
  // silent. addEventListener, not onvoiceschanged, so this never stomps a
  // handler a page has set for its own reasons.
  try {
    if (root.speechSynthesis) {
      root.speechSynthesis.getVoices();
      if (root.speechSynthesis.addEventListener) {
        root.speechSynthesis.addEventListener('voiceschanged', function () {
          root.speechSynthesis.getVoices();
        });
      }
    }
  } catch (e) { /* no speech synthesis here — chime-only, by design */ }

  root.OASIS_SAT_T10_MS = T10_MS;
  root.OASIS_SAT_T5_MS = T5_MS;
  root.OASIS_SAT_CHIME_3_MS = CHIME_3_MS;
  root.oasisSatAlertsDue = oasisSatAlertsDue;
  root.oasisSatSpeech = oasisSatSpeech;
  root.oasisSatAudioUnlock = oasisSatAudioUnlock;
  root.oasisSatChime = oasisSatChime;
  root.oasisSatSpeak = oasisSatSpeak;
})(typeof window !== 'undefined' ? window : this);
