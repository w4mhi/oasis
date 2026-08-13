'use strict';
// Unit tests for satgeo.labelAnchor — placing a satellite's name beside its
// live marker on the equirectangular world map.
//
// The map is 720×360 user units and a bird's sub-point can be anywhere in it,
// including hard against an edge. A label that runs off the map is worse than
// no label: the operator sees a truncated name, can't tell which bird it is,
// and now distrusts every other label on the screen too. So the two things
// worth pinning down are the side flip near the right edge and the vertical
// clamp near the poles.
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const g = require(path.join(__dirname, '..', '..', 'services', 'satellites',
                            'static', 'sat-geometry.js'));

const W = 720, H = 360;

test('a marker with room to its right labels to the right', () => {
  const a = g.labelAnchor(100, 180, 11, W, H);
  assert.strictEqual(a.anchor, 'start');
  assert.ok(a.x > 100, 'label sits to the right of the marker');
});

test('a long name near the right edge flips to the left', () => {
  // 'SAUDISAT 1C (SO-50)' is 19 characters. At x=690 it would run well past
  // 720 — this is the SO-50 case that prompted the feature, and the one an
  // equirectangular map hits constantly because every orbit crosses the edge.
  const a = g.labelAnchor(690, 180, 19, W, H);
  assert.strictEqual(a.anchor, 'end');
  assert.ok(a.x < 690, 'label sits to the left of the marker');
});

test('the flip is decided by the name length, not the marker alone', () => {
  // Same x, different names: a short one still fits on the right. Placing by
  // position alone would flip both and leave a two-character label stranded on
  // the wrong side of its own marker.
  assert.strictEqual(g.labelAnchor(660, 180, 30, W, H).anchor, 'end');
  assert.strictEqual(g.labelAnchor(660, 180, 4, W, H).anchor, 'start');
});

test('a label never starts left of the map on a normal name', () => {
  const a = g.labelAnchor(715, 180, 8, W, H);
  assert.ok(a.x - 8 * 4.8 > 0, 'flipped label still ends inside the map');
});

test('a near-polar sub-point is clamped away from the map edges', () => {
  // y=0 is the north pole edge. An unclamped baseline there is drawn at y=3,
  // which clips the ascenders; y=360 would be off the map entirely.
  assert.ok(g.labelAnchor(300, 0, 6, W, H).y >= 8, 'north edge clamped down');
  assert.ok(g.labelAnchor(300, H, 6, W, H).y <= H - 4, 'south edge clamped up');
});

test('away from the edges the label sits on the marker centre', () => {
  // Not the marker's exact y: SVG text is baseline-anchored, so a label at the
  // marker's y hangs above it rather than beside it.
  const a = g.labelAnchor(300, 180, 6, W, H);
  assert.ok(a.y > 180 && a.y < 186, 'nudged down onto the optical centre');
});

test('the flip threshold tracks the font size, not a baked-in advance', () => {
  // The caller now scales the label so it renders at a fixed PIXEL size, which
  // means the user-unit font size changes with the display. A 20-character name
  // that fits on the right at the default size must NOT still claim to fit once
  // the label is twice as wide — that is exactly how a name walks off the map.
  assert.strictEqual(g.labelAnchor(560, 180, 20, W, H).anchor, 'start');
  assert.strictEqual(g.labelAnchor(560, 180, 20, W, H, { fontSize: 16 }).anchor, 'end');
});

test('a bigger font is clamped further from the map edge', () => {
  // The top clamp is the font size: a 16-unit label at y=0 clipped at the old
  // fixed 8 would still lose its ascenders.
  assert.ok(g.labelAnchor(300, 0, 6, W, H, { fontSize: 16 }).y >= 16);
});

test('the gap clears whatever the label sits beside', () => {
  // The armed bird carries a ring at r=11, so it needs a wider gap than the
  // bare glyph or its name lands inside its own ring.
  const bare = g.labelAnchor(300, 180, 6, W, H);
  const ringed = g.labelAnchor(300, 180, 6, W, H, { gap: 14 });
  assert.ok(ringed.x > bare.x, 'a wider gap pushes the label further out');
  assert.ok(ringed.x - 300 >= 11, 'clears the armed ring radius');
});

test('a zero-length or missing name does not produce NaN', () => {
  // An unplotted-then-replotted bird can momentarily have no ROSTER entry, and
  // NaN in a transform silently drops the whole overlay element.
  for (const chars of [0, -1, undefined]) {
    const a = g.labelAnchor(300, 180, chars, W, H);
    assert.ok(isFinite(a.x) && isFinite(a.y), `chars=${chars} stays finite`);
  }
});
