'use strict';
const test = require('node:test');
const assert = require('node:assert');
const U = require('../../static/js/units.js');

test('fmtTemp imperial/metric/null', () => {
  U.setUnits('imperial');
  assert.strictEqual(U.fmtTemp(0), '32 °F');
  assert.strictEqual(U.fmtTemp(20), '68 °F');
  assert.strictEqual(U.fmtTemp(null), 'n/a');
  U.setUnits('metric');
  assert.strictEqual(U.fmtTemp(20), '20 °C');
  assert.strictEqual(U.fmtTemp(null), 'n/a');
});

test('fmtAlt imperial/metric/null', () => {
  U.setUnits('imperial');
  assert.strictEqual(U.fmtAlt(100), '328 ft');
  assert.strictEqual(U.fmtAlt(null), 'n/a');
  U.setUnits('metric');
  assert.strictEqual(U.fmtAlt(100), '100 m');
});

test('fmtSpeed imperial/metric/null', () => {
  U.setUnits('imperial');
  assert.strictEqual(U.fmtSpeed(60), '60 mph');
  assert.strictEqual(U.fmtSpeed(null), '');
  U.setUnits('metric');
  assert.strictEqual(U.fmtSpeed(100), '161 km/h');
});

test('fmtUptime buckets', () => {
  assert.strictEqual(U.fmtUptime(90), '1m');
  assert.strictEqual(U.fmtUptime(3661), '1h 1m');
  assert.strictEqual(U.fmtUptime(90061), '1d 1h');
});

test('isImperial reflects setUnits', () => {
  U.setUnits('imperial');
  assert.strictEqual(U.isImperial(), true);
  U.setUnits('metric');
  assert.strictEqual(U.isImperial(), false);
});
