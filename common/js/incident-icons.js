/*
 * incident-icons.js — inline-SVG glyphs for hazards and operator-placed warnings.
 *
 * Two catalogs carried emoji: configuration/hazards.json (auto-detected APRS
 * hazard categories, rendered by index.html, oasis-dashboard/dashboard.html and
 * maps/traffic/map.html) and maps/traffic/warnings.json (the operator-placed
 * warning palette, whose icon IS the map marker). Raspberry Pi OS ships no emoji
 * font, so on the kiosk — the screen an operator actually watches during an
 * incident — every hazard pill and every placed emergency marker drew a tofu box.
 * Colour still carries the category; the glyph is now a stroked SVG in
 * currentColor, which renders anywhere.
 *
 * Keyed by the catalog entry's `key`/`id`, so an operator-added regional hazard
 * or warning needs no new art: unknown keys get the warning triangle. That also
 * keeps both JSON files about what they are really for — matching APRS symbols
 * to categories.
 *
 * Classic <script src="/common/js/incident-icons.js">; also requireable by
 * node --test (pure string in, string out).
 */
(function (root, factory) {
  var api = factory();
  root.OasisIncidentIcons = api;
  root.incidentIconSvg = api.incidentIconSvg;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // 16x16 viewBox, stroked — no fills, so one glyph reads on light and dark.
  var PATHS = {
    fire:      '<path d="M8 14.5c2.5 0 4.3-1.7 4.3-4 0-3.2-3.3-4.4-2.6-8C7.4 3.4 5.6 5.7 5.6 8c0 1 .4 1.7.4 2.3 0 .8-.6 1.2-1.1 1.2-.6 0-1-.5-1.1-1.1-.7 1-.1 4.1 4.2 4.1z"/>',
    hurricane: '<circle cx="8" cy="8" r="1.6"/><path d="M8 6.4c0-2.6 1-4.4 3.4-4.4 1.6 0 2.6 1 2.6 2.4 0 2.3-2.6 3.6-6 3.6"/><path d="M8 9.6c0 2.6-1 4.4-3.4 4.4-1.6 0-2.6-1-2.6-2.4 0-2.3 2.6-3.6 6-3.6"/>',
    tornado:   '<path d="M2.5 2.5h11M3.5 5.5h9M5 8.5h6M6.5 11.5h3M7.5 14.5h1"/>',
    flood:     '<path d="M1.5 9.5c1.6 0 1.6 1.4 3.2 1.4s1.6-1.4 3.3-1.4 1.6 1.4 3.3 1.4 1.6-1.4 3.2-1.4"/><path d="M1.5 13c1.6 0 1.6 1.4 3.2 1.4s1.6-1.4 3.3-1.4 1.6 1.4 3.3 1.4 1.6-1.4 3.2-1.4"/><path d="M4.5 6.5l3.5-5 3.5 5"/>',
    smoke:     '<path d="M3 11.5h10M4.5 8.5h8"/><path d="M6 5.5c0-1.7 1.3-3 3-3M9.5 5.5c0-1.1.9-2 2-2"/>',
    skywarn:   '<path d="M2.5 7.5a3 3 0 013-3 4 4 0 017.5 1 2.75 2.75 0 01-.5 5.4H5.5a3 3 0 01-3-2.9z"/><path d="M8.5 10l-2 4h3l-2 2"/>',
    emergency: '<circle cx="8" cy="8" r="6"/><path d="M8 4.5v4.5M8 11.2v.3"/>',

    // ── Operator-placed warning palette (maps/traffic/warnings.json) ──────────
    animal_shelter: '<circle cx="4.6" cy="6.4" r="1.5"/><circle cx="8" cy="4.8" r="1.5"/><circle cx="11.4" cy="6.4" r="1.5"/><path d="M8 8c2 0 3.5 1.6 3.5 3.2S10 14 8 14s-3.5-1.2-3.5-2.8S6 8 8 8z"/>',
    eoc:            '<path d="M2 7.5L8 2.5l6 5"/><path d="M3.5 7v6.5h9V7"/><path d="M6.5 13.5v-4h3v4"/>',
    first_aid:      '<rect x="2" y="4" width="12" height="9" rx="1.5"/><path d="M8 6.5v4M6 8.5h4"/>',
    hazmat:         '<circle cx="8" cy="8" r="1.6"/><path d="M8 6.4L6.2 3.3a5.5 5.5 0 013.6 0z"/><path d="M9.2 9.2l3.3.6a5.5 5.5 0 01-1.8 3.1z"/><path d="M6.8 9.2l-1.5 3.7a5.5 5.5 0 01-1.8-3.1z"/>',
    wind:           '<path d="M2 6h7a2 2 0 10-2-2"/><path d="M2 9.5h9a2 2 0 11-2 2"/><path d="M2 13h5"/>',
    ice:            '<path d="M8 2v12M2.8 5l10.4 6M13.2 5L2.8 11"/><path d="M6.4 3.6L8 2l1.6 1.6M6.4 12.4L8 14l1.6-1.6"/>',
    medical:        '<path d="M1.5 8.5h3l1.5-3 2.5 6 1.5-3h4.5"/>',
    power:          '<path d="M9 1.5L4 9h3.5L7 14.5 12 7H8.5z"/>',
    roadblock:      '<rect x="1.5" y="5.5" width="13" height="5" rx="1"/><path d="M4 5.5L7 10.5M8 5.5l3 5"/><path d="M3 10.5v3M13 10.5v3"/>',
    shelter:        '<path d="M8 2L1.5 13.5h13z"/><path d="M8 6.5l3.5 7h-7z"/>',
    storm:          '<path d="M3 9.5a2.9 2.9 0 01.4-5.8 4 4 0 017.5.8 2.6 2.6 0 01-.4 5.1"/><path d="M8.5 8l-2 3.5h2.5L7 15"/>',
    water:          '<path d="M8 2.5s4 4.6 4 7.2a4 4 0 11-8 0C4 7.1 8 2.5 8 2.5z"/>',
    // Fallback for received NWS/SAME alerts that do not map to a more specific
    // category (earthquake, avalanche, volcano, landslide, and anything we do
    // not recognise) — an alert bell, distinct from storm/wind/ice/hazmat.
    weather:        '<path d="M8 2.2v1.6"/><path d="M4 11.3c0-1.3.6-1.9 1.1-2.7c.4-.6.6-1.3.6-2.1c0-1.5 1-2.7 2.3-2.7c1.3 0 2.3 1.2 2.3 2.7c0 .8.2 1.5.6 2.1c.5.8 1.1 1.4 1.1 2.7"/><path d="M4 11.3h8"/><path d="M6.6 12.9a1.4 1.4 0 002.8 0"/>',
  };

  var FALLBACK = '<path d="M8 2l6 11H2z"/><path d="M8 6v4M8 11.8v.2"/>';

  function incidentIconSvg(key, opts) {
    opts = opts || {};
    var size = opts.size || '1em';
    var body = PATHS[key] || FALLBACK;
    return '<svg viewBox="0 0 16 16" width="' + size + '" height="' + size + '" ' +
      'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true" focusable="false">' + body + '</svg>';
  }

  function hasIcon(key) {
    return Object.prototype.hasOwnProperty.call(PATHS, key);
  }

  return { incidentIconSvg: incidentIconSvg, hasIcon: hasIcon, KEYS: Object.keys(PATHS) };
});
