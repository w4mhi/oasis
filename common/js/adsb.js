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
    if (alt === 'ground') return '#A0522D'; 
    if (alt == null) return '#B8C4D0'; 
    
    var ft = Number(alt);
    if (!isFinite(ft) || ft < 0) return '#B8C4D0'; 

    if (ft <= 1500)  return '#8d24d8'; // Indigo
    if (ft <= 3000)  return '#3939f7'; // Blue
    if (ft <= 6000)  return '#0080FF'; // Light Blue
    if (ft <= 10000) return '#00FFFF'; // Cyan
    if (ft <= 14000) return '#00FF80'; // Teal
    if (ft <= 18000) return '#00FF00'; // Green
    if (ft <= 23000) return '#80FF00'; // Chartreuse
    if (ft <= 28000) return '#FFFF00'; // Yellow
    if (ft <= 33000) return '#FFCC00'; // Amber
    if (ft <= 38000) return '#FF8000'; // Orange
    if (ft <= 43000) return '#FF0000'; // Red
    return '#FF00FF';                  // Magenta
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
