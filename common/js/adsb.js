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
  root.hexIsMilitary = api.hexIsMilitary;
  root.acSymbol = api.acSymbol;
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

  // Military ICAO-address blocks (24-bit hex, inclusive [lo, hi]), ordered by
  // address. Transcribed from wiedehopf/readsb `aircraft.c isMilRange()` (the
  // maintained upstream list). readsb flags military from a per-aircraft database
  // (not offline-friendly); this allocation-range list is the DB-free equivalent.
  // Append [lo, hi] pairs to extend.
  var MIL_HEX_RANGES = [
    [0x010070, 0x01008F], [0x0A4000, 0x0A4FFF], [0x33FF00, 0x33FFFF],
    [0x350000, 0x37FFFF], [0x3AA000, 0x3AFFFF], [0x3B7000, 0x3BFFFF],
    [0x3EA000, 0x3EBFFF], [0x3F4000, 0x3FBFFF], [0x400000, 0x40003F],
    [0x43C000, 0x43CFFF], [0x444000, 0x446FFF], [0x44F000, 0x44FFFF],
    [0x457000, 0x457FFF], [0x45F400, 0x45F4FF], [0x468000, 0x4683FF],
    [0x473C00, 0x473C0F], [0x478100, 0x4781FF], [0x480000, 0x480FFF],
    [0x48D800, 0x48D87F], [0x497C00, 0x497CFF], [0x498420, 0x49842F],
    [0x4B7000, 0x4B7FFF], [0x4B8200, 0x4B82FF], [0x506F32, 0x506FFF],
    [0x70C070, 0x70C07F], [0x710258, 0x71028F], [0x710380, 0x71039F],
    [0x738A00, 0x738AFF], [0x7CF800, 0x7CFAFF], [0x800200, 0x8002FF],
    [0xADF7C8, 0xAFFFFF], [0xC20000, 0xC3FFFF], [0xE40000, 0xE41FFF],
    [0xE80600, 0xE806FF],
  ];
  function hexIsMilitary(hex) {
    if (!hex) return false;
    var n = parseInt(String(hex).replace(/[^0-9a-fA-F]/g, ''), 16);   // strip '~' TIS-B prefix etc.
    return isFinite(n) && MIL_HEX_RANGES.some(function (r) { return n >= r[0] && n <= r[1]; });
  }

  // Class-aware APRS symbol [table, code] for an aircraft, keyed on ADS-B category:
  // small /' · large-heavy \^ · heli \h · glider /g · military → glider (the [mil]
  // badge distinguishes it). Single source shared by the dashboard, index + map lists.
  function acSymbol(a) {
    if (a && hexIsMilitary(a.hex)) return ['/', 'g'];
    var c = a && a.category;
    if (c === 'B1' || c === 'B2') return ['/', 'g'];               // glider
    if (c === 'A7') return ['\\', 'h'];                            // helicopter
    if (c === 'A3' || c === 'A4' || c === 'A5') return ['\\', '^']; // large / heavy
    return ['/', "'"];                                             // small / default
  }

  return { altColor: altColor, operatorTag: operatorTag,
           recentHoursForAge: recentHoursForAge, RECENT_ALL_HOURS: RECENT_ALL_HOURS,
           hexIsMilitary: hexIsMilitary, MIL_HEX_RANGES: MIL_HEX_RANGES, acSymbol: acSymbol };
});
