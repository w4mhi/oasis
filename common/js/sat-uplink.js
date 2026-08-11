/*
 * sat-uplink.js — the uplink display lines on the satellite detail popup.
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
 */
(function (root, factory) {
  var api = factory(root);
  root.OasisSatUplink = api;
  // Bare global so the page's inline call sites read naturally.
  root.uplinkLines = api.uplinkLines;
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

  /* Uplink == downlink is a digipeater. Saying "simplex" is shorter than the
     two identical legs it would otherwise look like, and it is what an operator
     calls it. */
  function uplinkLines(entry) {
    var ups = (entry && entry.uplinks) || [];
    var multi = ((entry && entry.modes) || []).length > 1;
    return ups.map(function (u) {
      var parts = [];
      // Name the mode ONLY when the entry holds more than one: a single-mode
      // entry naming its own mode again is noise.
      if (multi && u.mode) parts.push(u.mode);
      if (u.simplex) parts.push('simplex');
      if (u.invert) parts.push('inverting');
      if (u.ctcss_hz != null) parts.push('CTCSS ' + u.ctcss_hz.toFixed(1));
      var arrow = u.simplex ? '↑↓' : '↑';
      var tail = parts.length ? ' ' + parts.join(' · ') : '';
      return arrow + ' ' + freqText(u) + tail;
    });
  }

  return { uplinkLines: uplinkLines, freqText: freqText };
});
