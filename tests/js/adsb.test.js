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
  assert.strictEqual(A.altColor(1000), '#8d24d8');        // <=1500  Indigo (brighter)
  assert.strictEqual(A.altColor(1500), '#8d24d8');        // boundary
  assert.strictEqual(A.altColor(1501), '#3939f7');        // <=3000  Blue (brighter)
  assert.strictEqual(A.altColor(3000), '#3939f7');
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

test('hexIsMilitary — ICAO military address ranges', () => {
  assert.strictEqual(A.hexIsMilitary('AE1234'), true);    // US mil block (ADF7C8–AFFFFF)
  assert.strictEqual(A.hexIsMilitary('~AE1234'), true);   // TIS-B '~' prefix stripped
  assert.strictEqual(A.hexIsMilitary('C21234'), true);    // Canada mil block
  assert.strictEqual(A.hexIsMilitary('A12345'), false);   // US civil (below ADF7C8)
  assert.strictEqual(A.hexIsMilitary('a2af23'), false);   // civil example from the live feed
  assert.strictEqual(A.hexIsMilitary(''), false);
  assert.strictEqual(A.hexIsMilitary(null), false);
});

test('acSymbol — class-aware aircraft symbol [table, code]', () => {
  assert.deepStrictEqual(A.acSymbol({ category: 'A1' }), ['/', "'"]);   // small
  assert.deepStrictEqual(A.acSymbol({ category: 'A2' }), ['/', "'"]);
  assert.deepStrictEqual(A.acSymbol({}), ['/', "'"]);                   // unknown → small default
  assert.deepStrictEqual(A.acSymbol({ category: 'A3' }), ['\\', '^']);  // large / heavy
  assert.deepStrictEqual(A.acSymbol({ category: 'A5' }), ['\\', '^']);
  assert.deepStrictEqual(A.acSymbol({ category: 'A7' }), ['\\', 'h']);  // heli
  assert.deepStrictEqual(A.acSymbol({ category: 'B1' }), ['/', 'g']);   // glider
  assert.deepStrictEqual(A.acSymbol({ hex: 'AE1234', category: 'A3' }), ['/', 'g']); // mil overrides class
});
