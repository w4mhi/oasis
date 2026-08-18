'use strict';
// Finding 5(b): the map's NWR layer trusted the server's `active` set alone,
// which keeps a clock_suspect record alive for up to 90 days (see
// services/nwr/common/alerts.py STALE_CLOCK_QUARANTINE_S). A box that boots
// weeks stale — both OASIS Pis have — sets clock_suspect on everything it
// hears until the clock is fixed, so a real warning received during that
// window could pin the map for months. common/js/nwr-alerts.js adds a much
// shorter recency check for exactly that case.
const test = require('node:test');
const assert = require('node:assert');
const N = require('../../common/js/nwr-alerts.js');

const ACTIVE = new Set(['a1']);
const base = (extra) => Object.assign(
  { id: 'a1', type: 'tornado', matched: true, clock_suspect: false, received: 1000 },
  extra || {});

test('an ordinary active matched alert is visible', () => {
  assert.strictEqual(N.nwrMapVisible(base(), ACTIVE, 1000), true);
});

test('an alert missing from the server active set is not visible', () => {
  assert.strictEqual(N.nwrMapVisible(base(), new Set(), 1000), false);
});

test('an informational alert (type null) is never plotted', () => {
  assert.strictEqual(N.nwrMapVisible(base({ type: null }), ACTIVE, 1000), false);
});

test('an alert outside the watch list (unmatched) is not visible', () => {
  assert.strictEqual(N.nwrMapVisible(base({ matched: false }), ACTIVE, 1000), false);
});

test('a fresh clock_suspect alert is still visible', () => {
  const a = base({ clock_suspect: true, received: 1000 });
  assert.strictEqual(N.nwrMapVisible(a, ACTIVE, 1000 + 3600), true);   // +1h
});

test('a clock_suspect alert ages off the map long before the store retires it', () => {
  const a = base({ clock_suspect: true, received: 1000 });
  const justUnder = 1000 + N.NWR_CLOCK_SUSPECT_MAP_RECENCY_S - 1;
  const justOver = 1000 + N.NWR_CLOCK_SUSPECT_MAP_RECENCY_S + 1;
  assert.strictEqual(N.nwrMapVisible(a, ACTIVE, justUnder), true);
  assert.strictEqual(N.nwrMapVisible(a, ACTIVE, justOver), false);
});

test('the map recency window is far shorter than the store 90-day quarantine', () => {
  assert.ok(N.NWR_CLOCK_SUSPECT_MAP_RECENCY_S < 90 * 24 * 3600);
  assert.ok(N.NWR_CLOCK_SUSPECT_MAP_RECENCY_S > 0);
});
