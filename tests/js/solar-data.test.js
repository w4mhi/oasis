'use strict';
const test = require('node:test');
const assert = require('node:assert');
const S = require('../../common/js/solar-data.js');

function stubStorage(obj) {
  return { getItem: (k) => (k in obj ? obj[k] : null) };
}

test('latestSolar returns newest report or null', () => {
  assert.strictEqual(S.latestSolar(stubStorage({})), null);
  assert.strictEqual(S.latestSolar(stubStorage({ 'solar-history': 'null' })), null);
  assert.strictEqual(S.latestSolar(stubStorage({ 'solar-history': '[]' })), null);
  const rep = { solarflux: '140', bands: { '30m-20m': { day: 'Good' } } };
  const st = stubStorage({ 'solar-history': JSON.stringify([rep, { old: true }]) });
  assert.deepStrictEqual(S.latestSolar(st), rep);
});

test('latestSolar tolerates bad JSON', () => {
  assert.strictEqual(S.latestSolar(stubStorage({ 'solar-history': '{bad' })), null);
});

test('bandCondition reads a group', () => {
  const rep = { bands: { '30m-20m': { day: 'Good', night: 'Fair' } } };
  assert.deepStrictEqual(S.bandCondition(rep, '30m-20m'), { day: 'Good', night: 'Fair' });
  assert.deepStrictEqual(S.bandCondition(rep, '12m-10m'), {});
  assert.deepStrictEqual(S.bandCondition(null, '30m-20m'), {});
});

test('ageHours + isStale', () => {
  const now = Date.parse('2026-07-23T12:00:00Z');
  const rep = { savedAt: '2026-07-23T00:00:00Z' };   // 12h old
  assert.ok(Math.abs(S.ageHours(rep, now) - 12) < 0.01);
  assert.strictEqual(S.isStale(rep, 24, now), false);
  assert.strictEqual(S.isStale(rep, 6, now), true);
  assert.strictEqual(S.ageHours({}, now), Infinity);
  assert.strictEqual(S.isStale({}, 24, now), true);   // unknown age => stale
});

test('bandsForDistanceKm buckets', () => {
  assert.deepStrictEqual(S.bandsForDistanceKm(200), ['80m-40m']);
  assert.deepStrictEqual(S.bandsForDistanceKm(1000), ['80m-40m', '30m-20m']);
  assert.deepStrictEqual(S.bandsForDistanceKm(2500), ['30m-20m', '17m-15m']);
  assert.deepStrictEqual(S.bandsForDistanceKm(5000), ['30m-20m', '17m-15m', '12m-10m']);
  assert.deepStrictEqual(S.bandsForDistanceKm(12000), ['17m-15m', '12m-10m', '30m-20m']);
  assert.deepStrictEqual(S.bandsForDistanceKm(NaN), []);
});
