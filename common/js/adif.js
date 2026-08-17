/*
 * adif.js — ADIF 3.1.4 (ADI) output for the net logger.
 *
 * The net log already exports CSV and hands off to ICS-309, but both of those
 * are emergency-management artifacts. A net control operator who wants the
 * night's check-ins in a station log had to retype them. ADIF is the format
 * every logging program reads, so one button turns a net into a logbook import.
 *
 * ADI is a tag-value format: <FIELDNAME:LENGTH>value, records terminated by
 * <EOR>, a header terminated by <EOH>. Two rules bite:
 *
 *   1. LENGTH is the value's BYTE count, not its character count. A name with
 *      an accent in it is one character and two bytes, and getting that wrong
 *      does not corrupt one field — the reader takes the declared number of
 *      bytes and starts parsing the next tag mid-value, so every field after it
 *      shifts. field() measures with TextEncoder for exactly this reason.
 *
 *   2. An empty field is written as nothing at all, not as <NAME:0>. Some
 *      readers treat a zero-length field as a present-but-blank value and
 *      overwrite good data in an existing logbook entry with it.
 *
 * Classic <script src>, no DOM and no fetch, so every function here is pure and
 * requireable by node --test.
 */
(function (root, factory) {
  var api = factory();
  root.OasisADIF = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var ADIF_VER = '3.1.4';

  // ADIF Band enumeration, low edge to high edge in MHz, inclusive. Stops at
  // 3cm: everything above it is enumerated by ADIF but no net runs there, and a
  // table nobody exercises is a table nobody notices has gone wrong.
  var BANDS = [
    ['2190m', 0.1357, 0.1378], ['630m', 0.472, 0.479], ['560m', 0.501, 0.504],
    ['160m', 1.8, 2.0], ['80m', 3.5, 4.0], ['60m', 5.06, 5.45],
    ['40m', 7.0, 7.3], ['30m', 10.1, 10.15], ['20m', 14.0, 14.35],
    ['17m', 18.068, 18.168], ['15m', 21.0, 21.45], ['12m', 24.89, 24.99],
    ['10m', 28.0, 29.7], ['8m', 40.0, 45.0], ['6m', 50.0, 54.0],
    ['5m', 54.000001, 69.9], ['4m', 70.0, 71.0], ['2m', 144.0, 148.0],
    ['1.25m', 222.0, 225.0], ['70cm', 420.0, 450.0], ['33cm', 902.0, 928.0],
    ['23cm', 1240.0, 1300.0], ['13cm', 2300.0, 2450.0], ['9cm', 3300.0, 3500.0],
    ['6cm', 5650.0, 5925.0], ['3cm', 10000.0, 10500.0],
  ];

  function _pad(n) { return String(n).padStart(2, '0'); }

  function _str(v) { return v === null || v === undefined ? '' : String(v); }

  /** UTF-8 byte length. TextEncoder exists in every browser we ship to and in node. */
  function byteLen(s) {
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(s).length;
    /* istanbul ignore next — belt and braces for an exotic runtime */
    return unescape(encodeURIComponent(s)).length;
  }

  /**
   * The frequency box is free text — "146.520 MHz", "146.94 (W4XYZ repeater)",
   * "14.325". Take the first number and trust the operator meant MHz; a net on
   * "146520" kHz is not a thing anyone types. Returns null when there is no
   * number to find, which is how the caller knows to omit FREQ and BAND rather
   * than write a guess.
   */
  function parseFreqMHz(text) {
    var m = /(\d+(?:\.\d+)?)/.exec(_str(text));
    if (!m) return null;
    var f = parseFloat(m[1]);
    return isNaN(f) ? null : f;
  }

  /** ADIF band name for a frequency in MHz, or '' if it is in no amateur band. */
  function bandFor(mhz) {
    if (typeof mhz !== 'number' || isNaN(mhz)) return '';
    for (var i = 0; i < BANDS.length; i++) {
      if (mhz >= BANDS[i][1] && mhz <= BANDS[i][2]) return BANDS[i][0];
    }
    return '';
  }

  /**
   * Prefill only. HF nets are SSB and VHF/UHF nets are FM often enough to be a
   * good default, and wrong often enough — HF FM, 10m FM, anything digital —
   * that the page keeps the field editable and stops auto-filling the moment
   * the operator touches it.
   */
  function modeFor(mhz) {
    if (typeof mhz !== 'number' || isNaN(mhz)) return '';
    return mhz < 30 ? 'SSB' : 'FM';
  }

  /**
   * The date box defaults to "2026-08-17 UTC" but is free text. Accept the
   * ISO-ish spellings and a bare YYYYMMDD; anything else returns '' and the
   * caller falls back to today, which for a net log being exported the same
   * evening is the right answer anyway.
   */
  function parseDate(text) {
    var s = _str(text).trim();
    var m = /(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/.exec(s) ||
            /\b(\d{4})(\d{2})(\d{2})\b/.exec(s);
    if (!m) return '';
    var y = +m[1], mo = +m[2], d = +m[3];
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return '';
    return '' + y + _pad(mo) + _pad(d);
  }

  /**
   * Check-in times are stored "1432Z" but the inline editor lets an operator
   * type anything. Strip to digits and accept HHMM or HHMMSS, the two shapes
   * ADIF's TIME_ON allows. Returns '' rather than a malformed time.
   */
  function parseTime(utc) {
    var digits = _str(utc).replace(/\D/g, '');
    if (digits.length !== 4 && digits.length !== 6) return '';
    var h = +digits.slice(0, 2), mi = +digits.slice(2, 4);
    var s = digits.length === 6 ? +digits.slice(4, 6) : 0;
    if (h > 23 || mi > 59 || s > 59) return '';
    return digits;
  }

  /**
   * One ADI field. Empty values produce '' — no tag at all — see the header
   * comment on why <NAME:0> is worse than silence. Newlines are stripped so a
   * pasted multi-line note cannot make the file unreadable to line-oriented
   * parsers, which plenty of real logging software still uses.
   */
  function field(name, value) {
    var v = _str(value).replace(/[\r\n]+/g, ' ').trim();
    if (!v) return '';
    return '<' + String(name).toUpperCase() + ':' + byteLen(v) + '>' + v;
  }

  /** UTC "YYYYMMDD" — the fallback when the date box cannot be parsed. */
  function todayUTC(d) {
    d = d instanceof Date ? d : new Date();
    return '' + d.getUTCFullYear() + _pad(d.getUTCMonth() + 1) + _pad(d.getUTCDate());
  }

  /** UTC "YYYYMMDD HHMMSS" for the header's CREATED_TIMESTAMP. */
  function timestamp(d) {
    d = d instanceof Date ? d : new Date();
    return todayUTC(d) + ' ' +
      _pad(d.getUTCHours()) + _pad(d.getUTCMinutes()) + _pad(d.getUTCSeconds());
  }

  /**
   * One check-in -> one QSO record, or '' if the row has no callsign. A record
   * without CALL is not a QSO, and shipping one makes the whole file suspect to
   * a strict importer.
   */
  function record(row, ctx) {
    row = row || {};
    ctx = ctx || {};
    var call = _str(row.call).trim().toUpperCase();
    if (!call) return '';

    var notes = _str(row.notes).trim();
    if (row.traffic) notes = notes ? notes + ' (traffic)' : 'traffic';

    var out = [
      field('CALL', call),
      field('QSO_DATE', ctx.date),
      field('TIME_ON', parseTime(row.utc)),
      field('MODE', ctx.mode),
      field('FREQ', ctx.freq),
      field('BAND', ctx.band),
      field('RST_SENT', ctx.rst),
      field('RST_RCVD', ctx.rst),
      field('NAME', row.name),
      field('QTH', row.city),
      field('STATE', row.state),
      field('GRIDSQUARE', row.grid),
      field('COMMENT', notes),
      field('OPERATOR', ctx.operator),
      field('STATION_CALLSIGN', ctx.operator),
    ].filter(Boolean);

    return out.join(' ') + ' <EOR>';
  }

  /**
   * The whole document.
   *
   *   build({ net, freq, ncs, date, mode, rst, rows, version, now })
   *
   * `freq` and `date` are the raw header strings — parsing them is this
   * module's job, not the page's. `mode` overrides the frequency-derived
   * default; blank `rst` omits both signal-report fields rather than inventing
   * one. `now` is injectable so a test can pin CREATED_TIMESTAMP.
   */
  function build(opts) {
    opts = opts || {};
    var rows = Array.isArray(opts.rows) ? opts.rows : [];

    var mhz = parseFreqMHz(opts.freq);
    var ctx = {
      date: parseDate(opts.date) || todayUTC(opts.now),
      mode: _str(opts.mode).trim().toUpperCase() || modeFor(mhz),
      freq: mhz === null ? '' : String(mhz),
      band: bandFor(mhz),
      rst: _str(opts.rst).trim(),
      operator: _str(opts.ncs).trim().toUpperCase(),
    };

    var net = _str(opts.net).trim();
    var lines = [
      'ADIF export from OASIS' + (net ? ' — net log: ' + net : ' net log'),
      field('ADIF_VER', ADIF_VER),
      field('PROGRAMID', 'OASIS'),
      field('PROGRAMVERSION', opts.version),
      field('CREATED_TIMESTAMP', timestamp(opts.now)),
      '<EOH>',
    ].filter(Boolean);

    rows.forEach(function (row) {
      var rec = record(row, ctx);
      if (rec) lines.push(rec);
    });

    return lines.join('\r\n') + '\r\n';
  }

  /** How many rows build() would actually write — the page reports this. */
  function countExportable(rows) {
    return (Array.isArray(rows) ? rows : []).filter(function (r) {
      return r && _str(r.call).trim();
    }).length;
  }

  return {
    ADIF_VER: ADIF_VER, BANDS: BANDS,
    byteLen: byteLen, parseFreqMHz: parseFreqMHz, bandFor: bandFor,
    modeFor: modeFor, parseDate: parseDate, parseTime: parseTime,
    field: field, todayUTC: todayUTC, timestamp: timestamp,
    record: record, build: build,
    countExportable: countExportable,
  };
});
