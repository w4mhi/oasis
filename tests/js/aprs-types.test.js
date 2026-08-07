'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const T = require('../../common/js/aprs-types.js');

const ROOT = path.join(__dirname, '..', '..');

test('table shape: every entry is [key, label], both non-empty strings', () => {
  const codes = Object.keys(T.TABLE);
  assert.ok(codes.length >= 44, 'table shrank unexpectedly: ' + codes.length);
  for (const c of codes) {
    assert.strictEqual(c.length, 1, 'sym_code must be a single char: ' + JSON.stringify(c));
    const v = T.TABLE[c];
    assert.ok(Array.isArray(v) && v.length === 2, 'not a pair: ' + c);
    assert.ok(typeof v[0] === 'string' && v[0], 'empty key: ' + c);
    assert.ok(typeof v[1] === 'string' && v[1], 'empty label: ' + c);
  }
});

test('categoryOf: lookup, weather special case, and Other fallback', () => {
  assert.deepStrictEqual(T.categoryOf({ sym_code: '>' }), { key: 'car', label: 'Car' });
  assert.deepStrictEqual(T.categoryOf({ sym_code: 'X' }), { key: 'heli', label: 'Helicopter' });
  // '_' is weather on BOTH symbol tables, so it must not depend on sym_table.
  assert.deepStrictEqual(T.categoryOf({ sym_code: '_' }), { key: 'wx', label: 'Weather' });
  assert.deepStrictEqual(T.categoryOf({ sym_code: '_', sym_table: '\\' }), { key: 'wx', label: 'Weather' });
  // Unknown / missing input must never throw — both pages call this per row.
  for (const s of [null, undefined, {}, { sym_code: '' }, { sym_code: '' }]) {
    assert.deepStrictEqual(T.categoryOf(s), { key: 'other', label: 'Other' });
  }
});

test('labelOf agrees with categoryOf().label for every code', () => {
  for (const c of Object.keys(T.TABLE)) {
    assert.strictEqual(T.labelOf({ sym_code: c }), T.categoryOf({ sym_code: c }).label);
  }
  assert.strictEqual(T.labelOf(null), 'Other');
});

// The regression this module exists to prevent: the table used to be duplicated
// by hand in index.html (_APRS_TYPES) and map.html (_OBJ_CATS). Neither page may
// reintroduce a local copy, or they can silently disagree again.
test('neither page carries its own symbol table any more', () => {
  for (const rel of ['index.html', 'maps/traffic/map.html']) {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    assert.ok(!/const\s+_APRS_TYPES\s*=/.test(src), rel + ' reintroduced _APRS_TYPES');
    assert.ok(!/const\s+_OBJ_CATS\s*=/.test(src), rel + ' reintroduced _OBJ_CATS');
    assert.ok(src.includes('src="/common/js/aprs-types.js"'), rel + ' does not load aprs-types.js');
  }
});

// Symbols the pages' own logic keys on by name; dropping one would break the
// Type filter, the emergency chip, or the map legend without any syntax error.
test('category keys and labels the pages depend on are present', () => {
  assert.strictEqual(T.labelOf({ sym_code: '!' }), 'Public safety');  // index emergency chip
  assert.strictEqual(T.labelOf({ sym_code: ':' }), 'Fire');
  assert.strictEqual(T.labelOf({ sym_code: "'" }), 'Aircraft');       // ADS-B rows borrow this
  assert.strictEqual(T.labelOf({ sym_code: 'X' }), 'Helicopter');
  assert.strictEqual(T.categoryOf({ sym_code: '#' }).key, 'digi');    // map legend rows
  assert.strictEqual(T.categoryOf({ sym_code: '&' }).key, 'igate');
});
