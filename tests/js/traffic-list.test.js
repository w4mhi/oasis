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

// The display half of the same "lost aircraft, emptied record" bug the recorder
// had: dump1090 publishes each field only while its own timer says it is fresh,
// so a plane fading out is still listed live with alt, then speed/track, then
// the position dropping away one by one. Taking the live record wholesale wrote
// those holes over values history still knew.
test('aircraftRows: a fading live frame does not blank what history knows', () => {
  const row = T.aircraftRows({
    now: 1000,
    recent: [{ hex: 'aaa', flight: 'ASA477', ts: 900, lat: 34.1, lon: -84.5,
               alt_baro: 35000, gs: 450, track: 270, squawk: '1200',
               category: 'A3', baro_rate: -640 }],
    // Still heard — but the server emits every key, null when unknown.
    live: [{ hex: 'aaa', flight: 'ASA477', seen: 0, lat: 34.2, lon: -84.6,
             alt_baro: null, gs: null, track: null, squawk: null,
             category: null, baro_rate: null }]
  })[0];
  assert.strictEqual(row.speed_mph, 518, 'speed carried forward from history');
  assert.strictEqual(row.course, 270, 'track carried forward');
  assert.strictEqual(row.category, 'A3');
  assert.strictEqual(row.baro_rate, -640);
  assert.strictEqual(row.comment, 'sq 1200', 'squawk carried forward');
  assert.ok(row.alt_m > 10667 && row.alt_m < 10669, 'altitude carried forward');
  // The LIVE position still wins — carry-forward fills gaps, it does not
  // outrank a value the aircraft actually just sent.
  assert.strictEqual(row.lat, 34.2);
  assert.strictEqual(row.lon, -84.6);
  assert.strictEqual(T.lastHeardEpoch(row.last_heard), 1000 * 1000,
    'still heard now: the age comes from the live frame');
});

test('aircraftRows: a still-heard aircraft keeps its last known position', () => {
  // Position expires while the aircraft is still transmitting. The filter drops
  // a LIVE row with no position, so without carry-forward the plane vanished
  // from the list outright instead of ageing like every other row.
  const rows = T.aircraftRows({
    now: 1000,
    recent: [{ hex: 'aaa', ts: 900, lat: 34.1, lon: -84.5, alt_baro: 35000 }],
    live: [{ hex: 'aaa', seen: 0, lat: null, lon: null }]
  });
  assert.strictEqual(rows.length, 1, 'the aircraft must not vanish');
  assert.strictEqual(rows[0].lat, 34.1);
  assert.strictEqual(rows[0].lon, -84.5);
  assert.strictEqual(rows[0]._positioned, true);

  // lat/lon are ONE datum: a half-position keeps the previous PAIR rather than
  // mixing a live lat with a stale lon, which would place it somewhere it has
  // never been.
  const half = T.aircraftRows({
    now: 1000,
    recent: [{ hex: 'bbb', ts: 900, lat: 10, lon: 20 }],
    live: [{ hex: 'bbb', seen: 0, lat: 11, lon: null }]
  })[0];
  assert.deepStrictEqual([half.lat, half.lon], [10, 20]);

  // A Mode-S-only aircraft has no position ANYWHERE, so it is still dropped —
  // carry-forward must not resurrect the clutter the filter exists to remove.
  const modeS = T.aircraftRows({
    now: 1000, recent: [{ hex: 'ccc', ts: 900, lat: null, lon: null }],
    live: [{ hex: 'ccc', seen: 0 }]
  });
  assert.strictEqual(modeS.length, 0, 'no position anywhere: still dropped');
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

// A row whose timestamp cannot be parsed must degrade to "unknown age", NOT
// throw. new Date(NaN).toISOString() raises RangeError, and this runs inside a
// .map(), so one bad aircraft used to abort the whole fold — taking the APRS
// stations down with it, because the caller never reached its render. The kiosk
// carried a copy of this function without the guard until it was deleted in
// favour of this one; the test is here so the guard cannot be "simplified" away.
test('aircraftRows: an unparseable timestamp yields a null last_heard, never a throw', () => {
  for (const bad of [{ last_seen: 'not a date' }, { ts: NaN }]) {
    const row = T.aircraftRows({
      now: 10, recent: [], live: [Object.assign({ hex: 'h', lat: 1, lon: 1 }, bad)]
    })[0];
    assert.ok(row, 'the row must still be produced: ' + JSON.stringify(bad));
    assert.strictEqual(row.last_heard, null, JSON.stringify(bad));
    // And it must stay harmless downstream, which is where the damage was.
    assert.strictEqual(T.lastHeardEpoch(row.last_heard), 0);
  }
  // An EMPTY last_seen is a DIFFERENT case and must not be lumped in with the
  // unparseable one: it is falsy, so it falls through to the `now` fallback and
  // the row gets a real timestamp. Asserting null here would have been asserting
  // a bug into place.
  const empty = T.aircraftRows({ now: 10, recent: [], live: [{ hex: 'e', lat: 1, lon: 1, last_seen: '' }] })[0];
  assert.strictEqual(T.lastHeardEpoch(empty.last_heard), 10 * 1000);
  // The good case still resolves, so the guard is not swallowing everything.
  const ok = T.aircraftRows({ now: 10, recent: [], live: [{ hex: 'g', lat: 1, lon: 1, seen: 0 }] })[0];
  assert.strictEqual(T.lastHeardEpoch(ok.last_heard), 10 * 1000);
});

// The detail-card fields. The kiosk's aircraft sheet renders the category label,
// the climb/descend arrow and the emergency banner off these four; they are the
// only reason it used to fold ADS-B itself. Dropping one would blank part of that
// sheet with no error anywhere — so pin them.
test('aircraftRows: detail fields pass through for callers that render a card', () => {
  const row = T.aircraftRows({ now: 10, recent: [], live: [{
    hex: 'abc123', lat: 1, lon: 1, seen: 0,
    category: 'A7', baro_rate: -640, squawk: '7700', emergency: 'general'
  }] })[0];
  assert.strictEqual(row.category, 'A7');
  assert.strictEqual(row.baro_rate, -640);
  assert.strictEqual(row.squawk, '7700');
  assert.strictEqual(row.emergency, 'general');
  assert.strictEqual(row.hex, 'abc123');
  // Absent stays undefined rather than becoming a misleading 0 / '' — the sheet
  // tests `baro_rate != null` to decide whether to show a vertical-rate arrow.
  const bare = T.aircraftRows({ now: 10, recent: [], live: [{ hex: 'h', lat: 1, lon: 1, seen: 0 }] })[0];
  assert.strictEqual(bare.baro_rate, undefined);
  assert.strictEqual(bare.emergency, undefined);
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

// THE KIOSK WAS MISSING FROM THIS LIST, which is the whole reason it kept a
// private copy of the ADS-B fold long enough to drift from the shared one. A
// guard that names its subjects one by one is only as good as the list, so when
// a third screen showing the same traffic appeared, the guard silently stopped
// covering the codebase while continuing to pass.
const TRAFFIC_PAGES = ['index.html', 'maps/traffic/map.html', 'oasis-dashboard/dashboard.html'];

test('no page reimplements the filter engine, the fold, or the breakdown', () => {
  for (const rel of TRAFFIC_PAGES) {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    assert.ok(src.includes('OasisTrafficList.filters'),
      rel + ' does not use the shared filter engine');
    assert.ok(src.includes('OasisTrafficList.aircraftRows'),
      rel + ' does not use the shared ADS-B fold');
    assert.ok(!/function\s+_callMatches\s*\(/.test(src), rel + ' kept a local callsign matcher');
    // The fold specifically: a local live+history merge keyed by hex is what the
    // kiosk had. Matching the FUNCTION NAME is deliberately narrow — this is a
    // reminder at review time, not a parser.
    assert.ok(!/function\s+_adsbForList\s*\(/.test(src),
      rel + ' kept a local ADS-B merge (_adsbForList) — call OasisTrafficList.aircraftRows');
  }
  // The breakdown is index + map only: the kiosk shows flow METERS (packet rates
  // off the receivers), which is a different readout, not this one under another
  // name. Asserting it everywhere would push the kiosk to adopt a number it has
  // no place to put.
  for (const rel of ['index.html', 'maps/traffic/map.html']) {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    assert.ok(src.includes('OasisTrafficList.sourceBreakdown'), rel + ' does not use the shared breakdown');
  }
});

test('sourceBreakdown is anchored to the snapshot, not to render time', () => {
  // The regression: callers re-render far more often than they re-poll (the map
  // redraws the drawer every 2s on the ADS-B poll while stations refresh every
  // 15s). With a wall-clock window the SAME snapshot yielded ever-smaller counts
  // between polls, so the breakdown disagreed with every other "heard" readout.
  const poll = Date.UTC(2026, 7, 6, 12, 0, 0);
  const stations = [];
  for (let i = 0; i < 13; i++) {
    stations.push({ via: i < 2 ? 'rf' : 'is',
                    last_heard: new Date(poll - i * 1100).toISOString() });
  }
  const at = now => {
    const b = T.sourceBreakdown({ stations, now, windowMs: 20000 });
    return b.rf + b.is;
  };
  // Anchored to the poll, the count is stable no matter when we render.
  assert.strictEqual(at(poll), 13);
  assert.strictEqual(at(poll), 13, 're-render with the same anchor must not change');
  // Advancing the anchor (i.e. a genuinely newer snapshot) is what may drop rows.
  assert.ok(at(poll + 14000) < 13, 'a later anchor legitimately ages rows out');
});

test('sourceBreakdown ages aircraft against their own poll', () => {
  // Stations and aircraft are fetched on different cadences; one shared clock
  // would age the fresher snapshot against the staler one's timestamp.
  const stationPoll = Date.UTC(2026, 7, 6, 12, 0, 0);
  const aircraftPoll = stationPoll + 14000;          // aircraft polled much later
  const b = T.sourceBreakdown({
    now: stationPoll,
    aircraftNow: aircraftPoll,
    windowMs: 20000,
    stations: [{ via: 'rf', last_heard: new Date(stationPoll - 5000).toISOString() }],
    aircraft: [{ _kind: 'aircraft', last_heard: new Date(aircraftPoll - 1000).toISOString() }]
  });
  assert.strictEqual(b.rf, 1);
  assert.strictEqual(b.adsb, 1, 'a 1s-old aircraft must count as heard');
  // aircraftNow defaults to now, so single-fetch callers need not pass it.
  const single = T.sourceBreakdown({
    now: stationPoll, windowMs: 20000, stations: [],
    aircraft: [{ _kind: 'aircraft', last_heard: new Date(stationPoll - 1000).toISOString() }]
  });
  assert.strictEqual(single.adsb, 1);
});

test('both pages anchor the breakdown to the fetch instant', () => {
  for (const rel of ['index.html', 'maps/traffic/map.html']) {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    assert.ok(src.includes('_aprsFetchedAtMs'), rel + ' does not record the fetch instant');
    assert.ok(/now:\s*_aprsFetchedAtMs/.test(src), rel + ' does not anchor the breakdown to it');
  }
});

// /api/adsb/aircraft resolves dump1090's clock server-side and sends an absolute
// `last_seen`, so the client no longer subtracts `now - seen`. The old path stays
// only for /api/adsb/recent, which has not been migrated yet.
test('aircraftRows: prefers the server-resolved last_seen over now - seen', () => {
  const iso = '2025-07-31T22:12:38Z';
  const rows = T.aircraftRows({
    now: 999999,                       // a deliberately wrong client clock
    recent: [],
    live: [{ hex: 'aaa', seen: 42, last_seen: iso, lat: 1, lon: 1 }],
  });
  assert.strictEqual(rows.length, 1);
  assert.strictEqual(new Date(rows[0].last_heard).toISOString().replace('.000', ''), iso);
});

test('aircraftRows: history rows use last_seen too — one path, no epoch ts', () => {
  // /api/adsb/recent now sends the same schema as /aircraft.
  const iso = '2025-07-31T21:13:20Z';
  const rows = T.aircraftRows({
    now: 999999, live: [],
    recent: [{ hex: 'bbb', last_seen: iso, lat: 1, lon: 1 }],
  });
  assert.strictEqual(new Date(rows[0].last_heard).toISOString().replace('.000', ''), iso);
});

test('aircraftRows: still degrades sanely for a record with neither field', () => {
  const rows = T.aircraftRows({
    now: 1000, recent: [], live: [{ hex: 'ccc', seen: 100, lat: 1, lon: 1 }],
  });
  assert.strictEqual(Math.round(new Date(rows[0].last_heard).getTime() / 1000), 900);
});

// Regression: a record with an unusable timestamp used to throw RangeError from
// new Date(NaN).toISOString() inside the .map(), aborting aircraftRows entirely.
// Because the map page builds its list as APRS-rows CONCAT aircraft-rows, that
// one bad record blanked the whole list — APRS stations included. Triggered live
// by a browser running a cached pre-migration traffic-list.js (which read the
// removed `ts` field) against the migrated API.
test('aircraftRows: a bad timestamp yields null last_heard, never a throw', () => {
  const cases = [
    { hex: 'a', lat: 1, lon: 1 },                                  // nothing at all
    { hex: 'b', lat: 1, lon: 1, ts: undefined },                   // the exact skew case
    { hex: 'c', lat: 1, lon: 1, last_seen: 'not-a-date' },
    { hex: 'd', lat: 1, lon: 1, last_seen: '' },
    { hex: 'e', lat: 1, lon: 1, ts: NaN },
    { hex: 'f', lat: 1, lon: 1, ts: Infinity },
    { hex: 'g', lat: 1, lon: 1, ts: 1e18 },                        // out of Date range
  ];
  let rows;
  assert.doesNotThrow(() => { rows = T.aircraftRows({ live: cases, recent: [], now: NaN }); });
  assert.strictEqual(rows.length, cases.length);
  for (const r of rows) {
    assert.ok(r.last_heard === null || !isNaN(Date.parse(r.last_heard)),
      `last_heard must be null or valid, got ${r.last_heard}`);
  }
});

test('aircraftRows: one bad record does not lose the good ones', () => {
  const rows = T.aircraftRows({
    now: 1000, recent: [],
    live: [{ hex: 'bad', lat: 1, lon: 1, last_seen: 'garbage' },
           { hex: 'good', lat: 2, lon: 2, last_seen: '2025-07-31T22:12:38Z' }],
  });
  assert.strictEqual(rows.length, 2);
  assert.ok(rows.find(r => r.hex === 'good').last_heard.startsWith('2025-07-31'));
});

// lastHeardEpoch runs per row on every render (~1600 rows on a busy station), so
// it takes a fast path for the ISO-8601 UTC form the API now emits everywhere.
// The slow path must still handle GrayWolf's native Go format from an older
// daemon build, and both must agree to the second.
test('lastHeardEpoch: fast and slow paths agree', () => {
  const iso = '2026-08-08T03:09:56Z';
  const go  = '2026-08-07 20:09:56.45893926-07:00';   // same instant, -07:00
  assert.strictEqual(T.lastHeardEpoch(iso), Date.parse(iso));
  // To the SECOND: the normalised ISO form drops GrayWolf's sub-second noise
  // (.45893926), so the slow path legitimately lands 458 ms later.
  assert.strictEqual(Math.floor(T.lastHeardEpoch(go) / 1000),
                     Math.floor(Date.parse(iso) / 1000));
});

test('lastHeardEpoch: junk and empties are 0, never NaN', () => {
  for (const bad of [null, undefined, '', 'not-a-date', 'Z', 0, false]) {
    const v = T.lastHeardEpoch(bad);
    assert.strictEqual(v, 0, `${JSON.stringify(bad)} -> ${v}`);
  }
});

test('lastHeardEpoch: a space-separated value never takes the fast path', () => {
  // Ends in Z but is not ISO — must fall through, not mis-parse.
  const v = T.lastHeardEpoch('2026-08-08 03:09:56Z');
  assert.ok(v > 0 && !isNaN(v));
});

// ── plottedRecent: the map's "APRS N plotted" card ───────────────────────────
// The card sits under the RF/IS chips and used to build its own list straight
// from the station array — position + age only. The chips gated the MAP but not
// the card, so an IS station stayed listed (blue "IS" tag and all) after IS was
// switched off. The predicate is now an input, so card and map cannot disagree.
test('plottedRecent: position + cycle window, and it obeys the map predicate', () => {
  const now = Date.parse('2026-08-15T12:00:00Z');
  const at = ms => new Date(now - ms).toISOString();
  const rows = [
    { callsign: 'RF-NOW',  via: 'rf', lat: 1, lon: 2, last_heard: at(5000) },
    { callsign: 'IS-NOW',  via: 'is', lat: 1, lon: 2, last_heard: at(5000) },
    { callsign: 'NO-FIX',  via: 'rf', lat: null, lon: null, last_heard: at(5000) },
    { callsign: 'RF-OLD',  via: 'rf', lat: 1, lon: 2, last_heard: at(90000) },
    { callsign: 'NO-TIME', via: 'rf', lat: 1, lon: 2 }
  ];
  const opts = { now, windowSec: 30 };
  // No predicate: position + heard-this-cycle only.
  assert.deepStrictEqual(
    T.plottedRecent(rows, opts).map(r => r.callsign), ['RF-NOW', 'IS-NOW']);
  // IS switched off on the map -> the card must drop IS-NOW too.
  const rfOnly = T.filters.sources(['rf']);
  assert.deepStrictEqual(
    T.plottedRecent(rows, Object.assign({ pred: rfOnly }, opts)).map(r => r.callsign),
    ['RF-NOW']);
  // A predicate that rejects everything empties the card rather than throwing.
  assert.deepStrictEqual(T.plottedRecent(rows, Object.assign({ pred: () => false }, opts)), []);
  // Junk in must not throw — this runs on every repaint.
  assert.deepStrictEqual(T.plottedRecent(null, opts), []);
  assert.deepStrictEqual(T.plottedRecent([null, undefined], opts), []);
});

// `now` is the FETCH instant, not render-time wall clock: the card repaints on
// every filter toggle, and a moving clock over a fixed snapshot would empty it
// a few seconds after the poll for no reason at all.
test('plottedRecent: the window is anchored to the snapshot, not to render time', () => {
  const fetchedAt = Date.parse('2026-08-15T12:00:00Z');
  const rows = [{ callsign: 'RF', via: 'rf', lat: 1, lon: 2,
                  last_heard: new Date(fetchedAt - 8000).toISOString() }];
  assert.strictEqual(T.plottedRecent(rows, { now: fetchedAt, windowSec: 15 }).length, 1);
  // Same data, "rendered" a minute later — still one row, because `now` is fixed.
  assert.strictEqual(T.plottedRecent(rows, { now: fetchedAt, windowSec: 15 }).length, 1);
  // But a caller that anchors to a later clock legitimately drops it.
  assert.strictEqual(T.plottedRecent(rows, { now: fetchedAt + 60000, windowSec: 15 }).length, 0);
});
