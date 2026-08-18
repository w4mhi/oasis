'use strict';
// Unit tests for the roster search matcher.
//
// The bug these pin down: the roster row draws the amateur designator on its
// second line ("SO-50 [LEO]") while the search read only `s.name`, so the
// operator typed the token the row was showing them and got "No satellites
// match the filters." Every assertion below is a thing a real operator types.
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');

const S = require(path.join(__dirname, '..', '..', 'services', 'satellites',
                            'static', 'sat-search.js'));

// Shaped exactly like a configuration/satellites.json record.
const SO50 = { name: 'SAUDISAT 1C [LEO]', designator: 'SO-50', orbit: 'LEO', norad: 27607 };
const AO7  = { name: 'OSCAR 7 [LEO]',     designator: 'AO-7',  orbit: 'LEO', norad: 7530 };
const ISS  = { name: 'ISS [LEO]',         designator: null,    orbit: 'LEO', norad: 25544 };
const BARE = { name: 'NETSAT 3 [LEO]',    orbit: 'LEO', norad: 46280 };  // no designator key at all

// ── the fix ────────────────────────────────────────────────────────────────
test('the designator finds the bird', () => {
  assert.ok(S.matches(SO50, 'SO-50'));
  assert.ok(S.matches(AO7, 'AO-7'));
});

test('the designator is matched case- and hyphen-insensitively', () => {
  for (const q of ['so-50', 'SO50', 'so 50', ' So-50 ']) {
    assert.ok(S.matches(SO50, q), `expected ${JSON.stringify(q)} to find SO-50`);
  }
});

test('a designator does not find an unrelated bird', () => {
  assert.ok(!S.matches(AO7, 'SO-50'));
  assert.ok(!S.matches(ISS, 'AO-7'));
});

// ── NORAD, quoted by CelesTrak and every pass predictor ────────────────────
test('the catalogue number finds the bird', () => {
  assert.ok(S.matches(ISS, '25544'));
  assert.ok(!S.matches(SO50, '25544'));
});

// ── no regression: the catalogue name still works ──────────────────────────
test('the catalogue name still matches, whole or partial', () => {
  assert.ok(S.matches(SO50, 'SAUDISAT'));
  assert.ok(S.matches(SO50, 'saudisat 1c'));
  assert.ok(S.matches(AO7, 'oscar'));
});

test('the orbit class still matches, as it did via the name suffix', () => {
  assert.ok(S.matches(SO50, 'LEO'));
});

test('an empty query filters nothing', () => {
  for (const q of ['', '   ', null, undefined]) assert.ok(S.matches(SO50, q));
});

// ── robustness: records without a designator must not throw ────────────────
test('a record with a null or absent designator is searchable by name', () => {
  assert.ok(S.matches(ISS, 'ISS'));
  assert.ok(S.matches(BARE, 'NETSAT'));
  assert.ok(!S.matches(BARE, 'AO-7'));
});

test('a junk record does not throw', () => {
  assert.doesNotThrow(() => S.matches(null, 'ao-7'));
  assert.doesNotThrow(() => S.matches({}, 'ao-7'));
  assert.strictEqual(S.matches({}, 'ao-7'), false);
});

// ── fields are matched individually, never joined ──────────────────────────
test('a query may not straddle two fields', () => {
  // "SO-50" + "LEO" concatenated would contain "50leo"; nothing on the row does.
  assert.ok(!S.matches(SO50, '50leo'));
  assert.ok(!S.matches(SO50, '1cso'));
});

// ── the shared name rule ───────────────────────────────────────────────────
test('bareName strips the trailing orbit tag only', () => {
  assert.strictEqual(S.bareName('SAUDISAT 1C [LEO]'), 'SAUDISAT 1C');
  assert.strictEqual(S.bareName('CAS-6 (TO-108) [LEO]'), 'CAS-6 (TO-108)');
  assert.strictEqual(S.bareName('NO TAG'), 'NO TAG');
  assert.strictEqual(S.bareName(null), '');
});

test('a designator carried in the NAME is still searchable', () => {
  // CAS-6 keeps TO-108 in its catalogue name, so `designator` is null for it.
  const cas6 = { name: 'CAS-6 (TO-108) [LEO]', designator: null, orbit: 'LEO', norad: 44881 };
  assert.ok(S.matches(cas6, 'TO-108'));
  assert.ok(S.matches(cas6, 'to108'));
});
