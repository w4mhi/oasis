'use strict';
// Unit tests for satgeo.dopplerAt — reading a Doppler factor out of a /track
// response at an arbitrary wall-clock instant.
//
// What matters here is that the function never invents a number. The readout it
// feeds is a frequency an operator dials a rig to, so a plausible-looking zero
// from a track that carries no factor at all would be worse than a blank line:
// zero Doppler is a real, meaningful reading (it happens at TCA), and nothing
// downstream could tell the two apart.
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const g = require(path.join(__dirname, '..', '..', 'services', 'satellites',
                            'static', 'sat-geometry.js'));

// A three-sample track 10 s apart, factors chosen so the arithmetic is checkable
// by eye: +1e-5 approaching, 0 at closest approach, -1e-5 receding.
const T0 = '2026-08-12T12:00:00+00:00';
const T1 = '2026-08-12T12:00:10+00:00';
const T2 = '2026-08-12T12:00:20+00:00';
const TRACK = [{ t: T0, factor: 1e-5 }, { t: T1, factor: 0 }, { t: T2, factor: -1e-5 }];
const ms = (iso) => Date.parse(iso);

test('no usable track yields null, never a zero factor', () => {
  assert.strictEqual(g.dopplerAt(null, ms(T1)), null);
  assert.strictEqual(g.dopplerAt([], ms(T1)), null);
  assert.strictEqual(g.dopplerAt([TRACK[0]], ms(T1)), null, 'one sample cannot interpolate');
});

test('a track from before `factor` existed yields null', () => {
  // The exact shape /track served until the factor landed: doppler_hz only.
  const old = [{ t: T0, doppler_hz: 4321 }, { t: T2, doppler_hz: -4321 }];
  assert.strictEqual(g.dopplerAt(old, ms(T1)), null);
});

test('unparseable timestamps yield null rather than NaN', () => {
  const bad = [{ t: 'soon', factor: 1e-5 }, { t: 'later', factor: -1e-5 }];
  assert.strictEqual(g.dopplerAt(bad, ms(T1)), null);
});

test('before AOS clamps to the first sample and says so', () => {
  const d = g.dopplerAt(TRACK, ms(T0) - 60_000);
  assert.strictEqual(d.phase, 'before');
  assert.strictEqual(d.factor, 1e-5);
});

test('after LOS clamps to the last sample and says so', () => {
  const d = g.dopplerAt(TRACK, ms(T2) + 60_000);
  assert.strictEqual(d.phase, 'after');
  assert.strictEqual(d.factor, -1e-5);
});

test('the track boundaries themselves are not "live"', () => {
  // A pass that has exactly ended must not read as in progress for one tick.
  assert.strictEqual(g.dopplerAt(TRACK, ms(T0)).phase, 'before');
  assert.strictEqual(g.dopplerAt(TRACK, ms(T2)).phase, 'after');
});

test('an instant on a sample returns that sample', () => {
  const d = g.dopplerAt(TRACK, ms(T1));
  assert.strictEqual(d.phase, 'live');
  assert.strictEqual(d.factor, 0);
});

test('between samples it interpolates linearly', () => {
  const quarter = g.dopplerAt(TRACK, ms(T0) + 2500);   // 25% of the first 10 s step
  assert.strictEqual(quarter.phase, 'live');
  assert.ok(Math.abs(quarter.factor - 0.75e-5) < 1e-12, `got ${quarter.factor}`);

  const half = g.dopplerAt(TRACK, ms(T1) + 5000);      // midpoint of the second step
  assert.ok(Math.abs(half.factor - -0.5e-5) < 1e-12, `got ${half.factor}`);
});

test('the factor is dimensionless — one sample serves both bands', () => {
  // The whole point of shipping a factor instead of a frequency: /track used to
  // compute Doppler for downlinks[0], so a bird with 2 m and 70 cm downlinks got
  // a number ~3x wrong on whichever one the operator did not arm.
  const { factor } = g.dopplerAt(TRACK, ms(T0) + 1000);
  const vhf = factor * 145.8e6, uhf = factor * 437.8e6;
  assert.ok(vhf > 0 && uhf > 0, 'approaching → both shifted up');
  assert.ok(Math.abs(uhf / vhf - 437.8 / 145.8) < 1e-9, 'shift scales with the carrier');
});

test('sign convention: up while approaching, down while receding', () => {
  assert.ok(g.dopplerAt(TRACK, ms(T0) + 1000).factor > 0);
  assert.ok(g.dopplerAt(TRACK, ms(T2) - 1000).factor < 0);
});

test('a realistic LEO factor lands in the expected kHz range', () => {
  // ~7 km/s radial at AOS is about the most a LEO pass offers.
  const f = 7.0 / 299792.458;
  assert.ok(Math.abs(f * 437.8e6 - 10_200) < 400, '70 cm AOS shift is ~10 kHz');
  assert.ok(Math.abs(f * 145.8e6 - 3_400) < 200, '2 m AOS shift is ~3.4 kHz');
});
