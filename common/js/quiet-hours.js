/*
 * quiet-hours.js — when the shack is asleep, and how an operator overrides it.
 *
 * ONE definition of the window and of what an override means, shared by the
 * hour bell and by satellite pass alerts. The two keep SEPARATE state on
 * purpose — silencing passes for an afternoon must not stop the station keeping
 * time, and vice versa — but they must not disagree about when night is. A
 * second copy of "22 to 07" is how one of them ends up silent at 06:00 while
 * the other is chiming.
 *
 * LOCAL time, deliberately. Quiet hours are about when the operator is asleep,
 * which no other clock knows. Reading them off UTC would, at UTC-7, silence the
 * shack from 05:00 to 14:00 local and let it ring all night — an inversion that
 * reads as a timezone bug rather than the design mistake it would be.
 *
 * Pure: no storage, no DOM, no Date.now(). Callers pass the time in, which is
 * what lets a test cross midnight without touching the host's timezone.
 */
(function (root, factory) {
  var api = factory();
  root.OasisQuietHours = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /* The window is NOT defined here — it lives in common/quiet-hours.json so the
     Python half (common/quiet_hours.py) reads the same numbers. These values are
     a FALLBACK for a page that never called load(), and for Node tests. If you
     find yourself editing them, edit the JSON instead. */
  var QUIET_FROM = 22, QUIET_TO = 7;

  /* Adopt the shared definition. Pages call this once at startup; everything
     else stays synchronous and pure. Failure is silent and harmless — the
     fallback above is the same pair the file ships with. */
  function load(json) {
    if (!json) { return; }
    var f = parseInt(json.from, 10), t = parseInt(json.to, 10);
    if (!isNaN(f) && !isNaN(t)) { QUIET_FROM = f; QUIET_TO = t; }
  }

  /* Spans midnight, so this is an OR and not a range test. */
  function quietAt(localHour) {
    return localHour >= QUIET_FROM || localHour < QUIET_TO;
  }

  /* The end of an operator override, as a timestamp: the NEXT 07:00 local.
     An override expires on its own because a night pass is a night, not a change
     of policy. A permanent "quiet hours off" switch would sit forgotten for
     months and then surprise someone at 03:00 — state nobody remembers setting
     is state nobody remembers to undo. */
  function overrideUntil(now) {
    var d = new Date(now.getTime());
    if (d.getHours() >= QUIET_TO) d.setDate(d.getDate() + 1);
    d.setHours(QUIET_TO, 0, 0, 0);
    return d.getTime();
  }

  function overrideActive(nowMs, until) {
    return !!until && nowMs < until;
  }

  /* The whole decision in one call, so two screens cannot compute it differently.
       muted         — the per-DEVICE mute, which outranks everything
       localHour     — the operator's hour, not UTC
       nowMs, until  — for the override
     Returns { silent, quiet, overridden } so a caller can both ACT on `silent`
     and SHOW why: a bell dimmed by quiet hours and a bell crossed out by a mute
     are different states and must not look alike. */
  function state(muted, localHour, nowMs, until) {
    var quiet = quietAt(localHour);
    var overridden = overrideActive(nowMs, until);
    return {
      quiet: quiet,
      overridden: overridden,
      silent: !!muted || (quiet && !overridden),
    };
  }

  var api = { quietAt: quietAt, overrideUntil: overrideUntil,
              overrideActive: overrideActive, state: state, load: load };
  // Live getters, not a one-time copy: load() reassigns the vars above after
  // this factory has already run once, and a snapshot here would freeze at the
  // fallback forever — the exact "second copy that disagrees" this file exists
  // to prevent, just moved one level down.
  Object.defineProperty(api, 'QUIET_FROM', { get: function () { return QUIET_FROM; } });
  Object.defineProperty(api, 'QUIET_TO', { get: function () { return QUIET_TO; } });
  return api;
});
