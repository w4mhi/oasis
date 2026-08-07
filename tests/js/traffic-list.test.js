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

// ── Tier 3: the shared filter engine ─────────────────────────────────────────

test('filters.age honours the cutoff and the exemption hook', () => {
  const F = T.filters;
  const now = Date.now();
  const fresh = { last_heard: new Date(now - 60 * 1000).toISOString() };
  const stale = { last_heard: new Date(now - 99 * 3600 * 1000).toISOString() };
  assert.strictEqual(F.age(0)(stale), true, '0 cutoff disables the filter entirely');
  assert.strictEqual(F.age(now - 30 * 60000)(fresh), true);
  assert.strictEqual(F.age(now - 30 * 60000)(stale), false);
  // The map keeps active hazard incidents on screen no matter how stale.
  assert.strictEqual(F.age(now - 30 * 60000, () => true)(stale), true);
  // Undated rows drop under a cutoff, but survive when there is none.
  assert.strictEqual(F.age(now - 30 * 60000)({}), false);
  assert.strictEqual(F.age(0)({}), true);
});

test('filters.callsign: prefix, wildcard, and metacharacter safety', () => {
  const F = T.filters;
  assert.strictEqual(F.callsign('')({ callsign: 'ANY' }), true, 'empty query matches all');
  assert.strictEqual(F.callsign('K7')({ callsign: 'K7ABC-9' }), true, 'prefix catches SSIDs');
  assert.strictEqual(F.callsign('k7')({ callsign: 'K7ABC' }), true, 'case-insensitive');
  assert.strictEqual(F.callsign('K7')({ callsign: 'N0CALL' }), false);
  assert.strictEqual(F.callsign('K7*9')({ callsign: 'K7ABC9' }), true);
  assert.strictEqual(F.callsign('K7*9')({ callsign: 'K7ABC8' }), false);
  assert.strictEqual(F.callsign('*')({ callsign: 'ANYTHING' }), true);
  // A stray regex metacharacter must not throw — it must simply match nothing.
  for (const q of ['K7(', 'K7[', 'K7\\', 'K7+*']) {
    assert.doesNotThrow(() => F.callsign(q)({ callsign: 'K7ABC' }), 'query ' + q);
  }
  assert.strictEqual(F.callsign('K7(')({ callsign: 'K7ABC' }), false);
  assert.strictEqual(F.callsign('K7')({}), false, 'missing callsign never matches a query');
});

test('filters.grid: field match and the No-fix bucket', () => {
  const F = T.filters;
  const gridOf = (lat, lon) => (lat == null ? '' : 'CN87ab');
  assert.strictEqual(F.grid('All', gridOf)({ lat: 1, lon: 1 }), true);
  assert.strictEqual(F.grid('CN87', gridOf)({ lat: 1, lon: 1 }), true);
  assert.strictEqual(F.grid('CN88', gridOf)({ lat: 1, lon: 1 }), false);
  assert.strictEqual(F.grid('__nofix', gridOf)({ lat: null, lon: null }), true);
  assert.strictEqual(F.grid('__nofix', gridOf)({ lat: 1, lon: 1 }), false);
});

test('filters.sources: empty AND complete selection both mean everything', () => {
  const F = T.filters;
  const rf = { via: 'rf' }, is = { via: 'is' }, ac = { _kind: 'aircraft' };
  for (const sel of [[], null, undefined, ['rf', 'is', 'adsb']]) {
    assert.strictEqual(F.sources(sel)(rf), true, JSON.stringify(sel));
    assert.strictEqual(F.sources(sel)(ac), true, JSON.stringify(sel));
  }
  assert.strictEqual(F.sources(['rf'])(rf), true);
  assert.strictEqual(F.sources(['rf'])(is), false);
  assert.strictEqual(F.sources(['rf'])(ac), false);
  // The combination the single-select dropdown cannot express.
  assert.strictEqual(F.sources(['rf', 'is'])(is), true);
  assert.strictEqual(F.sources(['rf', 'is'])(ac), false);
});

test('filters.all composes and ignores nulls', () => {
  const F = T.filters;
  const yes = () => true, no = () => false;
  assert.strictEqual(F.all(yes, null, yes)({}), true);
  assert.strictEqual(F.all(yes, no, yes)({}), false);
  assert.strictEqual(F.all()({}), true, 'no predicates = everything passes');
});

test('sourceBreakdown counts the cycle window and tracks the newest packet', () => {
  const now = 1000000;
  const at = ms => new Date(ms).toISOString();
  const b = T.sourceBreakdown({
    now, windowMs: 20000,
    stations: [
      { via: 'rf', last_heard: at(now - 5000) },     // in window
      { via: 'is', last_heard: at(now - 10000) },    // in window
      { via: 'rf', last_heard: at(now - 90000) }     // outside: newest, not counted
    ],
    aircraft: [{ _kind: 'aircraft', last_heard: at(now - 1000) }]
  });
  assert.deepStrictEqual([b.rf, b.is, b.adsb], [1, 1, 1]);
  assert.strictEqual(b.newest, now - 1000, 'newest spans stations AND aircraft');
  assert.strictEqual(b.freshText, 'just now');
  assert.strictEqual(b.tone, 'ok');
  // Freshness buckets: text at 2/60 min, tone at 30/360 min. Uses a realistic
  // wall-clock `now` — `now` above is only ~17 min after the epoch, so
  // subtracting an hour there would yield a pre-1970 instant.
  const NOW = Date.UTC(2026, 7, 6, 12, 0, 0);
  const tone = mins => T.sourceBreakdown({ now: NOW, stations: [{ via: 'rf', last_heard: new Date(NOW - mins * 60000).toISOString() }] });
  assert.strictEqual(tone(10).tone, 'ok');
  assert.strictEqual(tone(10).freshText, '10m ago');
  assert.strictEqual(tone(45).tone, 'warn');
  assert.strictEqual(tone(120).freshText, '2h ago');
  assert.strictEqual(tone(400).tone, 'stale');
  // Nothing heard at all -> no freshness, no tone (callers blank the element).
  const empty = T.sourceBreakdown({ now, stations: [], aircraft: [] });
  assert.deepStrictEqual([empty.rf, empty.is, empty.adsb, empty.newest], [0, 0, 0, 0]);
  assert.strictEqual(empty.freshText, '');
  assert.strictEqual(empty.tone, '');
});

test('neither page reimplements the filter engine or the breakdown', () => {
  for (const rel of ['index.html', 'maps/traffic/map.html']) {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    assert.ok(src.includes('OasisTrafficList.filters') || src.includes('OasisTrafficList.filters'.replace('.', '.')),
      rel + ' does not use the shared filter engine');
    assert.ok(src.includes('OasisTrafficList.sourceBreakdown'), rel + ' does not use the shared breakdown');
    assert.ok(!/function\s+_callMatches\s*\(/.test(src), rel + ' kept a local callsign matcher');
  }
});
