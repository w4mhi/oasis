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

// ── The map's loader, sliced out of maps/traffic/map.html and run ────────────
// The filtering rule above is shared and tested; the LOADER around it is
// page-local, and it is where a stale marker survives. Every path through it
// must end at renderNwrAlerts(), which is the only caller of
// clearNwrMarkers().
const fs = require('node:fs');
const path = require('node:path');
const mapHtml = fs.readFileSync(
  path.join(__dirname, '..', '..', 'maps', 'traffic', 'map.html'), 'utf8');

function loader(responses) {
  const calls = { renders: 0 };
  const queue = responses.slice();
  const start = mapHtml.indexOf('let nwrAlerts    = [];');
  assert.notStrictEqual(start, -1, 'map.html: the NWR alert state is gone');
  const end = mapHtml.indexOf('\nfunction nwrMarkers', start);
  assert.notStrictEqual(end, -1, 'map.html: loadNwrAlerts not found');
  const src = mapHtml.slice(start, end);
  const fakeFetch = async () => {
    const next = queue.shift();
    if (next instanceof Error) { throw next; }
    return { ok: true, json: async () => next };
  };
  const factory = new Function('fetch', 'nwrMapVisible', 'renderNwrAlerts',
    src + '\nreturn { load: loadNwrAlerts, alerts: () => nwrAlerts };');
  const api = factory(fakeFetch, N.nwrMapVisible, () => { calls.renders++; });
  return { api, calls };
}

const ONE_ALERT = {
  ok: true, active: ['a1'],
  alerts: [{ id: 'a1', type: 'tornado', matched: true, clock_suspect: false,
             received: Date.now() / 1000, areas: [] }],
};

test('a server error clears the markers instead of freezing them', async () => {
  // server/app.py turns any exception on an /api/* route into {ok:false} —
  // e.g. alerts.active() meeting a record with no id. The old loader returned
  // on that before renderNwrAlerts(), so an expired tornado warning stayed
  // pinned to the map indefinitely and the only signal was that it never
  // changed.
  const { api, calls } = loader([ONE_ALERT, { ok: false, error: 'boom' }]);
  await api.load();
  assert.strictEqual(api.alerts().length, 1, 'the good load must plot the alert');
  assert.strictEqual(calls.renders, 1);

  await api.load();
  assert.deepStrictEqual(api.alerts(), [],
    'the stale alert survived a server error');
  assert.strictEqual(calls.renders, 2,
    'renderNwrAlerts is the only caller of clearNwrMarkers — the error path ' +
    'must reach it');
});

test('the other two failure paths still clear (unchanged)', async () => {
  const empty = { ok: true, active: [], alerts: [] };
  const { api, calls } = loader([ONE_ALERT, empty, new Error('offline')]);
  await api.load();
  await api.load();
  assert.deepStrictEqual(api.alerts(), []);
  await api.load();                    // fetch throws: feature not installed
  assert.deepStrictEqual(api.alerts(), []);
  assert.strictEqual(calls.renders, 3);
});
