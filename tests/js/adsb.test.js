'use strict';
const test = require('node:test');
const assert = require('node:assert');
const A = require('../../common/js/adsb.js');

test('altColor bands (feet)', () => {
  // Mirrors the spectral (Doppler) bands in common/js/adsb.js — keep in sync with
  // that single source. Each assertion pins a band and, where useful, its boundary.
  assert.strictEqual(A.altColor('ground'), '#A0522D');
  assert.strictEqual(A.altColor(null), '#B8C4D0');
  assert.strictEqual(A.altColor(undefined), '#B8C4D0');   // both consumers rely on undefined->grey
  assert.strictEqual(A.altColor(NaN), '#B8C4D0');
  assert.strictEqual(A.altColor(-1), '#B8C4D0');          // negative -> grey
  assert.strictEqual(A.altColor(1000), '#4B0082');        // <=1500  Indigo
  assert.strictEqual(A.altColor(1500), '#4B0082');        // boundary
  assert.strictEqual(A.altColor(1501), '#0000FF');        // <=3000  Blue
  assert.strictEqual(A.altColor(3000), '#0000FF');
  assert.strictEqual(A.altColor(6000), '#0080FF');        // <=6000  Light Blue
  assert.strictEqual(A.altColor(10000), '#00FFFF');       // <=10000 Cyan
  assert.strictEqual(A.altColor(14000), '#00FF80');       // <=14000 Teal
  assert.strictEqual(A.altColor(18000), '#00FF00');       // <=18000 Green
  assert.strictEqual(A.altColor(23000), '#80FF00');       // <=23000 Chartreuse
  assert.strictEqual(A.altColor(28000), '#FFFF00');       // <=28000 Yellow
  assert.strictEqual(A.altColor(33000), '#FFCC00');       // <=33000 Amber
  assert.strictEqual(A.altColor(38000), '#FF8000');       // <=38000 Orange
  assert.strictEqual(A.altColor(43000), '#FF0000');       // <=43000 Red
  assert.strictEqual(A.altColor(45000), '#FF00FF');       // >43000  Magenta
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
