'use strict';
const test = require('node:test');
const assert = require('node:assert');
const F = require('../../common/js/format.js');

const agoIso = (ms) => new Date(Date.now() - ms).toISOString();

test('fmtAge buckets + classes', () => {
  assert.deepStrictEqual(F.fmtAge(null), { text: '—', cls: '' });
  assert.deepStrictEqual(F.fmtAge(agoIso(5 * 60000)), { text: '5m ago', cls: 'age-ok' });
  assert.deepStrictEqual(F.fmtAge(agoIso(45 * 60000)), { text: '45m ago', cls: 'age-warn' });
  assert.deepStrictEqual(F.fmtAge(agoIso(2 * 3600000)), { text: '2h ago', cls: 'age-warn' });
  assert.deepStrictEqual(F.fmtAge(agoIso(8 * 3600000)), { text: '8h ago', cls: 'age-old' });
  assert.deepStrictEqual(F.fmtAge(agoIso(-60000)), { text: 'just now', cls: 'age-ok' });
});

test('fmtLastHeard guards + shape', () => {
  assert.strictEqual(F.fmtLastHeard(null), '—');
  assert.strictEqual(F.fmtLastHeard('not-a-date'), 'not-a-date');
  const out = F.fmtLastHeard('2026-07-17T14:05:00Z');
  // UTC portion is timezone-independent — assert it exactly.
  assert.ok(out.startsWith('2026-07-17 14:05Z'));
  // Local [HH:MM] suffix depends on the runner's timezone — assert only its shape.
  assert.ok(out.includes('['));
});
