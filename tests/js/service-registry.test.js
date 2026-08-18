'use strict';
// Unit tests for the shared service registry that keeps index.html and the OASIS
// Dashboard kiosk counting the same set of services.
const test = require('node:test');
const assert = require('node:assert');
const R = require('../../common/js/service-registry.js');

test('registry: services are well-formed and uniquely identified', () => {
  assert.ok(Array.isArray(R.OASIS_SERVICES), 'OASIS_SERVICES is an array');
  const ids = R.OASIS_SERVICES.map(s => s.id);
  assert.strictEqual(new Set(ids).size, ids.length, 'ids are unique');
  R.OASIS_SERVICES.forEach(s => {
    assert.ok(s.id && s.name, 'each service has id + name');
    assert.ok(s.gate === null || typeof s.gate === 'object', 'gate is null or object');
  });
});

test('resolveHidden: an unknown/null manifest counts everything (hides nothing)', async () => {
  const hidden = await R.oasisResolveHidden(null, async () => false);
  assert.strictEqual(hidden.size, 0);
});

test('resolveHidden: a matched feature keeps a service visible without probing', async () => {
  let probed = false;
  const hidden = await R.oasisResolveHidden(['graywolf'], async () => { probed = true; return false; });
  assert.ok(!hidden.has('gw'), 'graywolf feature → GrayWolf visible');
  assert.ok(!hidden.has('aprs'), 'graywolf feature → APRS visible');
});

test('resolveHidden: Pi snapshot hides only kiwix/nwr/wiki; a running-but-unlisted webssh stays counted', async () => {
  // webssh, kiwix, wikipedia are NOT in features on this box. OpenWebRX is not
  // in the registry at all any more (3.92.0) — it can be installed and running
  // and OASIS still shows no tile for it, so it can never appear here.
  const features = ['adsb', 'fcc', 'graywolf', 'gps-l76x', 'repeaterbook', 'rtl-sdr-feed', 'winlink'];
  const live = { webssh: true, gpsd: true, 'dump1090-fa': true, openwebrx: true, kiwix: false, 'aprs-sdr-feed': true };
  const probe = async (g) => !!live[g.unit];
  const hidden = await R.oasisResolveHidden(features, probe);
  assert.deepStrictEqual([...hidden].sort(), ['kiwix', 'nwr', 'wiki']);
  assert.ok(!R.OASIS_SERVICES.some(s => s.id === 'owrx'), 'no OpenWebRX tile in the registry');
  assert.ok(!hidden.has('webssh'), 'webssh enabled outside the manifest is still counted');
});

test('resolveHidden: gpsd requires a deliberate enable (enabledOnly), not merely active', async () => {
  // No gps feature; probe reports active-but-not-enabled for gpsd → still hidden.
  const probe = async (g) => (g.enabledOnly ? false : true);
  const hidden = await R.oasisResolveHidden([], probe);
  assert.ok(hidden.has('gpsd'), 'gpsd active-but-not-enabled stays hidden');
});

test('assertChecks: reports registry ids a page failed to implement', () => {
  const all = R.OASIS_SERVICES.map(s => s.id);
  assert.deepStrictEqual(R.oasisAssertChecks(all), [], 'complete page → nothing missing');
  assert.deepStrictEqual(R.oasisAssertChecks(all.filter(id => id !== 'adsb')), ['adsb']);
});

// Build a browse(path) => entries over an in-memory tree keyed by dir path.
// Each dir maps to its /api/browse-shaped entries; also tallies visited paths.
// The countPmtiles suites moved to tests/test_health_maps.py when the recursive
// .pmtiles walk moved server-side (GET /api/health/maps). The nested-layout and
// depth-cap cases they guarded are covered there against the real filesystem,
// which is a stronger test than the fake browse tree they used here.

// ── The RTL/SDR assignment dot ───────────────────────────────────────────────
// The bug: satellites showed AMBER whenever it was not mid-capture, i.e. almost
// always, on a station that was assigned, free and ready. Amber reads as "look
// at me" on a wall panel, so a healthy station asked for attention all day.
//
// Both pages had a byte-identical copy of the rule, so both were wrong together
// and neither could be used to check the other.
test('sdrDotClass: nothing assigned is hollow, whatever else is true', () => {
  assert.strictEqual(R.sdrDotClass({ assigned: false }), '');
  assert.strictEqual(R.sdrDotClass({ assigned: false, active: true, daemon: true }), '');
  assert.strictEqual(R.sdrDotClass({}), '');
  assert.strictEqual(R.sdrDotClass(), '', 'no argument must not throw');
});

test('sdrDotClass: a daemon service is green only while its unit runs', () => {
  const d = extra => R.sdrDotClass(Object.assign({ assigned: true, daemon: true }, extra));
  assert.strictEqual(d({ active: true }), 'inuse');
  assert.strictEqual(d({ active: false }), 'assigned', 'assigned but down stays amber');
  // A daemon whose unit we cannot probe must NOT drift to green — "we can't
  // tell" is not "it's working". OpenWebRX was the case that set this rule.
  assert.strictEqual(d({ active: false, blocked: false }), 'assigned');
});

test('sdrDotClass: satellites is green when assigned and free', () => {
  const s = extra => R.sdrDotClass(Object.assign({ assigned: true, daemon: false }, extra));
  // THE FIX: idle, dongle free, nothing recording — this is the ready state.
  assert.strictEqual(s({ active: false, blocked: false }), 'inuse');
  assert.strictEqual(s({ active: true }), 'inuse', 'recording is still green');
  // The one case that deserves amber: assigned, but someone else holds it, so
  // arming a pass right now would fail.
  assert.strictEqual(s({ active: false, blocked: true }), 'assigned');
  // Ours beats blocked — while WE record, busy is true and holder is us.
  assert.strictEqual(s({ active: true, blocked: true }), 'inuse');
});
