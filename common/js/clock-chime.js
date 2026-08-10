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
//   voice  — strike, then the spoken time.
// Speech WITHOUT the strike is deliberately not reachable: the strike buys the
// attention the sentence needs, and without it the announcement arrives
// mid-word from across the room.
(function (root) {
  'use strict';

  var MODES = ['off', 'chime', 'voice'];

  // Quiet hours, in LOCAL time — 22:00 to 07:00. Local even though the bell sits
  // on the Zulu card and announces Zulu first: quiet hours are about when the
  // operator is asleep, which no other clock knows. Reading these off the UTC
  // hour instead would, at UTC-7, silence the shack from 05:00 to 14:00 local
  // and chime all night — a total inversion that reads as a timezone bug rather
  // than the design mistake it would be.
  var QUIET_FROM = 22, QUIET_TO = 7;

  function oasisClockNextMode(mode) {
    // Junk (a hand-edited localStorage, a value from a future build) reads as
    // "off" and then ADVANCES, so a tap always moves. Landing back on the
    // unrecognised value would leave the control dead under the finger with
    // nothing on screen to explain it.
    var i = MODES.indexOf(mode);
    if (i < 0) i = 0;
    return MODES[(i + 1) % MODES.length];
  }

  function oasisClockQuiet(localHour) {
    return localHour >= QUIET_FROM || localHour < QUIET_TO;
  }

  // The end of an operator override, as a timestamp: the NEXT 07:00 local.
  //
  // An override expires on its own because a contest is a night, not a change of
  // policy. A "quiet hours off" switch would sit forgotten for months and then
  // surprise someone at 03:00 — the operator would have to remember to undo
  // something they set once, which is the kind of state nobody remembers.
  function oasisClockOverrideUntil(now) {
    var d = new Date(now.getTime());
    if (d.getHours() >= QUIET_TO) d.setDate(d.getDate() + 1);
    d.setHours(QUIET_TO, 0, 0, 0);
    return d.getTime();
  }

  function oasisClockOverrideActive(nowMs, overrideUntil) {
    return !!overrideUntil && nowMs < overrideUntil;
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
    var none = { chime: false, speak: '' };
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
    return { chime: true, speak: mode === 'voice' ? oasisClockPhrase(parts) : '' };
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

  function oasisClockPhrase(parts) {
    var zulu = _hourWords(parts.utcH);
    var local = parts.locM ? _hourMinuteWords(parts.locH, parts.locM)
                           : _hourWords(parts.locH);
    return 'The time is ' + zulu + ' Zulu. Local time is ' + local + '.';
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

  root.OASIS_CLOCK_MODES = MODES;
  root.OASIS_CLOCK_CHIME_2_MS = CHIME_2_MS;
  root.oasisClockNextMode = oasisClockNextMode;
  root.oasisClockQuiet = oasisClockQuiet;
  root.oasisClockOverrideUntil = oasisClockOverrideUntil;
  root.oasisClockOverrideActive = oasisClockOverrideActive;
  root.oasisClockDue = oasisClockDue;
  root.oasisClockPhrase = oasisClockPhrase;
  root.oasisClockParts = oasisClockParts;
  root.oasisClockChime = oasisClockChime;
  if (typeof module !== 'undefined' && module.exports) module.exports = root;
})(typeof window !== 'undefined' ? window : this);
