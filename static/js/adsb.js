/*
 * adsb.js — shared ADS-B front-end helpers for OASIS.
 *
 * altColor        altitude(ft) -> hex; the single source (was inline in map.html).
 * operatorTag     flight callsign -> airline telephony name (globalThis.OASIS_OPERATORS).
 * recentHoursForAge  Age <select> value -> hours of aircraft history to fetch.
 *
 * Classic <script> served by Flask (loaded by the map + dashboard); also
 * requireable by node --test (no root package.json -> .js is CommonJS).
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisAdsb = api;
  root.altColor = api.altColor;
  root.operatorTag = api.operatorTag;
  root.recentHoursForAge = api.recentHoursForAge;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  function altColor(alt) {
    if (alt === 'ground')   return '#A0522D';   // brown — surface
    if (alt == null)        return '#B8C4D0';   // null/undefined -> unknown altitude
    var ft = Number(alt);
    if (!isFinite(ft))      return '#B8C4D0';   // unknown altitude -> neutral grey
    if (ft <= 2000)         return '#FF3311';
    if (ft <= 5000)         return '#FF7F00';
    if (ft <= 10000)        return '#FFFF00';
    if (ft <= 15000)        return '#11FF66';
    if (ft <= 25000)        return '#00FFFF';
    if (ft <= 35000)        return '#0066FF';
    return '#FF00FF';
  }

  // Airline telephony callsign from a flight id: 3 letters + a digit -> ICAO
  // operator code -> table lookup. Registrations (N12345) and unknown codes
  // return ''. `table` defaults to the vendored globalThis.OASIS_OPERATORS.
  function operatorTag(flight, table) {
    var m = String(flight || '').trim().toUpperCase().match(/^([A-Z]{3})\d/);
    if (!m) return '';
    var t = table || (root && root.OASIS_OPERATORS) || {};
    return t[m[1]] || '';
  }

  var RECENT_ALL_HOURS = 1e7;   // ~1141 yr -> recorder's now-hours*3600 cutoff goes negative -> all history
  function recentHoursForAge(ageValue) {
    return String(ageValue) === '0' ? RECENT_ALL_HOURS : 24;
  }

  return { altColor: altColor, operatorTag: operatorTag,
           recentHoursForAge: recentHoursForAge, RECENT_ALL_HOURS: RECENT_ALL_HOURS };
});
