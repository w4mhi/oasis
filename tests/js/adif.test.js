'use strict';
/*
 * adif.test.js — pins the ADI output of the net logger's ADIF export.
 *
 * ADI is length-prefixed, so a wrong length does not corrupt one field: the
 * reader takes that many bytes and resumes parsing mid-value, shifting every
 * field after it. These tests exist mostly to hold that one invariant down.
 */
const test = require('node:test');
const assert = require('node:assert');
const A = require('../../common/js/adif.js');

// A fixed instant so CREATED_TIMESTAMP is assertable.
const NOW = new Date(Date.UTC(2026, 7, 17, 19, 4, 5));

const ROW = {
  seq: 1, utc: '1432Z', call: 'w4mhi', name: 'Mihai', city: 'Huntsville',
  state: 'AL', grid: 'EM64', traffic: false, notes: 'first check-in',
};

// ── field() ──────────────────────────────────────────────────────────────────

test('field length is the UTF-8 byte count, not the character count', () => {
  assert.strictEqual(A.field('CALL', 'W4MHI'), '<CALL:5>W4MHI');
  // 5 characters, 6 bytes — the whole reason byteLen() exists.
  assert.strictEqual(A.field('NAME', 'Josée'), '<NAME:6>Josée');
  assert.strictEqual(A.byteLen('Josée'), 6);
});

test('an empty field emits nothing at all, never <NAME:0>', () => {
  ['', '   ', null, undefined].forEach((v) => {
    assert.strictEqual(A.field('NAME', v), '', JSON.stringify(v));
  });
});

test('field names are upper-cased and newlines flattened', () => {
  assert.strictEqual(A.field('qth', 'Huntsville'), '<QTH:10>Huntsville');
  assert.strictEqual(A.field('COMMENT', 'two\nlines'), '<COMMENT:9>two lines');
  assert.strictEqual(A.field('COMMENT', 'crlf\r\nhere'), '<COMMENT:9>crlf here');
});

// ── frequency, band, mode ────────────────────────────────────────────────────

test('the frequency box is free text and only the first number matters', () => {
  assert.strictEqual(A.parseFreqMHz('146.520 MHz'), 146.52);
  assert.strictEqual(A.parseFreqMHz('146.94 (W4XYZ repeater, -600)'), 146.94);
  assert.strictEqual(A.parseFreqMHz('14.325'), 14.325);
  assert.strictEqual(A.parseFreqMHz('simplex'), null);
  assert.strictEqual(A.parseFreqMHz(''), null);
  assert.strictEqual(A.parseFreqMHz(null), null);
});

test('bandFor covers the bands a net actually runs on', () => {
  assert.strictEqual(A.bandFor(146.52), '2m');
  assert.strictEqual(A.bandFor(446.0), '70cm');
  assert.strictEqual(A.bandFor(14.325), '20m');
  assert.strictEqual(A.bandFor(3.965), '80m');
  assert.strictEqual(A.bandFor(53.1), '6m');
  assert.strictEqual(A.bandFor(927.0), '33cm');
});

test('band edges are inclusive and the gaps between bands are empty', () => {
  assert.strictEqual(A.bandFor(144.0), '2m');
  assert.strictEqual(A.bandFor(148.0), '2m');
  assert.strictEqual(A.bandFor(143.9), '');
  assert.strictEqual(A.bandFor(148.1), '');
  assert.strictEqual(A.bandFor(462.5625), '');   // FRS, not an amateur band
  assert.strictEqual(A.bandFor(null), '');
  assert.strictEqual(A.bandFor(NaN), '');
});

test('modeFor splits at 30 MHz and says nothing without a frequency', () => {
  assert.strictEqual(A.modeFor(14.325), 'SSB');
  assert.strictEqual(A.modeFor(29.6), 'SSB');
  assert.strictEqual(A.modeFor(50.125), 'FM');
  assert.strictEqual(A.modeFor(146.52), 'FM');
  assert.strictEqual(A.modeFor(null), '');
});

// ── date and time ────────────────────────────────────────────────────────────

test('parseDate accepts what the date box actually contains', () => {
  assert.strictEqual(A.parseDate('2026-08-17 UTC'), '20260817');
  assert.strictEqual(A.parseDate('2026/08/17'), '20260817');
  assert.strictEqual(A.parseDate('20260817'), '20260817');
  assert.strictEqual(A.parseDate('2026-8-7'), '20260807');
});

test('parseDate refuses junk rather than guessing', () => {
  assert.strictEqual(A.parseDate('tuesday'), '');
  assert.strictEqual(A.parseDate('2026-13-01'), '');
  assert.strictEqual(A.parseDate('2026-02-45'), '');
  assert.strictEqual(A.parseDate(''), '');
});

test('parseTime accepts the stored form and what an operator might retype', () => {
  assert.strictEqual(A.parseTime('1432Z'), '1432');
  assert.strictEqual(A.parseTime('14:32Z'), '1432');
  assert.strictEqual(A.parseTime('14:32:07'), '143207');
  assert.strictEqual(A.parseTime('0005Z'), '0005');
});

test('parseTime rejects impossible and malformed clocks', () => {
  assert.strictEqual(A.parseTime('2515Z'), '');
  assert.strictEqual(A.parseTime('1499Z'), '');
  assert.strictEqual(A.parseTime('143'), '');
  assert.strictEqual(A.parseTime('half past two'), '');
  assert.strictEqual(A.parseTime(''), '');
});

// ── record() ─────────────────────────────────────────────────────────────────

const CTX = {
  date: '20260817', mode: 'FM', freq: '146.52', band: '2m',
  rst: '59', operator: 'W4MHI',
};

test('a check-in becomes one QSO record, callsign upper-cased', () => {
  const r = A.record(ROW, CTX);
  assert.ok(r.startsWith('<CALL:5>W4MHI '), r);
  assert.ok(r.endsWith(' <EOR>'), r);
  ['<QSO_DATE:8>20260817', '<TIME_ON:4>1432', '<MODE:2>FM', '<FREQ:6>146.52',
   '<BAND:2>2m', '<RST_SENT:2>59', '<RST_RCVD:2>59', '<NAME:5>Mihai',
   '<QTH:10>Huntsville', '<STATE:2>AL', '<GRIDSQUARE:4>EM64',
   '<COMMENT:14>first check-in', '<OPERATOR:5>W4MHI',
   '<STATION_CALLSIGN:5>W4MHI'].forEach((tag) => {
    assert.ok(r.includes(tag), 'missing ' + tag + ' in ' + r);
  });
});

test('a row with no callsign is not a QSO and is dropped', () => {
  assert.strictEqual(A.record(Object.assign({}, ROW, { call: '' }), CTX), '');
  assert.strictEqual(A.record(Object.assign({}, ROW, { call: '  ' }), CTX), '');
  assert.strictEqual(A.record({}, CTX), '');
});

test('the traffic flag is marked in COMMENT, the same way ICS-309 marks it', () => {
  const withNotes = A.record(Object.assign({}, ROW, { traffic: true }), CTX);
  assert.ok(withNotes.includes('<COMMENT:24>first check-in (traffic)'), withNotes);
  const bare = A.record({ call: 'N0CALL', traffic: true }, CTX);
  assert.ok(bare.includes('<COMMENT:7>traffic'), bare);
});

test('blank RST omits both report fields instead of inventing one', () => {
  const r = A.record(ROW, Object.assign({}, CTX, { rst: '' }));
  assert.ok(!r.includes('RST_SENT'), r);
  assert.ok(!r.includes('RST_RCVD'), r);
});

test('a sparse check-in emits only the fields it has', () => {
  const r = A.record({ call: 'N0CALL', utc: '1500Z' }, CTX);
  ['NAME', 'QTH', 'STATE', 'GRIDSQUARE', 'COMMENT'].forEach((f) => {
    assert.ok(!r.includes('<' + f + ':'), f + ' should be absent from ' + r);
  });
  assert.ok(r.includes('<CALL:6>N0CALL'), r);
});

// ── build() ──────────────────────────────────────────────────────────────────

const HEADER = {
  net: 'ARES/RACES Net', freq: '146.520 MHz', ncs: 'w4mhi',
  date: '2026-08-17 UTC', rst: '59', version: '3.63.7', now: NOW,
};

test('build writes a conformant header terminated by <EOH>', () => {
  const out = A.build(Object.assign({}, HEADER, { rows: [ROW] }));
  const lines = out.split('\r\n');
  assert.ok(lines[0].startsWith('ADIF export from OASIS'), lines[0]);
  assert.ok(out.includes('<ADIF_VER:5>3.1.4'));
  assert.ok(out.includes('<PROGRAMID:5>OASIS'));
  assert.ok(out.includes('<PROGRAMVERSION:6>3.63.7'));
  assert.ok(out.includes('<CREATED_TIMESTAMP:15>20260817 190405'));
  assert.strictEqual(lines.filter((l) => l === '<EOH>').length, 1);
  // Header first, records after.
  assert.ok(out.indexOf('<EOH>') < out.indexOf('<CALL:'));
});

test('build derives mode and band from the frequency box', () => {
  const vhf = A.build(Object.assign({}, HEADER, { rows: [ROW] }));
  assert.ok(vhf.includes('<MODE:2>FM'), vhf);
  assert.ok(vhf.includes('<BAND:2>2m'), vhf);

  const hf = A.build(Object.assign({}, HEADER, { freq: '3.965 MHz', rows: [ROW] }));
  assert.ok(hf.includes('<MODE:3>SSB'), hf);
  assert.ok(hf.includes('<BAND:3>80m'), hf);
});

test('an explicit mode overrides the derived one and is upper-cased', () => {
  const out = A.build(Object.assign({}, HEADER, { mode: 'c4fm', rows: [ROW] }));
  assert.ok(out.includes('<MODE:4>C4FM'), out);
  assert.ok(!out.includes('<MODE:2>FM'), out);
});

test('an unparseable frequency omits FREQ and BAND rather than guessing', () => {
  const out = A.build(Object.assign({}, HEADER, { freq: 'simplex', rows: [ROW] }));
  assert.ok(!out.includes('<FREQ:'), out);
  assert.ok(!out.includes('<BAND:'), out);
  assert.ok(!out.includes('<MODE:'), out);   // no frequency, nothing to derive
});

test('an unparseable date falls back to today UTC, not to a blank QSO_DATE', () => {
  const out = A.build(Object.assign({}, HEADER, { date: 'tuesday', rows: [ROW] }));
  assert.ok(out.includes('<QSO_DATE:8>20260817'), out);
});

test('build emits one record per logged callsign and skips the rest', () => {
  const rows = [ROW, { call: 'N0CALL', utc: '1440Z' }, { call: '', name: 'nobody' }];
  const out = A.build(Object.assign({}, HEADER, { rows: rows }));
  assert.strictEqual(out.split('<EOR>').length - 1, 2);
  assert.strictEqual(A.countExportable(rows), 2);
});

test('an empty log still produces a valid, record-free file', () => {
  const out = A.build(Object.assign({}, HEADER, { rows: [] }));
  assert.ok(out.includes('<EOH>'));
  assert.ok(!out.includes('<EOR>'));
  assert.strictEqual(A.countExportable([]), 0);
  assert.strictEqual(A.countExportable(null), 0);
});

test('build survives being handed nothing at all', () => {
  const out = A.build();
  assert.ok(out.includes('<EOH>'), out);
  assert.ok(!out.includes('<EOR>'), out);
});

test('every record line is round-trippable by a length-prefixed reader', () => {
  const out = A.build(Object.assign({}, HEADER, {
    rows: [Object.assign({}, ROW, { name: 'Josée', notes: 'café net' })],
  }));
  // Walk the tags the way a real parser does: read the declared byte count and
  // expect the next character to be '<' or a separator. A wrong length shows up
  // here as a mid-value resume.
  const buf = Buffer.from(out, 'utf8');
  let i = buf.indexOf('<EOH>') + 5;
  let seen = 0;
  while (i < buf.length) {
    const open = buf.indexOf(0x3c /* < */, i);
    if (open === -1) break;
    const close = buf.indexOf(0x3e /* > */, open);
    const tag = buf.slice(open + 1, close).toString('utf8');
    if (tag === 'EOR') { i = close + 1; continue; }
    const parts = tag.split(':');
    assert.strictEqual(parts.length, 2, 'malformed tag ' + tag);
    const len = Number(parts[1]);
    assert.ok(Number.isInteger(len) && len > 0, 'bad length in ' + tag);
    i = close + 1 + len;
    const next = buf.slice(i, i + 1).toString('utf8');
    assert.ok(next === '' || next === ' ' || next === '<' || next === '\r',
              'field ' + tag + ' length lands mid-value, next char ' + JSON.stringify(next));
    seen++;
  }
  assert.ok(seen > 10, 'expected to walk the whole record, saw ' + seen);
});
