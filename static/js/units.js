/*
 * units.js — shared measurement-unit formatting for OASIS dashboards.
 *
 * Loaded as a classic <script> in index.html and small-screen/index7.html.
 * Owns the imperial/metric preference (localStorage 'oasis_units', default
 * imperial) so both dashboards agree, and exposes its formatters as bare
 * globals for the pages' existing inline call sites. Also requireable by
 * node --test (no root package.json -> .js is CommonJS), so the pure
 * formatters are unit-tested in tests/js/. The server reports Celsius / mph /
 * metres; colour thresholds stay in Celsius — only the displayed text changes.
 */
(function (root, factory) {
  var api = factory();
  root.OasisUnits = api;
  // Bare globals so index.html / index7.html call sites keep working unchanged.
  root.isImperial = api.isImperial;
  root.fmtTemp = api.fmtTemp;
  root.fmtAlt = api.fmtAlt;
  root.fmtSpeed = api.fmtSpeed;
  root.fmtUptime = api.fmtUptime;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  var LS = (typeof localStorage !== 'undefined') ? localStorage : null;
  var _units = (LS && LS.getItem('oasis_units')) || 'imperial';

  function isImperial() { return _units === 'imperial'; }
  function setUnits(u) {
    _units = (u === 'metric') ? 'metric' : 'imperial';
    if (LS) { try { LS.setItem('oasis_units', _units); } catch (e) {} }
  }
  function toggleUnits() { setUnits(isImperial() ? 'metric' : 'imperial'); }

  function fmtTemp(c) {
    if (c == null) return 'n/a';
    return isImperial() ? (Math.round(c * 9 / 5 + 32) + ' °F') : (c + ' °C');
  }
  function fmtAlt(m) {
    if (m == null) return 'n/a';
    return isImperial()
      ? (Math.round(m * 3.28084).toLocaleString() + ' ft')
      : (Math.round(m).toLocaleString() + ' m');
  }
  function fmtSpeed(mph) {
    if (mph == null) return '';
    return isImperial() ? (mph + ' mph') : (Math.round(mph * 1.60934) + ' km/h');
  }
  function fmtUptime(sec) {
    var d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
    if (d) return d + 'd ' + h + 'h';
    if (h) return h + 'h ' + m + 'm';
    return m + 'm';
  }
  return { isImperial: isImperial, setUnits: setUnits, toggleUnits: toggleUnits,
           fmtTemp: fmtTemp, fmtAlt: fmtAlt, fmtSpeed: fmtSpeed, fmtUptime: fmtUptime };
});
