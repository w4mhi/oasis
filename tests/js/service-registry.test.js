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

test('resolveHidden: Pi snapshot hides only kiwix/owrx/wiki; a running-but-unlisted webssh stays counted', async () => {
  // webssh, kiwix, openwebrx, wikipedia are NOT in features on this box
  const features = ['adsb', 'fcc', 'graywolf', 'gps-l76x', 'repeaterbook', 'rtl-sdr-feed', 'winlink'];
  const live = { webssh: true, gpsd: true, 'dump1090-fa': true, openwebrx: false, kiwix: false, 'aprs-sdr-feed': true };
  const probe = async (g) => !!live[g.unit];
  const hidden = await R.oasisResolveHidden(features, probe);
  assert.deepStrictEqual([...hidden].sort(), ['kiwix', 'owrx', 'wiki']);
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

// Build a getMaps() over a canned /api/maps payload.
function fakeMaps(payload) {
  return async () => payload;
}

test('countMaps: counts the GrayWolf archives /api/maps reports', async () => {
  // The real-Pi case the old /api/browse crawl of maps/ could never see: the
  // tiles live in GrayWolf's store, outside SUITE_ROOT → permanent false WARN.
  const inv = { present: ['washington', 'oregon'], source: 'graywolf',
                graywolf_dir: '/var/lib/graywolf/tiles/state', have_maps: true };
  assert.strictEqual(await R.oasisCountMaps(fakeMaps(inv)), 2);
});

test('countMaps: an empty GrayWolf store counts zero (legit WARN)', async () => {
  const inv = { present: [], source: 'graywolf', have_maps: false };
  assert.strictEqual(await R.oasisCountMaps(fakeMaps(inv)), 0);
});

test('countMaps: falls back to have_maps when present is absent', async () => {
  assert.strictEqual(await R.oasisCountMaps(fakeMaps({ have_maps: true })), 1);
  assert.strictEqual(await R.oasisCountMaps(fakeMaps({ have_maps: false })), 0);
  assert.strictEqual(await R.oasisCountMaps(fakeMaps(null)), 0);
});

test('countMaps: a failing inventory fetch propagates (page shows the error state)', async () => {
  await assert.rejects(R.oasisCountMaps(async () => { throw new Error('maps'); }));
});
