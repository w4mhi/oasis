/*
 * format.js — shared time/age formatting for OASIS dashboards.
 *
 * fmtAge returns { text, cls } so callers can colour-code staleness — the main
 * dashboard uses the class; the 7" kiosk uses only .text. Classic <script> in
 * both dashboards; requireable by node --test.
 */
(function (root, factory) {
  var api = factory();
  root.OasisFormat = api;
  root.fmtAge = api.fmtAge;
  root.fmtLastHeard = api.fmtLastHeard;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function fmtAge(iso) {
    if (!iso) return { text: '—', cls: '' };
    var sec = Math.floor((Date.now() - new Date(iso)) / 1000);
    if (sec < 0) return { text: 'just now', cls: 'age-ok' };
    if (sec < 3600) {
      var m = Math.floor(sec / 60);
      return { text: m + 'm ago', cls: m < 30 ? 'age-ok' : 'age-warn' };
    }
    var h = Math.floor(sec / 3600);
    return { text: h + 'h ago', cls: h < 6 ? 'age-warn' : 'age-old' };
  }
  function fmtLastHeard(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    var utc = d.toISOString().replace('T', ' ').slice(0, 16) + 'Z';
    var z = function (v) { return String(v).padStart(2, '0'); };
    var local = z(d.getHours()) + ':' + z(d.getMinutes());
    return utc + ' <span style="color:var(--text-dim)">[' + local + ']</span>';
  }
  return { fmtAge: fmtAge, fmtLastHeard: fmtLastHeard };
});
