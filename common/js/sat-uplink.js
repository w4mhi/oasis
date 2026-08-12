/*
 * sat-uplink.js — the FUP cells of the satellite detail popup's frequency table.
 *
 * 87 of 807 card-visible transmitters carry an uplink, and every one of them is
 * a Transponder or Transceiver. An uplink is the signal that a bird can be
 * worked two ways, which is why the other 720 entries render nothing here and
 * the enriched popup does not become the crowded card again.
 *
 * The rule this exists to enforce is ATTRIBUTION. Card entries are grouped by
 * (frequency, demod), so the ISS's 437.800 is one entry holding FM, SSTV and
 * FSK — and only the FM member can be transmitted to. A line reading
 * "437.800 FM · SSTV · FSK  ↑ 145.990" would tell an operator they can transmit
 * to the SSTV service. Naming the mode is the whole point.
 *
 * Loaded as a classic <script>, and requireable by node --test.
 *
 * Same UMD shape as sat-look.js: an outer (root, factory) IIFE, a bare global
 * alongside the namespaced export, a module.exports guard. It is not a byte-
 * for-byte match — sat-look.js's factory takes `root` because it reads
 * root.satellite (the vendored SGP4 propagator); this formatter is pure and
 * needs no root access, so its factory takes nothing and the call below
 * passes nothing.
 */
(function (root, factory) {
  var api = factory();
  root.OasisSatUplink = api;
  // Bare global so the page's inline call sites read naturally.
  root.uplinkCells = api.uplinkCells;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /* An en dash for the passband, matching the pass-window range on the same
     card. BMP, so the Pi renders it. */
  function freqText(u) {
    return u.freq_high_mhz != null
      ? u.freq_mhz + '–' + u.freq_high_mhz
      : String(u.freq_mhz);
  }

  /* The FUP cell of the popup's frequency table: the uplink frequency plus only
     the qualifiers the table cannot show by itself.

     No arrow, and no "simplex": the column header says FUP, and an uplink equal
     to the downlink is visible as FDN and FUP carrying the same number. Both
     were carried in the old free-text line, where nothing else supplied them.

     What the table CANNOT show, and this therefore must, is the mode a shared
     entry's uplink belongs to — see the attribution note below. */
  function uplinkCells(entry) {
    var ups = (entry && entry.uplinks) || [];
    var modes = (entry && entry.modes) || [];
    var multi = modes.length > 1;
    return ups.map(function (u) {
      var parts = [];
      // Name the mode ONLY when the entry holds more than one AND this
      // uplink says which one it is. That second condition can fail —
      // `u.mode` missing, or `multi` false because `modes` was itself
      // missing or empty — and when it does we render unattributed rather
      // than guess. This is a deliberate call, not a side effect of `&&`
      // short-circuiting: roster.group_downlinks on the server always
      // populates both `modes` and every uplink's own `mode`, so a
      // multi-mode entry with an unlabelled uplink cannot come from live
      // OASIS data — it only happens on a stale cached page. Inventing a
      // placeholder label for that case would put words on screen that
      // describe nothing, which is worse than a bare frequency, and a stale
      // page's data is stale in every other respect too, so that staleness
      // is the safety net.
      var attributable = multi && !!u.mode;
      if (attributable) parts.push(u.mode);
      if (u.invert) parts.push('inverting');
      if (u.ctcss_hz != null) parts.push('CTCSS ' + u.ctcss_hz.toFixed(1));
      var tail = parts.length ? ' ' + parts.join(' · ') : '';
      return freqText(u) + tail;
    });
  }

  return { uplinkCells: uplinkCells, freqText: freqText };
});
