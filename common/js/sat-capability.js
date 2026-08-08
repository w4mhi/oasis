// sat-capability.js
// -----------------------------------------------------------------------------
// What a satellite can DO, derived from its roster labels — shared by the
// Satellites map (which paints one blended dot per bird) and the kiosk list
// (which has room to show the channels separately).
//
// Three channels, one per real capability:
//
//   VOICE    green   FM / LINEAR / SSB — you can work it with a mic
//   IMAGING  blue    WEATHER / SSTV    — you can decode a picture
//   DATA     orange  APRS              — you can pass packet through it
//
// The MAP mixes them additively into a single colour, because a dot is all the
// room it has: Voice+Imaging = cyan, Voice+APRS = yellow, Imaging+APRS =
// magenta, all three = near-white, so a multi-capability bird (ISS and the other
// crewed stations) lights up on its own. That blend is a compromise forced by
// having one dot per bird — a LIST row does not have that constraint, so the
// kiosk renders the channels as separate glyphs and keeps the blend only for its
// edge rail.
//
// A bird that only beacons telemetry has no real channel and falls back to the
// Data orange. It never BLENDS: bare telemetry is on roughly 90 of 130 birds and
// would warm every colour on the map into meaninglessness. It only colours a
// bird that does nothing else.
//
// Both renderings read these definitions, so the two screens cannot disagree
// about what a bird does or what colour says so.
(function (root) {
  'use strict';

  // Inline SVG, never emoji — the Pi has no emoji font and would render tofu.
  // Chosen for SILHOUETTE: these are read at a couple of metres on a 7" panel,
  // so what matters is that mic / camera / packet are distinguishable in
  // outline, before any detail or colour resolves.
  var MIC = 'M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V22h2v-4.08A7 7 0 0 0 19 11h-2Z';
  var CAM = 'M9 3 7.17 5H4a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3.17L15 3H9Zm3 5a5 5 0 1 1 0 10 5 5 0 0 1 0-10Zm0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z';
  var PKT = 'M6 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4V6a2 2 0 0 1 2-2Zm2 4h10v2H8V8Zm0 4h7v2H8v-2Z';
  var TLM = 'M12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12Zm0 2.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Z';

  // Label sets are the roster's closed vocabulary (see satnogs.LABELS).
  var CHANNELS = [
    { key: 'voice',   label: 'Voice',       rgb: [0, 200, 90],
      labels: ['VOICE', 'FM', 'LINEAR', 'SSB'], glyph: MIC,
      title: 'Voice — FM / linear / SSB' },
    { key: 'imaging', label: 'Imaging',     rgb: [0, 90, 230],
      labels: ['WEATHER', 'SSTV'], glyph: CAM,
      title: 'Imaging — weather APT/LRPT or SSTV' },
    { key: 'data',    label: 'Data / APRS', rgb: [230, 100, 0],
      labels: ['APRS'], glyph: PKT,
      title: 'Data — APRS packet' },
  ];
  // Telemetry-only birds borrow the Data colour (as on the map) but a distinct
  // ring glyph, so "beacons only" never looks like "passes packet".
  var TELEMETRY = { key: 'telemetry', label: 'Telemetry only', rgb: [230, 100, 0],
                    glyph: TLM, title: 'Telemetry beacon only — nothing to work' };

  function rgbCss(rgb) { return 'rgb(' + rgb[0] + ' ' + rgb[1] + ' ' + rgb[2] + ')'; }

  function _has(labels, wanted) {
    for (var i = 0; i < wanted.length; i++) {
      if (labels.indexOf(wanted[i]) !== -1) return true;
    }
    return false;
  }

  // labels[] -> the channels this bird actually has, in CHANNELS order. A bird
  // with none gets the single telemetry pseudo-channel, so callers never have to
  // special-case an empty list.
  function oasisSatChannels(labels) {
    var have = [];
    var ls = (labels || []).map(function (l) { return String(l).toUpperCase(); });
    for (var i = 0; i < CHANNELS.length; i++) {
      if (_has(ls, CHANNELS[i].labels)) have.push(CHANNELS[i]);
    }
    return have.length ? have : [TELEMETRY];
  }

  // labels[] -> the map's blended colour. Telemetry-only never blends.
  function oasisSatCapabilityColor(labels) {
    var have = oasisSatChannels(labels);
    if (have.length === 1 && have[0].key === 'telemetry') return rgbCss(TELEMETRY.rgb);
    var mixed = [0, 0, 0];
    for (var i = 0; i < have.length; i++) {
      for (var k = 0; k < 3; k++) mixed[k] = Math.min(255, mixed[k] + have[i].rgb[k]);
    }
    return rgbCss(mixed);
  }

  // labels[] -> one <span> per channel, each carrying its own colour inline.
  // Inline rather than a CSS class per channel so the colours have exactly ONE
  // definition (above) — a stylesheet copy is how the map and the kiosk would
  // start disagreeing about which green means voice.
  function oasisSatCapabilityGlyphs(labels) {
    return oasisSatChannels(labels).map(function (c) {
      return '<span class="cap cap-' + c.key + '" title="' + c.title + '"'
        + ' style="color:' + rgbCss(c.rgb) + '">'
        + '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
        + '<path d="' + c.glyph + '"/></svg></span>';
    }).join('');
  }

  root.OASIS_SAT_CHANNELS = CHANNELS;
  root.OASIS_SAT_TELEMETRY = TELEMETRY;
  root.oasisSatRgbCss = rgbCss;
  root.oasisSatChannels = oasisSatChannels;
  root.oasisSatCapabilityColor = oasisSatCapabilityColor;
  root.oasisSatCapabilityGlyphs = oasisSatCapabilityGlyphs;
})(typeof window !== 'undefined' ? window : this);
