'use strict';
const test = require('node:test');
const assert = require('node:assert');
const M = require('../../common/js/maidenhead.js');

test('latLonToGrid — known points + precision', () => {
  // Origin: Gauss cell JJ00 at the 0°/0° corner.
  assert.strictEqual(M.latLonToGrid(0, 0, 6), 'JJ00aa');
  assert.strictEqual(M.latLonToGrid(0, 0, 4), 'JJ00');
  // FN31 (NYC / Long Island area) — the classic east-coast reference square.
  assert.strictEqual(M.latLonToGrid(41.5, -73, 4), 'FN31');
  // precision defaults to 6 and coerces junk values back to 6.
  assert.strictEqual(M.latLonToGrid(41.5, -73).length, 6);
  assert.strictEqual(M.latLonToGrid(41.5, -73, 99).length, 6);
});

test('latLonToGrid — out-of-range returns empty', () => {
  assert.strictEqual(M.latLonToGrid(999, 0), '');
  assert.strictEqual(M.latLonToGrid(0, 999), '');
  assert.strictEqual(M.latLonToGrid(-91, 0), '');
  assert.strictEqual(M.latLonToGrid(0, 181), '');
});

test('gridToLatLon — cell centers + validation', () => {
  assert.deepStrictEqual(M.gridToLatLon('JJ00'), [0.5, 1]);
  assert.deepStrictEqual(M.gridToLatLon('FN31'), [41.5, -73]);
  // Case-insensitive + whitespace-tolerant.
  assert.deepStrictEqual(M.gridToLatLon('  fn31  '), [41.5, -73]);
  // Invalid inputs → null (not a throw).
  assert.strictEqual(M.gridToLatLon(null), null);
  assert.strictEqual(M.gridToLatLon('12'), null);
  assert.strictEqual(M.gridToLatLon('ZZ99'), null);   // Z is outside the A-R field band
  assert.strictEqual(M.gridToLatLon('FN3'), null);    // odd length
});

test('gridToLonLat swaps gridToLatLon', () => {
  assert.deepStrictEqual(M.gridToLonLat('FN31'), [-73, 41.5]);
  assert.strictEqual(M.gridToLonLat('nope'), null);
});

test('round-trip: encode → decode → re-encode is stable', () => {
  for (const [lat, lon] of [[41.5, -73], [51.5, -0.1], [-33.9, 151.2], [35.7, 139.7]]) {
    const grid = M.latLonToGrid(lat, lon, 6);
    const [dlat, dlon] = M.gridToLatLon(grid);
    // Decoded point must fall inside the same 6-char cell it came from.
    assert.strictEqual(M.latLonToGrid(dlat, dlon, 6), grid);
  }
});
