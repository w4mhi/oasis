'use strict';
const test = require('node:test');
const assert = require('node:assert');
const A = require('../../static/js/adsb.js');

test('altColor bands (feet)', () => {
  assert.strictEqual(A.altColor('ground'), '#A0522D');
  assert.strictEqual(A.altColor(null), '#B8C4D0');
  assert.strictEqual(A.altColor(undefined), '#B8C4D0');   // both consumers rely on undefined->grey
  assert.strictEqual(A.altColor(NaN), '#B8C4D0');
  assert.strictEqual(A.altColor(1000), '#FF3311');
  assert.strictEqual(A.altColor(2000), '#FF3311');
  assert.strictEqual(A.altColor(2001), '#FF7F00');
  assert.strictEqual(A.altColor(5000), '#FF7F00');
  assert.strictEqual(A.altColor(10000), '#FFFF00');
  assert.strictEqual(A.altColor(15000), '#11FF66');
  assert.strictEqual(A.altColor(25000), '#00FFFF');
  assert.strictEqual(A.altColor(35000), '#0066FF');
  assert.strictEqual(A.altColor(40000), '#FF00FF');
});

test('operatorTag decodes ICAO prefix', () => {
  const tbl = { ASA: 'ALASKA', UAL: 'UNITED' };
  assert.strictEqual(A.operatorTag('ASA424', tbl), 'ALASKA');
  assert.strictEqual(A.operatorTag('asa424', tbl), 'ALASKA');
  assert.strictEqual(A.operatorTag('UAL1', tbl), 'UNITED');
  assert.strictEqual(A.operatorTag('N12345', tbl), '');    // registration
  assert.strictEqual(A.operatorTag('UAL', tbl), '');       // no digit
  assert.strictEqual(A.operatorTag('ZZZ123', tbl), '');    // unknown code
  assert.strictEqual(A.operatorTag('', tbl), '');
  assert.strictEqual(A.operatorTag(null, tbl), '');
});

test('recentHoursForAge maps Age <select> value', () => {
  assert.strictEqual(A.recentHoursForAge('0'), A.RECENT_ALL_HOURS);
  assert.strictEqual(A.recentHoursForAge(0), A.RECENT_ALL_HOURS);
  assert.strictEqual(A.recentHoursForAge('1440'), 24);
  assert.strictEqual(A.recentHoursForAge('60'), 24);
  assert.strictEqual(A.recentHoursForAge('15'), 24);
});
