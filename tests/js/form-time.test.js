/*
 * form-time.test.js — pins the one time format the ICS forms agreed on.
 *
 * Dates are constructed with local-time components so the assertions hold in
 * any TZ the test runs under; the UTC case builds from an explicit Z string.
 */
const test = require('node:test');
const assert = require('node:assert');
const T = require('../../common/js/form-time.js');

const at = (h, m) => new Date(2026, 7, 13, h, m, 0); // 13 Aug 2026, local

test('canonical ICS time is 24-hour local with the L marker', () => {
  assert.strictEqual(T.hhmmL(at(12, 45)), '1245L');
  assert.strictEqual(T.hhmmL(at(0, 0)), '0000L');
  assert.strictEqual(T.hhmmL(at(23, 59)), '2359L');
});

test('afternoon times never come out 12-hour', () => {
  assert.strictEqual(T.hhmm(at(13, 5)), '1305');
  assert.strictEqual(T.hhmm(at(21, 0)), '2100');
  assert.strictEqual(T.hhmm(at(0, 30)), '0030');
});

test('single digits are zero-padded on both halves', () => {
  assert.strictEqual(T.hhmm(at(9, 7)), '0907');
});

test('the combined stamp is YYYYMMDD HHmmL', () => {
  assert.strictEqual(T.stamp(at(9, 7)), '20260813 0907L');
});

test('dateISO feeds a native date input', () => {
  assert.strictEqual(T.dateISO(at(9, 7)), '2026-08-13');
});

test('the net log keeps UTC for its own display', () => {
  const d = new Date('2026-08-13T18:05:00.000Z');
  assert.strictEqual(T.hhmmZ(d), '1805Z');
});

test('crossing into an ICS form converts UTC rather than relabelling it', () => {
  const iso = '2026-08-13T18:05:00.000Z';
  const expected = T.hhmmL(new Date(iso));      // same instant, local clock
  assert.strictEqual(T.toLocalHHMM(iso), expected);
  assert.ok(expected.endsWith('L'), 'must be marked local, not Z');
  // the whole point: the Z spelling and the L spelling are different strings
  assert.notStrictEqual(T.toLocalHHMM(iso), T.hhmmZ(new Date(iso)));
});

test('a junk timestamp yields empty, never NaNL', () => {
  for (const bad of ['', null, undefined, 'not-a-date']) {
    assert.strictEqual(T.toLocalHHMM(bad), '', String(bad));
  }
});

test('every emitted time is four digits plus at most one zone letter', () => {
  const shape = /^\d{4}[LZ]?$/;
  assert.match(T.hhmm(at(7, 3)), shape);
  assert.match(T.hhmmL(at(7, 3)), shape);
  assert.match(T.hhmmZ(new Date('2026-08-13T07:03:00Z')), shape);
});
