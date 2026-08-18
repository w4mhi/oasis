// nwr-alerts.js
// -----------------------------------------------------------------------------
// Shared predicate for which received NWR alerts belong on the map right now.
//
// services/nwr/common/alerts.py's active() deliberately keeps a clock_suspect
// record alive for up to STALE_CLOCK_QUARANTINE_S (90 days) — the STORE must
// never let a bad boot clock silently retire a live warning. Both OASIS Pis
// have actually booted weeks stale with every health check green, which is
// exactly what sets clock_suspect, so that floor is deliberate and correct
// for the store.
//
// The map is a different consumer. A passer-by reading it wants "what's
// happening right now," and a clock_suspect record gets a much shorter leash
// here — NWR_CLOCK_SUSPECT_MAP_RECENCY_S, bounded by realistic SAME
// watch/warning lifetimes (a Tornado/Severe WATCH can run several hours;
// warnings are almost always under two) plus slack for an operator to notice
// and fix a stale clock, not the store's 90-day worst-case-reboot floor.
// `received` is stamped from the same untrusted clock as everything else on
// a clock_suspect record, but comparing it to "now" on the SAME box is still
// meaningful even when the absolute date is wrong, exactly as
// alerts.py's _is_active() already reasons for its own 90-day check — a
// frozen-but-self-consistent clock still measures elapsed time correctly.
(function (root) {
  'use strict';

  var NWR_CLOCK_SUSPECT_MAP_RECENCY_S = 6 * 3600;   // 6 hours

  // (alert record, Set of active ids from /api/nwr/alerts, now in epoch
  // SECONDS) -> bool. Mirrors the map/kiosk filter (active + matched + a
  // real hazard type) and adds the clock_suspect recency check.
  function nwrMapVisible(a, activeIds, nowS) {
    if (!activeIds.has(a.id) || a.type === null || !a.matched) { return false; }
    if (a.clock_suspect) {
      return (nowS - (a.received || 0)) < NWR_CLOCK_SUSPECT_MAP_RECENCY_S;
    }
    return true;
  }

  root.NWR_CLOCK_SUSPECT_MAP_RECENCY_S = NWR_CLOCK_SUSPECT_MAP_RECENCY_S;
  root.nwrMapVisible = nwrMapVisible;
})(typeof window !== 'undefined' ? window : this);
