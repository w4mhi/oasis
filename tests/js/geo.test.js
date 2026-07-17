'use strict';
const test = require('node:test');
const assert = require('node:assert');
const U = require('../../common/js/units.js'); // must load first: geo reads OasisUnits
const G = require('../../common/js/geo.js');

test('gridSquare known values + guards', () => {
  assert.strictEqual(G.gridSquare(0, 0), 'JJ00aa');
  assert.strictEqual(G.gridSquare(null, 0), '');
  assert.strictEqual(G.gridSquare('x', 0), '');
  assert.strictEqual(G.gridSquare(51.5, -0.1).slice(0, 4), 'IO91');
});

test('haversineMi ~69mi per degree at equator', () => {
  assert.ok(Math.abs(G.haversineMi(0, 0, 0, 1) - 69.09) < 0.2);
});

test('bearingCardinal', () => {
  assert.strictEqual(G.bearingCardinal(0, 0, 0, 1), 'E');
  assert.strictEqual(G.bearingCardinal(0, 0, 1, 0), 'N');
});

test('distFromHome imperial/metric/here/unset', () => {
  G.setHomeCoords(0, 0);
  U.setUnits('imperial');
  assert.strictEqual(G.distFromHome(0, 1), '69.1mi E');
  U.setUnits('metric');
  assert.strictEqual(G.distFromHome(0, 1), '111km E');
  assert.strictEqual(G.distFromHome(0, 0.0005), 'here');
  G.setHomeCoords(null, null);
  assert.strictEqual(G.distFromHome(0, 1), '');
});
