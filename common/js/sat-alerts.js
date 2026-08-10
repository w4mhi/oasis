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
// The chime is synthesised right here. The spoken heads-up goes through the
// station's own speech service (common/js/speech.js) — the AudioContext and
// the voice ladder live there now, since neither was ever satellite-specific.
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
      // Key on the rise MINUTE, not the raw value. /api/satellites/passes
      // returns microsecond precision straight from Skyfield's root-finder
      // ("2026-08-09T22:05:10.349112+00:00"), and recomputing the same pass —
      // a cache refresh, a TLE update, a different search window — shifts the
      // trailing digits. Keyed on the raw string, that reads as a DIFFERENT
      // pass: the flags reset and the bird is announced a second time. The
      // dashboard re-fetches passes every 60 s, so it only takes one recompute
      // landing inside the ten-minute window.
      // A minute is safe granularity: successive passes of the same satellite
      // are ~90 minutes apart, never within one minute of each other.
      var key = String(Math.round(rise / 60000));
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
  // The AudioContext and the speaking itself live in common/js/speech.js —
  // both are generic, and guardian and Winlink will want them too. Resolved at
  // CALL time so load order between the two files cannot matter.
  function speechApi() {
    if (typeof root.oasisSpeak === 'function') return root;
    if (typeof require === 'function') { try { return require('./speech.js'); } catch (_) { /* browser */ } }
    return null;
  }

  function oasisSatAudioUnlock() {
    var api = speechApi();
    return api ? api.oasisAudioContext() : null;
  }

  // Kept as an alias so pages and tests that predate the speech service keep
  // working; new callers should use oasisSpeak directly.
  function oasisSatSpeak(text) {
    var api = speechApi();
    return api ? api.oasisSpeak(text) : false;
  }

  // Every element scheduled but not yet finished — {osc, gain, until}. A whole
  // VVV is committed to the audio graph in one pass, up to ~2.6 s ahead of the
  // speaker, so without these handles nothing can call it back: the mute bell
  // stopped the NEXT alert and let the current one play out in full.
  var _ringing = [];

  // Morse "V" = dit dit dit dah, `reps` times at `freq` Hz. dah = 3 x dit.
  function oasisSatChime(reps, freq) {
    var ctx = oasisSatAudioUnlock();
    if (!ctx) return false;
    var unit = 0.075, pattern = [1, 1, 1, 3];
    var t = ctx.currentTime + 0.03;
    // Elements from an earlier chime that have already sounded are dead weight:
    // dropped here rather than on a timer, because a kiosk runs for weeks and an
    // unbounded list of spent oscillators is a leak nobody would ever see.
    _ringing = _ringing.filter(function (n) { return n.until > ctx.currentTime; });
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
        _ringing.push({ osc: osc, gain: g, until: t + dur + 0.02 });
        t += dur + unit;
      }
      t += unit * 3;                       // gap between repeated V's
    }
    return true;
  }

  // Cut the chime off wherever it is. An element still waiting its turn is
  // cancelled outright — stop() at a time before its own start() means it never
  // sounds at all — and one mid-dit is truncated.
  function oasisSatChimeStop() {
    var live = _ringing;
    _ringing = [];
    live.forEach(function (n) {
      try { n.osc.stop(0); } catch (e) { /* already finished */ }
      // Disconnect too: stopping a sine mid-cycle steps the output to zero,
      // which is a click. The gain ramp is what shapes the element, and it is
      // still scheduled, so drop the node out of the graph rather than let it
      // finish ramping into a destination nobody is listening to.
      try { n.gain.disconnect(); } catch (e) { /* already gone */ }
    });
    return true;
  }

  // What the mute bell calls. The bell is ONE control, so it owes the operator
  // one outcome: nothing audible. Stopping the Vs while the voice reads out
  // three birds is not silence.
  function oasisSatSilence() {
    oasisSatChimeStop();
    var api = speechApi();
    if (api && typeof api.oasisSpeakStop === 'function') api.oasisSpeakStop();
    return true;
  }

  // How long oasisSatChime(3) runs, so a caller can let the voice follow the
  // chime instead of talking over it.
  var CHIME_3_MS = 2600;

  root.OASIS_SAT_T10_MS = T10_MS;
  root.OASIS_SAT_T5_MS = T5_MS;
  root.OASIS_SAT_CHIME_3_MS = CHIME_3_MS;
  root.oasisSatAlertsDue = oasisSatAlertsDue;
  root.oasisSatSpeech = oasisSatSpeech;
  root.oasisSatAudioUnlock = oasisSatAudioUnlock;
  root.oasisSatChime = oasisSatChime;
  root.oasisSatChimeStop = oasisSatChimeStop;
  root.oasisSatSilence = oasisSatSilence;
  root.oasisSatSpeak = oasisSatSpeak;
})(typeof window !== 'undefined' ? window : this);
