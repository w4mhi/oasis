'use strict';
// Unit tests for satgeo.inPassWindow — the shared "is this pass near enough to
// draw X" predicate behind the map's two overlays.
//
// Two windows use it with different leads, and the whole point of sharing one
// function is that the two can only ever differ by that lead. They answer
// different questions: the footprint says "this bird can be worked from here",
// which is a claim worth making late; the name label says only "this is which
// bird", which is wanted as soon as you start planning against a pass.
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const g = require(path.join(__dirname, '..', '..', 'services', 'satellites',
                            'static', 'sat-geometry.js'));

const RISE = '2026-08-12T18:00:00Z';
const SET  = '2026-08-12T18:15:00Z';
const at = (iso) => Date.parse(iso);
const FOOTPRINT = 15, LABEL = 60;

test('inside the pass itself, both windows are open', () => {
  const now = at('2026-08-12T18:07:00Z');
  assert.strictEqual(g.inPassWindow(RISE, SET, now, FOOTPRINT), true);
  assert.strictEqual(g.inPassWindow(RISE, SET, now, LABEL), true);
});

test('30 min before AOS the label is drawn and the footprint is not', () => {
  // The reason the lead is a parameter at all. Drawing coverage circles this
  // early fills the map for birds nowhere near usable; a name costs nothing.
  const now = at('2026-08-12T17:30:00Z');
  assert.strictEqual(g.inPassWindow(RISE, SET, now, FOOTPRINT), false);
  assert.strictEqual(g.inPassWindow(RISE, SET, now, LABEL), true);
});

test('both windows shut at LOS, not at some later grace period', () => {
  // A label lingering after the bird has set is a lie about a pass that is over,
  // and the marker keeps moving, so it would follow the sat around the far side.
  assert.strictEqual(g.inPassWindow(RISE, SET, at('2026-08-12T18:15:00Z'), LABEL), true);
  assert.strictEqual(g.inPassWindow(RISE, SET, at('2026-08-12T18:15:01Z'), LABEL), false);
});

test('the lead boundary is inclusive on the exact second', () => {
  assert.strictEqual(g.inPassWindow(RISE, SET, at('2026-08-12T17:45:00Z'), FOOTPRINT), true);
  assert.strictEqual(g.inPassWindow(RISE, SET, at('2026-08-12T17:44:59Z'), FOOTPRINT), false);
});

test('61 minutes out, nothing is drawn', () => {
  assert.strictEqual(g.inPassWindow(RISE, SET, at('2026-08-12T16:59:00Z'), LABEL), false);
});

test('an unparseable pass draws nothing rather than comparing against NaN', () => {
  // /passes is computed under a wall-clock budget and returns partial results
  // that fill in over later polls, so a half-formed entry really does arrive
  // here. NaN comparisons are false anyway — this makes that a decision.
  const now = at('2026-08-12T18:07:00Z');
  assert.strictEqual(g.inPassWindow(undefined, SET, now, LABEL), false);
  assert.strictEqual(g.inPassWindow(RISE, null, now, LABEL), false);
  assert.strictEqual(g.inPassWindow('not a date', SET, now, LABEL), false);
});

test('a zero lead is honoured, not treated as "unset"', () => {
  // Guards against a `leadMin || DEFAULT` style fallback creeping in.
  assert.strictEqual(g.inPassWindow(RISE, SET, at('2026-08-12T17:59:59Z'), 0), false);
  assert.strictEqual(g.inPassWindow(RISE, SET, at('2026-08-12T18:00:00Z'), 0), true);
});
