/*
 * solar-data.js — shared read helpers for OASIS solar / propagation data.
 *
 * solar.html WRITES the latest HAMQSL report into localStorage['solar-history']
 * (newest first, see saveHistory there). These helpers let other pages
 * (grayline.html) READ it without duplicating the storage contract: the latest
 * report, a band group's day/night condition, its age/staleness, and a coarse
 * distance -> band-group heuristic for path suggestions.
 *
 * Classic <script> served by Flask (loaded by grayline.html); also requireable
 * by node --test (no root package.json -> .js is CommonJS).
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisSolar = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  var HISTORY_KEY = 'solar-history';

  // The newest saved report, or null. `storage` defaults to localStorage in the
  // browser; tests pass a stub exposing getItem().
  function latestSolar(storage) {
    var s = storage || (typeof localStorage !== 'undefined' ? localStorage : null);
    if (!s) return null;
    try {
      var hist = JSON.parse(s.getItem(HISTORY_KEY) || 'null');
      return (Array.isArray(hist) && hist.length) ? hist[0] : null;
    } catch (e) {
      return null;
    }
  }

  // {day, night} conditions for a band group (e.g. '30m-20m'), or {} if absent.
  function bandCondition(report, group) {
    if (!report || !report.bands) return {};
    return report.bands[group] || {};
  }

  // Hours since the report was saved (report.savedAt is an ISO string written by
  // solar.html). Infinity when unknown, so callers treat it as "stale".
  function ageHours(report, nowMs) {
    if (!report || !report.savedAt) return Infinity;
    var saved = new Date(report.savedAt).getTime();
    if (isNaN(saved)) return Infinity;
    var now = (typeof nowMs === 'number') ? nowMs : Date.now();
    return (now - saved) / 3600000;
  }

  function isStale(report, hours, nowMs) {
    return ageHours(report, nowMs) > (typeof hours === 'number' ? hours : 24);
  }

  // Distance (km) -> ordered band groups likely usable for that path (best first).
  // Coarse by necessity — matched to HAMQSL's grouped bands. Refine with operator
  // feedback; the groups map to solar.html's BAND_ORDER.
  function bandsForDistanceKm(km) {
    if (km == null || isNaN(km)) return [];
    if (km < 400) return ['80m-40m'];                       // NVIS / very short
    if (km < 1500) return ['80m-40m', '30m-20m'];           // short
    if (km < 3500) return ['30m-20m', '17m-15m'];           // medium
    if (km < 7000) return ['30m-20m', '17m-15m', '12m-10m']; // long
    return ['17m-15m', '12m-10m', '30m-20m'];               // very long
  }

  return {
    HISTORY_KEY: HISTORY_KEY,
    latestSolar: latestSolar,
    bandCondition: bandCondition,
    ageHours: ageHours,
    isStale: isStale,
    bandsForDistanceKm: bandsForDistanceKm,
  };
});
