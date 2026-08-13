/*
 * form-time.js — one time format for every OASIS form.
 *
 * The five form pages each hand-rolled getHours()/getMinutes() and drifted:
 * ICS-205 wrote "1245", ICS-213/214/309 wrote "1245L", and the net log wrote
 * "1805Z" — the same clock, three spellings, and one of them a different
 * timezone. Worse, the net log handed its UTC values straight into ICS-309
 * fields whose own auto-fill was local, so one form could hold both zones with
 * a single letter telling them apart.
 *
 * The canonical format for ICS forms is 24-hour local with an explicit zone
 * marker: HHMM + 'L'. The net log keeps UTC for its own display, because that
 * is how nets are logged — but anything crossing into an ICS form is converted
 * first, by toLocalHHMM() below, so a form is never half in one zone.
 *
 * Classic <script src>. Every function here is pure and requireable by
 * node --test — pass a Date in, get a string out.
 */
(function (root, factory) {
  var api = factory();
  root.OasisFormTime = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var ZONE = 'L';           // local; the net log's own display uses 'Z'
  function _p(n) { return String(n).padStart(2, '0'); }
  function _d(d) { return d instanceof Date ? d : new Date(); }

  /** 24-hour local clock, no zone marker: "1245". */
  function hhmm(d) {
    d = _d(d);
    return _p(d.getHours()) + _p(d.getMinutes());
  }

  /** Canonical ICS time: "1245L". */
  function hhmmL(d) {
    return hhmm(d) + ZONE;
  }

  /** UTC clock for the net log's own display: "1805Z". */
  function hhmmZ(d) {
    d = _d(d);
    return _p(d.getUTCHours()) + _p(d.getUTCMinutes()) + 'Z';
  }

  /** Canonical ICS date/time: "20260813 1245L". */
  function stamp(d) {
    d = _d(d);
    return '' + d.getFullYear() + _p(d.getMonth() + 1) + _p(d.getDate()) +
      ' ' + hhmmL(d);
  }

  /** Date for a native <input type="date">: "2026-08-13". */
  function dateISO(d) {
    d = _d(d);
    return d.getFullYear() + '-' + _p(d.getMonth() + 1) + '-' + _p(d.getDate());
  }

  /**
   * An ISO/UTC timestamp -> canonical local ICS time. This is the net log ->
   * ICS-309 boundary: check-ins are stored UTC, but an ICS form is local, so
   * they are converted rather than relabelled. Returns '' for junk input so a
   * bad row can't write "NaNL" into a form.
   */
  function toLocalHHMM(iso) {
    // Guard falsy input explicitly: new Date(null) is epoch 0, not an invalid
    // date, so a check-in with no timestamp would otherwise write the local
    // spelling of 1970-01-01T00:00Z into an ICS form.
    if (iso === null || iso === undefined || iso === '') return '';
    var d = new Date(iso);
    return isNaN(d.getTime()) ? '' : hhmmL(d);
  }

  return {
    ZONE: ZONE,
    hhmm: hhmm, hhmmL: hhmmL, hhmmZ: hhmmZ,
    stamp: stamp, dateISO: dateISO, toLocalHHMM: toLocalHHMM,
  };
});
