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

  var QUIET_FROM = 22, QUIET_TO = 7;

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

  return { QUIET_FROM: QUIET_FROM, QUIET_TO: QUIET_TO, quietAt: quietAt,
           overrideUntil: overrideUntil, overrideActive: overrideActive,
           state: state };
});
