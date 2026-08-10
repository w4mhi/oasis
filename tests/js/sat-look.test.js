'use strict';
// Unit tests for common/js/sat-look.js — the look-angle/range readout shared by
// the Satellites page and the kiosk dashboard.
//
// The geometry test is deliberately self-referential in the useful way: it asks
// satellite.js for the sub-satellite point, stands the observer exactly there,
// and then asserts OUR wrapper reports the bird at the zenith and at a slant
// range equal to its altitude. That pins the observer/ECF/look-angle plumbing
// (the easy place to get a sign or a unit wrong) without pinning the orbit, so
// it cannot rot when the embedded TLE ages.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

// sat-look.js reads the `satellite` global at CALL time, exactly as the pages do
// (classic <script> after satellite.min.js), so loading it here is enough.
global.satellite = require(
  path.join(__dirname, '..', '..', 'services', 'satellites', 'static', 'satellite.min.js'));
const L = require('../../common/js/sat-look.js');

// A real ISS TLE. Only its FORM matters here — every assertion below is a
// geometric self-consistency check, not a claim about where the ISS was.
const L1 = '1 25544U 98067A   24001.50000000  .00016717  00000-0  30777-3 0  9993';
const L2 = '2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49814556 10000';
const WHEN = new Date(Date.UTC(2024, 0, 1, 12, 0, 0));

test('observer under the sub-point sees the bird at the zenith, one altitude away', () => {
  const pv = satellite.propagate(satellite.twoline2satrec(L1, L2), WHEN);
  const gd = satellite.eciToGeodetic(pv.position, satellite.gstime(WHEN));
  const station = { lat: satellite.degreesLat(gd.latitude), lon: satellite.degreesLong(gd.longitude) };

  const look = L.currentLook(L1, L2, station, WHEN);
  assert.ok(look, 'expected a look angle');
  assert.ok(look.el > 89.9, `elevation should be ~90 at the sub-point, got ${look.el}`);
  // Straight up means the slant range IS the altitude.
  assert.ok(Math.abs(look.rangeKm - gd.height) < 1,
            `range ${look.rangeKm} should match altitude ${gd.height}`);
  // Sanity: a LEO bird, not a decimal-point slip.
  assert.ok(look.rangeKm > 300 && look.rangeKm < 600, `LEO altitude expected, got ${look.rangeKm}`);
});

test('an observer on the far side of the planet is well below the horizon', () => {
  const pv = satellite.propagate(satellite.twoline2satrec(L1, L2), WHEN);
  const gd = satellite.eciToGeodetic(pv.position, satellite.gstime(WHEN));
  const lat = satellite.degreesLat(gd.latitude), lon = satellite.degreesLong(gd.longitude);
  const anti = { lat: -lat, lon: lon > 0 ? lon - 180 : lon + 180 };

  const look = L.currentLook(L1, L2, anti, WHEN);
  assert.ok(look.el < -50, `antipode should be far below the horizon, got ${look.el}`);
  // Below the horizon the range is still defined, and larger than at the zenith.
  assert.ok(look.rangeKm > 6000, `antipodal range should be huge, got ${look.rangeKm}`);
});

test('currentLook returns null rather than guessing when an input is missing', () => {
  const st = { lat: 35, lon: -80 };
  assert.strictEqual(L.currentLook(null, L2, st, WHEN), null);
  assert.strictEqual(L.currentLook(L1, null, st, WHEN), null);
  assert.strictEqual(L.currentLook(L1, L2, null, WHEN), null);
  assert.strictEqual(L.currentLook(L1, L2, { lat: null, lon: null }, WHEN), null);
  assert.strictEqual(L.currentLook('garbage', 'garbage', st, WHEN), null);
});

test('fmtKm groups thousands with a space, never a locale separator', () => {
  assert.strictEqual(L.fmtKm(0), '0 km');
  assert.strictEqual(L.fmtKm(42), '42 km');
  assert.strictEqual(L.fmtKm(999), '999 km');
  assert.strictEqual(L.fmtKm(1000), '1 000 km');
  assert.strictEqual(L.fmtKm(1240.4), '1 240 km');
  assert.strictEqual(L.fmtKm(35786), '35 786 km');
  assert.strictEqual(L.fmtKm(1234567), '1 234 567 km');
  // A comma would read as a decimal point to half the planet.
  assert.ok(!L.fmtKm(1240).includes(','));
});

test('fmtKm says "unknown" rather than NaN when there is nothing to show', () => {
  assert.strictEqual(L.fmtKm(null), '—');
  assert.strictEqual(L.fmtKm(undefined), '—');
  assert.strictEqual(L.fmtKm(NaN), '—');
  assert.strictEqual(L.fmtKm(Infinity), '—');
});

test('elevTrend: the first sample of a bird admits it does not know', () => {
  L.resetTrend();
  assert.strictEqual(L.elevTrend('first', 10), 0);
  assert.strictEqual(L.trendArrow(0), '');
});

test('elevTrend: climbing and falling, per bird', () => {
  L.resetTrend();
  L.elevTrend('a', 10);
  assert.strictEqual(L.elevTrend('a', 12), 1);
  assert.strictEqual(L.trendArrow(1), '↗');
  assert.strictEqual(L.elevTrend('a', 40), 1);
  assert.strictEqual(L.elevTrend('a', 38), -1);
  assert.strictEqual(L.trendArrow(-1), '↘');
  // A second bird keeps its own history — the bug being fixed was one arrow for
  // every row.
  L.elevTrend('b', 80);
  assert.strictEqual(L.elevTrend('b', 60), -1);
  assert.strictEqual(L.elevTrend('a', 39), 1);
});

test('elevTrend: a flat sample holds the last direction instead of flapping', () => {
  L.resetTrend();
  L.elevTrend('c', 10);
  assert.strictEqual(L.elevTrend('c', 20), 1);
  assert.strictEqual(L.elevTrend('c', 20), 1);      // paused tab / culmination
  assert.strictEqual(L.elevTrend('c', 19), -1);
  assert.strictEqual(L.elevTrend('c', 19), -1);
});

test('elevTrend: a null elevation forgets the bird, so a new pass starts clean', () => {
  L.resetTrend();
  L.elevTrend('d', 30);
  assert.strictEqual(L.elevTrend('d', 40), 1);
  assert.strictEqual(L.elevTrend('d', null), 0);    // set, or TLE went missing
  assert.strictEqual(L.elevTrend('d', 5), 0);       // risen again: no stale arrow
  assert.strictEqual(L.elevTrend('d', 7), 1);
});

test('a real pass: the arrow flips once, at culmination', () => {
  L.resetTrend();
  // Walk a minute at a time and find a stretch where the bird is up, then check
  // the arrow agrees with the elevation actually changing.
  const station = (() => {
    const pv = satellite.propagate(satellite.twoline2satrec(L1, L2), WHEN);
    const gd = satellite.eciToGeodetic(pv.position, satellite.gstime(WHEN));
    return { lat: satellite.degreesLat(gd.latitude), lon: satellite.degreesLong(gd.longitude) };
  })();
  let prev = null, flips = 0, samples = 0;
  for (let m = -6; m <= 6; m++) {
    const look = L.currentLook(L1, L2, station, new Date(WHEN.getTime() + m * 60000));
    if (!look || look.el < 0) continue;
    const dir = L.elevTrend('pass', look.el);
    if (prev !== null && dir !== 0 && dir !== prev) flips++;
    if (dir !== 0) prev = dir;
    samples++;
  }
  assert.ok(samples > 4, `expected a usable pass, got ${samples} samples`);
  assert.strictEqual(flips, 1, 'a single pass climbs then falls exactly once');
});
