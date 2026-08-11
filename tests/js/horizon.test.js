'use strict';
// Unit tests for common/js/horizon.js — the azimuth-dependent horizon mask.
//
// The mask MARKS passes, it never filters them: a filter deletes the pass, and a
// slightly wrong mask would then silently remove workable passes with nothing on
// screen to say why. So these tests care about the curve being right and never
// producing NaN — a NaN floor compares false and would silently unmark
// everything.
const test = require('node:test');
const assert = require('node:assert');
const H = require('../../common/js/horizon.js');

test('sixteen sectors, ascending from north', () => {
  assert.strictEqual(H.SECTORS.length, 16);
  assert.strictEqual(H.SECTORS[0].name, 'N');
  assert.strictEqual(H.SECTORS[0].az, 0);
  assert.strictEqual(H.SECTORS[1].name, 'NNE');
  assert.strictEqual(H.SECTORS[4].name, 'E');
  assert.strictEqual(H.SECTORS[4].az, 90);
  assert.strictEqual(H.SECTORS[12].name, 'W');
  assert.strictEqual(H.SECTORS[12].az, 270);
});

test('an empty horizon is min_elev everywhere', () => {
  for (const az of [0, 45, 123.4, 270, 359.9]) {
    assert.strictEqual(H.floorAt({}, az, 10), 10);
  }
});

test('an absent horizon is min_elev, not a throw', () => {
  assert.strictEqual(H.floorAt(null, 90, 10), 10);
  assert.strictEqual(H.floorAt(undefined, 90, 10), 10);
});

test('a sector centre returns its own value', () => {
  assert.strictEqual(H.floorAt({ N: 25 }, 0, 10), 25);
  assert.strictEqual(H.floorAt({ E: 8 }, 90, 10), 8);
});

test('between two centres the floor interpolates', () => {
  // NNE is 22.5 degrees. Halfway between N(20) and NNE(30) is 25.
  const h = { N: 20, NNE: 30 };
  assert.strictEqual(H.floorAt(h, 11.25, 10), 25);
});

test('interpolation wraps the SHORT way across north', () => {
  // NNW is 337.5. Halfway to N at 348.75 must be 25, not a long way round
  // through the south.
  const h = { NNW: 20, N: 30 };
  assert.strictEqual(H.floorAt(h, 348.75, 10), 25);
});

test('a partial mask is legal and only raises the floor near its sector', () => {
  const h = { N: 25 };
  assert.strictEqual(H.floorAt(h, 0, 10), 25);      // its own centre
  assert.strictEqual(H.floorAt(h, 180, 10), 10);    // opposite: untouched
  const east = H.floorAt(h, 90, 10);
  assert.strictEqual(east, 10);                     // two sectors away: min_elev
});

test('azimuth outside 0-360 is normalised, not clamped', () => {
  assert.strictEqual(H.floorAt({ N: 25 }, 360, 10), 25);
  assert.strictEqual(H.floorAt({ N: 25 }, -360, 10), 25);
  assert.strictEqual(H.floorAt({ E: 8 }, 450, 10), 8);
});

test('junk values never produce a NaN floor', () => {
  // A NaN floor compares false against every elevation and would silently unmark
  // every pass — the mask would look like it was working and be doing nothing.
  for (const bad of [{ N: 'tall' }, { N: null }, { N: NaN }, { NOPE: 25 }]) {
    const v = H.floorAt(bad, 0, 10);
    assert.ok(Number.isFinite(v), JSON.stringify(bad) + ' -> ' + v);
  }
  assert.ok(Number.isFinite(H.floorAt({ N: 25 }, NaN, 10)));
});

test('a junk min_elev never produces a NaN floor either', () => {
  // Same failure mode as the horizon values above, but for the fallback itself:
  // a NaN floor compares false against every elevation, so a corrupt min_elev
  // would silently mark nothing while the mask looked like it was working.
  assert.ok(Number.isFinite(H.floorAt({}, 0, NaN)));
  assert.ok(Number.isFinite(H.floorAt({}, 0, 'ten')));
});

test('a sector value outside 0-89 falls back to min_elev', () => {
  assert.strictEqual(H.floorAt({ N: -5 }, 0, 10), 10);
  assert.strictEqual(H.floorAt({ N: 95 }, 0, 10), 10);
});

test('isBlocked compares elevation against the floor at that bearing', () => {
  const h = { N: 25, S: 5 };
  assert.strictEqual(H.isBlocked(h, 0, 17, 10), true);    // 17 deg in the north
  assert.strictEqual(H.isBlocked(h, 180, 17, 10), false);  // 17 deg in the south
});

// The same polar projection sat-geometry.js uses, passed in because that module
// is page-local: el 90 at the centre, el 0 at radius r, az 0 = north = up.
function polar(az, el, cx, cy, r) {
  const rad = r * (90 - el) / 90, a = az * Math.PI / 180;
  return { x: cx + rad * Math.sin(a), y: cy - rad * Math.cos(a) };
}

test('an empty horizon draws no rim', () => {
  assert.strictEqual(H.rimPath({}, 10, polar, 75, 75, 62), '');
  assert.strictEqual(H.rimPath(null, 10, polar, 75, 75, 62), '');
});

test('the rim is exactly two closed subpaths, each starting with a move', () => {
  // A shaded annulus needs the outer edge and the skyline traced back as two
  // separate subpaths under fill-rule evenodd. One subpath, or a stray move at
  // the end, leaves a radial spoke across the plot.
  const d = H.rimPath({ N: 25, S: 5 }, 10, polar, 75, 75, 62);
  assert.strictEqual((d.match(/M/g) || []).length, 2);
  assert.strictEqual((d.match(/Z/g) || []).length, 2);
  assert.ok(d.startsWith('M'));
  assert.ok(d.endsWith('Z'));
  assert.ok(!/Z\s*L/.test(d), 'a subpath must not start with a line');
});

test('the rim reaches the outer edge and follows the floor', () => {
  const d = H.rimPath({ N: 25 }, 10, polar, 75, 75, 62);
  const [outer, inner] = d.split('ZM');
  // Due north on the outer edge is el 0 -> the top of the circle, y = 75 - 62.
  assert.ok(outer.startsWith('M75.0 13.0'), outer.slice(0, 20));
  // The inner ring is traced BACKWARDS, so its first point is az 360 == north
  // at the N floor of 25 deg: rad = 62 * 65/90.
  const expectY = (75 - 62 * (90 - 25) / 90).toFixed(1);
  assert.ok(inner.startsWith('75.0 ' + expectY), inner.slice(0, 20));
});

// clearSegments — one entry per CONTIGUOUS run that clears the mask, not the
// first-clear-to-last-clear span the bug this replaces used to report. The
// mask is azimuth-dependent, not a circle, so a real pass can clear a low
// sector, sweep behind a tall one mid-pass, and clear again before it sets —
// reporting one span there would claim the middle dip is workable when it is
// behind the trees.
//
// { N: 30 } with minElev 10 raises the floor only near due north: el 20 stays
// clear at az 45/90 (floor falls back to 10) and goes blocked at az 0 (floor
// 30). This is the same floorAt/isBlocked the page uses, evaluated for real —
// not a stubbed isBlocked — so the fixtures below genuinely cross the N floor.
function sample(t, az, el) {
  return { t, lat: 0, lon: 0, el, az, doppler_hz: 0 };
}
const T = i => `2026-01-01T00:0${i}:00Z`;

test('clearSegments on an empty track is []', () => {
  assert.deepStrictEqual(H.clearSegments([], { N: 30 }, 10), []);
});

test('clearSegments with an empty horizon is [] even though nothing is blocked', () => {
  const track = [sample(T(0), 0, 20), sample(T(1), 90, 20)];
  assert.deepStrictEqual(H.clearSegments(track, {}, 10), []);
});

test('clearSegments on a track that clears throughout is one segment end to end', () => {
  const track = [sample(T(0), 90, 20), sample(T(1), 45, 20), sample(T(2), 0, 20)];
  // N floor is only 5 here, so even due north at el 20 is clear.
  assert.deepStrictEqual(H.clearSegments(track, { N: 5 }, 10),
    [{ from: T(0), to: T(2) }]);
});

test('clearSegments blocked at both ends is one segment in the middle', () => {
  const track = [
    sample(T(0), 0, 5),     // az 0, floor 30 -> blocked
    sample(T(1), 45, 20),   // floor 10 -> clear
    sample(T(2), 90, 20),   // floor 10 -> clear
    sample(T(3), 0, 5),     // blocked again
  ];
  assert.deepStrictEqual(H.clearSegments(track, { N: 30 }, 10),
    [{ from: T(1), to: T(2) }]);
});

test('clearSegments on an all-blocked track is []', () => {
  const track = [sample(T(0), 0, 10), sample(T(1), 90, 10)];
  // minElev itself is 50 here, so every bearing is blocked.
  assert.deepStrictEqual(H.clearSegments(track, { N: 50 }, 50), []);
});

test('clearSegments splits a pass that clears, dips behind a sector, and clears again', () => {
  // The case the whole fix exists for: az sweeps E -> N -> E, dipping behind
  // the N: 30 floor mid-pass while el stays flat at 20. First-clear-to-
  // last-clear would report T(0)-T(4) straight through the blocked sample;
  // the honest answer is two separate runs either side of it.
  const track = [
    sample(T(0), 90, 20),
    sample(T(1), 45, 20),
    sample(T(2), 0, 20),    // floor 30 -> blocked
    sample(T(3), 45, 20),
    sample(T(4), 90, 20),
  ];
  assert.deepStrictEqual(H.clearSegments(track, { N: 30 }, 10),
    [{ from: T(0), to: T(1) }, { from: T(3), to: T(4) }]);
});
