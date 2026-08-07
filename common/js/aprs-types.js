/*
 * aprs-types.js — the canonical APRS symbol → type table for OASIS.
 *
 * TABLE        sym_code -> [categoryKey, label]
 * categoryOf   station -> {key, label}   (map legend / objects panel)
 * labelOf      station -> label          (dashboard Type column + Type filter)
 *
 * This table used to live twice: index.html's _APRS_TYPES (sym_code -> label)
 * and maps/traffic/map.html's _OBJ_CATS (sym_code -> [key, label]), each with a
 * comment naming the other as its mirror. They were kept in sync by hand across
 * 44 entries; nothing enforced it, so adding a symbol to one silently made the
 * two pages disagree about what a station IS. The pair-valued shape won because
 * it is the strict superset — every index label was already element [1] of the
 * map's pair.
 *
 * Callers keep their own thin wrapper (aprsTypeLabel / aprsCategory) because the
 * pages differ in what they layer ON TOP of the lookup: the map lets configured
 * natural hazards override the symbol, the dashboard does not. That policy stays
 * page-local; only the table and the plain lookup are shared.
 *
 * Classic <script> served by Flask (loaded by the map + dashboard); also
 * requireable by node --test (no root package.json -> .js is CommonJS).
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisAprsTypes = api;
  root.aprsTypeTable = api.TABLE;
  root.aprsCategoryOf = api.categoryOf;
  root.aprsLabelOf = api.labelOf;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  // sym_code -> [categoryKey, label]. Labels are singular so the dashboard's
  // Type column and the map's legend name a station identically.
  var TABLE = {
    // Land vehicles
    '>': ['car', 'Car'], 'j': ['car', 'Car'], 'v': ['car', 'Car'],
    '<': ['motorcycle', 'Motorcycle'],
    'k': ['truck', 'Truck'], 'u': ['truck', 'Truck'],
    'R': ['rv', 'RV'],
    'U': ['bus', 'Bus'],
    '=': ['train', 'Train'],
    'b': ['bicycle', 'Bicycle'],
    // Air
    "'": ['aircraft', 'Aircraft'], '^': ['aircraft', 'Aircraft'], 'g': ['aircraft', 'Aircraft'],
    'X': ['heli', 'Helicopter'], 'O': ['balloon', 'Balloon'],
    // Marine
    's': ['marine', 'Boat'], 'Y': ['marine', 'Boat'], 'C': ['marine', 'Boat'],
    // People
    '[': ['pedestrian', 'Person'], 'p': ['pedestrian', 'Person'],
    // Weather
    '_': ['wx', 'Weather'], 'W': ['wx', 'Weather'],
    // RF infrastructure
    '#': ['digi', 'Digipeater'], '&': ['igate', 'Gateway'],
    'r': ['antenna', 'Antenna'], 'y': ['antenna', 'Antenna'], '`': ['antenna', 'Antenna'],
    // Fixed locations
    '-': ['home', 'Home'],
    // Public safety / emergency
    '!': ['safety', 'Public safety'], 'P': ['safety', 'Public safety'], 'a': ['safety', 'Public safety'],
    'f': ['safety', 'Public safety'], 'd': ['safety', 'Public safety'], 'c': ['safety', 'Public safety'],
    'A': ['safety', 'Public safety'], 'h': ['safety', 'Public safety'], 'o': ['safety', 'Public safety'],
    '+': ['safety', 'Public safety'],
    // Services / places
    '?': ['services', 'Services'], '$': ['services', 'Services'],
    ';': ['services', 'Services'], 'B': ['services', 'Services'],
    'K': ['services', 'Services'],
    // Emergency / incidents — the primary-table Fire symbol ('/' + ':') is used
    // by wildfire-incident objects gated into APRS-IS.
    ':': ['fire', 'Fire']
  };

  var OTHER = { key: 'other', label: 'Other' };

  // Weather stations use '_' on BOTH the primary and alternate symbol tables, so
  // it is special-cased ahead of the lookup rather than trusted to sym_table.
  function categoryOf(s) {
    if (s && s.sym_code === '_') return { key: 'wx', label: 'Weather' };
    var hit = s ? TABLE[s.sym_code] : null;
    return hit ? { key: hit[0], label: hit[1] } : { key: OTHER.key, label: OTHER.label };
  }

  function labelOf(s) {
    return categoryOf(s).label;
  }

  return { TABLE: TABLE, categoryOf: categoryOf, labelOf: labelOf };
});
