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

  // Label sets are the roster's closed vocabulary (see satnogs.LABELS).
  var CHANNELS = [
    { key: 'voice',   label: 'Voice',       rgb: [0, 200, 90],
      labels: ['VOICE', 'FM', 'LINEAR', 'SSB'],
      title: 'Voice — FM / linear / SSB' },
    { key: 'imaging', label: 'Imaging',     rgb: [0, 90, 230],
      labels: ['WEATHER', 'SSTV'],
      title: 'Imaging — weather APT/LRPT or SSTV' },
    { key: 'data',    label: 'Data / APRS', rgb: [230, 100, 0],
      labels: ['APRS'],
      title: 'Data — APRS packet' },
  ];
  // Telemetry-only birds borrow the Data colour, as on the map.
  var TELEMETRY = { key: 'telemetry', label: 'Telemetry only', rgb: [230, 100, 0],
                    title: 'Telemetry beacon only — nothing to work' };

  // ── Row chips: the roster's OWN labels, abbreviated ────────────────────────
  // The kiosk shows the same vocabulary as the Satellites page's roster cards
  // rather than a reduced icon set. Collapsing nine labels into three channels
  // reads tidily but throws away the distinction an operator actually acts on:
  // FM and LINEAR are both "voice", and they need different radios.
  //
  // Shortened only where the full word does not fit a 7" row.
  var ABBREV = { WEATHER: 'WX', LINEAR: 'LIN', CREWED: 'CREW' };
  // Pairs the aggregator always emits TOGETHER, so showing both is duplication,
  // not detail. VOICE is produced only by FM/FMN/NFM, and LINEAR+SSB come as a
  // set from an SSB mode or a Transponder type (see satnogs._tx_labels). The
  // page prints both because it has the width; a kiosk row does not.
  var IMPLIED_BY = { VOICE: 'FM', SSB: 'LINEAR' };
  // Which channel colours a given label. CREWED is deliberately absent — it is
  // a fact about the bird, not a capability, so it stays neutral instead of
  // claiming a channel it cannot be worked on.
  function _colorFor(label) {
    for (var i = 0; i < CHANNELS.length; i++) {
      if (CHANNELS[i].labels.indexOf(label) !== -1) return rgbCss(CHANNELS[i].rgb);
    }
    if (label === 'DATA') return rgbCss(TELEMETRY.rgb);
    return null;                       // neutral — the page's own chip colour
  }
  var CHIP_ORDER = ['WEATHER', 'VOICE', 'FM', 'APRS', 'SSTV', 'LINEAR', 'SSB',
                    'DATA', 'CREWED'];   // == satnogs.LABELS, the stable order

  // labels[] -> [{label, text, color}] for the row, deduped and abbreviated.
  function oasisSatLabelChips(labels) {
    var ls = (labels || []).map(function (l) { return String(l).toUpperCase(); });
    var out = [];
    for (var i = 0; i < CHIP_ORDER.length; i++) {
      var l = CHIP_ORDER[i];
      if (ls.indexOf(l) === -1) continue;
      // Drop the half of a redundant pair whose partner is also present.
      if (IMPLIED_BY[l] && ls.indexOf(IMPLIED_BY[l]) !== -1) continue;
      out.push({ label: l, text: ABBREV[l] || l, color: _colorFor(l) });
    }
    return out;
  }

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

  // labels[] -> the row's chip markup. Colours are emitted INLINE from the
  // definitions above rather than as a CSS class per label, so they have exactly
  // one definition — a stylesheet copy is how the map and the kiosk would start
  // disagreeing about which green means voice.
  function oasisSatCapabilityChips(labels) {
    return oasisSatLabelChips(labels).map(function (c) {
      var style = c.color ? ' style="color:' + c.color + ';border-color:' + c.color + '"' : '';
      return '<span class="cap" title="' + c.label + '"' + style + '>' + c.text + '</span>';
    }).join('');
  }

  root.OASIS_SAT_CHANNELS = CHANNELS;
  root.OASIS_SAT_TELEMETRY = TELEMETRY;
  root.oasisSatRgbCss = rgbCss;
  root.oasisSatChannels = oasisSatChannels;
  root.oasisSatCapabilityColor = oasisSatCapabilityColor;
  root.oasisSatLabelChips = oasisSatLabelChips;
  root.oasisSatCapabilityChips = oasisSatCapabilityChips;
})(typeof window !== 'undefined' ? window : this);
