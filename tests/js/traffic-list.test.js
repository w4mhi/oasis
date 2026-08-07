'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const T = require('../../common/js/traffic-list.js');

const ROOT = path.join(__dirname, '..', '..');

test('lastHeardEpoch parses BOTH wire formats to the same instant', () => {
  // GrayWolf: space separator, nanoseconds, local offset. Aircraft: ISO Z.
  const gw = T.lastHeardEpoch('2026-08-06 16:24:54.604872593-07:00');
  const iso = T.lastHeardEpoch('2026-08-06T23:24:54.604Z');
  assert.strictEqual(gw, iso, 'the two formats must compare as the same instant');
  assert.ok(gw > 0);
  // Unparseable / missing sorts last, never NaN (NaN would poison the sort).
  for (const bad of [null, undefined, '', 'not a date']) {
    assert.strictEqual(T.lastHeardEpoch(bad), 0);
  }
});

test('stationSource trusts `via`, falls back to sniffing the path', () => {
  assert.strictEqual(T.stationSource({ via: 'rf' }), 'rf');
  assert.strictEqual(T.stationSource({ via: 'IS' }), 'is');
  // Unknown/absent via -> look for a q-construct or igate marker in the path.
  assert.strictEqual(T.stationSource({ path: ['TCPIP*', 'qAC', 'X'] }), 'is');
  assert.strictEqual(T.stationSource({ path: ['IGATE'] }), 'is');
  assert.strictEqual(T.stationSource({ path: ['qAR'] }), 'is');
  assert.strictEqual(T.stationSource({ path: ['WIDE1-1'] }), 'rf');
  // Missing everything must not throw — this runs per row, per poll.
  assert.strictEqual(T.stationSource({}), 'rf');
  assert.strictEqual(T.stationSource(null), 'rf');
});

test('sourceKey / sourceLabel classify all three receive paths', () => {
  assert.strictEqual(T.sourceKey({ _kind: 'aircraft' }), 'adsb');
  assert.strictEqual(T.sourceKey({ via: 'rf' }), 'rf');
  assert.strictEqual(T.sourceLabel({ _kind: 'aircraft' }), 'ADS-B');
  assert.strictEqual(T.sourceLabel({ via: 'rf' }), 'RF');
  assert.strictEqual(T.sourceLabel({ via: 'is' }), 'IS');
});

test('sortByRecency: newest first, stable, non-mutating', () => {
  const rows = [
    { callsign: 'OLD', last_heard: '2026-08-06T10:00:00.000Z' },
    { callsign: 'NEW', last_heard: '2026-08-06T12:00:00.000Z' },
    // same instant as NEW, expressed in the OTHER wire format
    { callsign: 'TIE', last_heard: '2026-08-06 05:00:00.000000-07:00' },
    { callsign: 'BAD', last_heard: 'garbage' }
  ];
  const copy = rows.slice();
  const out = T.sortByRecency(rows);
  assert.deepStrictEqual(out.map(r => r.callsign), ['NEW', 'TIE', 'OLD', 'BAD']);
  assert.deepStrictEqual(rows, copy, 'must not mutate the caller array');
  assert.notStrictEqual(out, rows, 'must return a new array');
});

test('courseHtml renders only when actually moving', () => {
  assert.strictEqual(T.courseHtml(null, 30), '');
  assert.strictEqual(T.courseHtml(45, 0), '', 'parked station: stale course stays blank');
  assert.strictEqual(T.courseHtml(45, undefined), '');
  const html = T.courseHtml(45, 30);
  assert.ok(html.includes('rotate(45deg)'));
  assert.ok(html.includes('NE 45'));
  // Compass wrap: 350 deg rounds to the N bucket, not off the end of the array.
  assert.ok(T.courseHtml(350, 10).includes('N 350'));
  assert.ok(!T.courseHtml(350, 10).includes('undefined'));
});

test('aircraftRows: live wins over history, unpositioned live dropped', () => {
  const rows = T.aircraftRows({
    now: 1000,
    live: [{ hex: 'aaa', flight: 'ASA1 ', seen: 10, lat: 47, lon: -122, gs: 100, alt_baro: 10000 }],
    recent: [
      { hex: 'aaa', ts: 500, lat: 1, lon: 1 },            // superseded by live
      { hex: 'bbb', ts: 900, lat: null, lon: null }        // history-only, kept
    ]
  });
  const byHex = Object.fromEntries(rows.map(r => [r.hex, r]));
  assert.strictEqual(rows.length, 2);
  // live wins: last_heard from now-seen (990), not the history ts (500)
  assert.strictEqual(T.lastHeardEpoch(byHex.aaa.last_heard), 990 * 1000);
  assert.strictEqual(byHex.aaa.callsign, 'ASA1', 'flight is trimmed');
  assert.strictEqual(byHex.aaa._positioned, true);
  assert.strictEqual(byHex.bbb._positioned, false, 'history-only unpositioned row is kept');

  // A LIVE aircraft with no fix is dropped (many of them, and they clutter).
  const dropped = T.aircraftRows({ now: 10, live: [{ hex: 'ccc', seen: 1 }], recent: [] });
  assert.strictEqual(dropped.length, 0);
});

test('aircraftRows: altitude guard and emergency squawks', () => {
  const mk = extra => T.aircraftRows({ now: 10, recent: [], live: [Object.assign({ hex: 'h', seen: 0, lat: 1, lon: 1 }, extra)] })[0];
  // alt_baro null/'ground' must stay null, NOT become 0 (Number(null) === 0 would
  // render a no-altitude aircraft as a red "0 ft").
  assert.strictEqual(mk({}).alt_m, null);
  assert.strictEqual(mk({ alt_baro: 'ground' }).alt_m, null);
  assert.strictEqual(mk({ alt_baro: 'bogus' }).alt_m, null);
  assert.ok(mk({ alt_baro: 32808 }).alt_m > 9999 && mk({ alt_baro: 32808 }).alt_m < 10001);
  // squawk shows; the three emergency codes get the extra warning bit
  assert.strictEqual(mk({ squawk: '1200' }).comment, 'sq 1200');
  for (const sq of T.EMERGENCY_SQUAWKS) {
    assert.ok(mk({ squawk: sq }).comment.includes('⚠ ' + sq), 'emergency squawk ' + sq);
  }
  // speed converts knots -> mph; missing gs is 0 (not null) so the cell stays blank
  assert.strictEqual(mk({ gs: 100 }).speed_mph, 115);
  assert.strictEqual(mk({}).speed_mph, 0);
});

test('altColorFor bands aircraft only, never APRS stations', () => {
  assert.strictEqual(T.altColorFor({ via: 'rf', alt_m: 100 }), null, 'ground elevation is not a flight level');
  assert.strictEqual(T.altColorFor({ _kind: 'aircraft', alt_m: null }), null);
  assert.ok(typeof T.altColorFor({ _kind: 'aircraft', alt_m: 3000 }) === 'string');
});

test('isUnlocated flags heard-but-unpositioned aircraft only', () => {
  assert.strictEqual(T.isUnlocated({ _kind: 'aircraft', _positioned: false }), true);
  assert.strictEqual(T.isUnlocated({ _kind: 'aircraft', _positioned: true }), false);
  assert.strictEqual(T.isUnlocated({ via: 'rf' }), false);
  assert.strictEqual(T.isUnlocated(null), false);
});

// The duplication this module exists to remove: neither page may keep a private
// copy of the merge/shape/sort logic, or they can silently diverge again.
test('neither page reimplements the shared list logic', () => {
  for (const rel of ['index.html', 'maps/traffic/map.html']) {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    assert.ok(!/function\s+_adsbForList\s*\(/.test(src), rel + ' reintroduced _adsbForList');
    assert.ok(!/function\s+_lhEpoch\s*\(/.test(src), rel + ' reintroduced _lhEpoch');
    assert.ok(!/function\s+(aprsSource|_stationSource)\s*\(/.test(src), rel + ' reintroduced a local source fn');
    assert.ok(src.includes('src="/common/js/traffic-list.js"'), rel + ' does not load traffic-list.js');
    // The API coalesces last_heard server-side; the dead fallback must not return.
    assert.ok(!src.includes('s.timestamp || s.time'), rel + ' reintroduced the dead timestamp fallback');
  }
});
