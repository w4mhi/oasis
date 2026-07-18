'use strict';
const test = require('node:test');
const assert = require('node:assert');
const D = require('../../common/js/diagnostics.js');

test('statusClass maps known + unknown statuses', () => {
  assert.strictEqual(D.statusClass('ok'), 'ok');
  assert.strictEqual(D.statusClass('warn'), 'warn');
  assert.strictEqual(D.statusClass('fail'), 'fail');
  assert.strictEqual(D.statusClass('weird'), 'unknown');
  assert.strictEqual(D.statusClass(undefined), 'unknown');
  assert.strictEqual(D.statusClass(null), 'unknown');
});

test('statusIcon glyphs', () => {
  assert.strictEqual(D.statusIcon('ok'), '✓');
  assert.strictEqual(D.statusIcon('warn'), '⚠');
  assert.strictEqual(D.statusIcon('fail'), '✗');
  assert.strictEqual(D.statusIcon('other'), '…');
});

test('capUpDown: fail is DOWN, ok/warn count as UP', () => {
  const caps = [
    { id: 'ACCESS', status: 'ok' },
    { id: 'APRS_RX', status: 'warn' },
    { id: 'WINLINK', status: 'fail' },
  ];
  assert.deepStrictEqual(D.capUpDown(caps), { up: 2, down: 1 });
});

test('capUpDown guards empty/missing input', () => {
  assert.deepStrictEqual(D.capUpDown([]), { up: 0, down: 0 });
  assert.deepStrictEqual(D.capUpDown(undefined), { up: 0, down: 0 });
  assert.deepStrictEqual(D.capUpDown(null), { up: 0, down: 0 });
});

test('capUpDown ignores malformed entries without throwing', () => {
  assert.deepStrictEqual(D.capUpDown([null, {}, { status: 'ok' }]), { up: 3, down: 0 });
});
