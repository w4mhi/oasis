// clock-chime.js
// -----------------------------------------------------------------------------
// The dashboard's hour bell: a strike at the top of the hour, optionally
// followed by the spoken time.
//
// It lives beside sat-alerts.js and works the same way — a pure decision half
// that is unit-tested, and a small audio half that is not. The failures here are
// silent in exactly the same way: a bell that never rings looks identical to an
// hour that has not arrived yet, and one that rings at 03:00 in a shack is a
// bug the operator only finds out about in the dark.
//
// Three states, one control, because the useful combinations are exactly three:
//   off    — nothing
//   chime  — strike only. What makes quiet hours survivable rather than binary:
//            during a contest you may want the hour marked without being
//            narrated at.
//   voice  — strike, then the spoken time: Zulu, a second's silence, local.
// Speech WITHOUT the strike is deliberately not reachable: the strike buys the
// attention the sentence needs, and without it the announcement arrives
// mid-word from across the room.
(function (root) {
  'use strict';

  var MODES = ['off', 'chime', 'voice'];

  // Quiet hours now live in common/js/quiet-hours.js, shared with satellite pass
  // alerts. The two keep SEPARATE state — silencing passes for an afternoon must
  // not stop the station keeping time — but they must not disagree about when
  // night is, and a second copy of "22 to 07" is how one ends up silent at 06:00
  // while the other is chiming. Read at CALL time, as the pages load it first.

  function oasisClockNextMode(mode) {
    // Junk (a hand-edited localStorage, a value from a future build) reads as
    // "off" and then ADVANCES, so a tap always moves. Landing back on the
    // unrecognised value would leave the control dead under the finger with
    // nothing on screen to explain it.
    var i = MODES.indexOf(mode);
    if (i < 0) i = 0;
    return MODES[(i + 1) % MODES.length];
  }

  function _qh() {
    return root.OasisQuietHours ||
      (typeof require === 'function' ? require('./quiet-hours.js') : null);
  }

  function oasisClockQuiet(localHour) { return _qh().quietAt(localHour); }
  function oasisClockOverrideUntil(now) { return _qh().overrideUntil(now); }
  function oasisClockOverrideActive(nowMs, until) {
    return _qh().overrideActive(nowMs, until);
  }

  // (nowMs, parts, mode, overrideUntil, state) → { chime, speak } for THIS tick.
  //
  //   parts — { utcH, locH, locM } read at nowMs. Passed in rather than derived
  //           so this stays pure: the local half depends on the host's timezone,
  //           which a test cannot vary without reaching into the process.
  //   state — { key }, MUTATED in place, holding the hour already handled.
  //
  // Fires on the top of the UTC hour, because the bell belongs to the Zulu card
  // and Zulu is what it announces first. In a half-hour zone (India, +05:30) the
  // local time is then 11:30 rather than 11:00, which oasisClockPhrase says out
  // loud rather than rounding into a lie.
  function oasisClockDue(nowMs, parts, mode, overrideUntil, state) {
    var none = { chime: false, speak: [] };
    var key = Math.floor(nowMs / 3600000);
    state = state || {};
    // First tick of the session only records where we are. A page that comes up
    // at 11:00:30 must not announce the hour it just missed — the same rule as
    // oasisSatAlertsDue's already-risen pass, and for the same reason: an alert
    // about something that has already happened is noise, not information.
    if (state.key === undefined) { state.key = key; return none; }
    if (state.key === key) return none;
    // Advanced BEFORE the mode and quiet checks, never after. Left un-advanced
    // while off or quiet, the first unmuted tick would fire for an hour that
    // elapsed silently hours ago.
    state.key = key;
    if (mode !== 'chime' && mode !== 'voice') return none;
    if (oasisClockQuiet(parts.locH) && !oasisClockOverrideActive(nowMs, overrideUntil)) {
      return none;
    }
    return { chime: true, speak: mode === 'voice' ? oasisClockSpeech(parts) : [] };
  }

  // ── Words, not digits ────────────────────────────────────────────────────
  // Piper's text normalisation is thin, and "18:00" through it is a coin flip —
  // "eighteen colon zero zero" is a real outcome. The string is ours to build,
  // so it is built already spoken.
  var ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
              'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
              'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
  var TENS = ['twenty', 'thirty', 'forty', 'fifty'];

  function _words(n) {
    if (n < 20) return ONES[n];
    return TENS[Math.floor(n / 10) - 2] + (n % 10 ? ' ' + ONES[n % 10] : '');
  }

  // A whole hour: "eighteen hundred", "oh one hundred", "midnight".
  function _hourWords(h) {
    if (h === 0) return 'midnight';
    if (h < 10) return 'oh ' + ONES[h] + ' hundred';
    return _words(h) + ' hundred';
  }

  // Off the hour — only reachable in a half-hour or quarter-hour zone, where the
  // top of the UTC hour is 11:30 or 11:45 locally. "Zero zero thirty" rather
  // than "midnight thirty", which is not a time anyone says.
  function _hourMinuteWords(h, m) {
    var head = h === 0 ? 'zero zero' : (h < 10 ? 'oh ' + ONES[h] : _words(h));
    return head + ' ' + _words(m);
  }

  // TWO sentences, not one, and the caller is expected to leave a gap between
  // them. Both clocks in a single utterance is eight words of numbers with one
  // breath in the middle, and the second half lands while the operator is still
  // holding the first — the listener has to keep "eighteen hundred Zulu" in
  // their head while "fourteen hundred" is already arriving. Split, each half is
  // a whole thought, and the pause is where the first one is written down.
  //
  // It also makes the two independently cacheable: the Zulu half is the same
  // string on every station at that hour, so it is warm far more often than the
  // combined sentence ever was.
  function oasisClockPhrases(parts) {
    var zulu = _hourWords(parts.utcH);
    var local = parts.locM ? _hourMinuteWords(parts.locH, parts.locM)
                           : _hourWords(parts.locH);
    return ['The time is ' + zulu + ' Zulu.', 'Local time is ' + local + '.'];
  }

  var _pad = function (n) { return (n < 10 ? '0' : '') + n; };

  // The same two phrases, each carrying the readable label its cached WAV gets
  // on the server. There are only 24 Zulu ones, so that corner of the cache
  // fills once and never churns. The local label carries MINUTES because the
  // bell fires on the top of the UTC hour: in a half-hour zone that is 04:30
  // locally, and a name saying 0400 would be a lie.
  //
  // The kind travels WITH the text rather than as a parallel array — two lists
  // the caller has to keep in step is how a label ends up on the wrong phrase.
  function oasisClockSpeech(parts) {
    var said = oasisClockPhrases(parts);
    return [
      { text: said[0], kind: 'TIME_UTC_' + _pad(parts.utcH) + '00' },
      { text: said[1], kind: 'TIME_LOC_' + _pad(parts.locH) + _pad(parts.locM || 0) },
    ];
  }

  // Read the three fields this module needs off a Date, in one place, so a
  // caller cannot mix a UTC field with a local one by accident.
  function oasisClockParts(now) {
    return { utcH: now.getUTCHours(), locH: now.getHours(), locM: now.getMinutes() };
  }

  // ── Audio (browser only) ─────────────────────────────────────────────────
  // Two slow, low strikes with a long decay — deliberately nothing like the
  // satellite VVV (three dits and a dah at 620/780 Hz). If the two were close
  // the operator would look up at the rig every hour, which is worse than no
  // chime at all.
  function _ctx() {
    if (typeof root.oasisAudioContext === 'function') return root.oasisAudioContext();
    if (typeof require === 'function') {
      try { return require('./speech.js').oasisAudioContext(); } catch (_) { /* browser */ }
    }
    return null;
  }

  function oasisClockChime(strikes) {
    var ctx = _ctx();
    if (!ctx) return false;
    var t = ctx.currentTime + 0.03;
    for (var i = 0; i < (strikes || 2); i++) {
      // Two partials, an octave apart, so it reads as a struck bell rather than
      // a test tone. The fifth would be prettier and is not worth the node.
      [330, 660].forEach(function (hz, n) {
        var osc = ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.value = hz;
        var g = ctx.createGain();
        var peak = n ? 0.09 : 0.22;        // the octave sits under the fundamental
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(peak, t + 0.008);   // hard attack: a strike
        g.gain.exponentialRampToValueAtTime(0.0001, t + 1.1);   // long decay: a bell
        osc.connect(g); g.connect(ctx.destination);
        osc.start(t); osc.stop(t + 1.2);
      });
      t += 0.55;
    }
    return true;
  }

  // How long oasisClockChime(2) runs, so a caller can let the strike finish
  // before the voice starts instead of talking over it.
  var CHIME_2_MS = 1800;

  // The silence BETWEEN the two sentences — after the first has finished, not
  // after it started. A second is long enough to be heard as a deliberate pause
  // rather than a stutter in the synthesis, and short enough that the pair still
  // reads as one announcement instead of two unrelated ones.
  var GAP_MS = 1000;

  root.OASIS_CLOCK_MODES = MODES;
  root.OASIS_CLOCK_CHIME_2_MS = CHIME_2_MS;
  root.OASIS_CLOCK_GAP_MS = GAP_MS;
  root.oasisClockNextMode = oasisClockNextMode;
  root.oasisClockQuiet = oasisClockQuiet;
  root.oasisClockOverrideUntil = oasisClockOverrideUntil;
  root.oasisClockOverrideActive = oasisClockOverrideActive;
  root.oasisClockDue = oasisClockDue;
  root.oasisClockPhrases = oasisClockPhrases;
  root.oasisClockSpeech = oasisClockSpeech;
  root.oasisClockParts = oasisClockParts;
  root.oasisClockChime = oasisClockChime;
  if (typeof module !== 'undefined' && module.exports) module.exports = root;
})(typeof window !== 'undefined' ? window : this);
