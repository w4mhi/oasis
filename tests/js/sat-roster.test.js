'use strict';
// Unit tests for satroster.isListenable — "is there anything on this bird we
// could actually demodulate?"
//
// The stakes are asymmetric, and that shapes every case below. A false NEGATIVE
// hides a bird the operator could have worked, silently, from a roster of ~130 —
// they would never know it was there. A false positive merely leaves a dead
// entry in a list they are already scrolling. So where the roster does not say,
// the answer is YES.
//
// That is the same trap as the permissions banner and the maps count: an absent
// field and a negative answer are not the same fact, and collapsing them to one
// `false` is how a filter starts lying.
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const r = require(path.join(__dirname, '..', '..', 'services', 'satellites',
                            'static', 'sat-roster.js'));

const dl = (mode, supported) => ({ mode, freq_mhz: 145.8, supported });

test('a bird with one supported downlink is listenable', () => {
  assert.strictEqual(r.isListenable({ downlinks: [dl('FM', true)] }), true);
});

test('a bird whose downlinks are all unsupported is not', () => {
  // The real shape of this case: BPSK telemetry and LRPT imagery, both real
  // transmissions, neither one something the chain can turn into audio.
  const sat = { downlinks: [dl('BPSK', false), dl('LRPT', false)] };
  assert.strictEqual(r.isListenable(sat), false);
});

test('one supported downlink among unsupported ones is enough', () => {
  const sat = { downlinks: [dl('BPSK', false), dl('FM', true), dl('LRPT', false)] };
  assert.strictEqual(r.isListenable(sat), true);
});

test('no downlinks at all is not listenable', () => {
  // Known-empty, not unknown: there is nothing to hear and the roster says so.
  assert.strictEqual(r.isListenable({ downlinks: [] }), false);
  assert.strictEqual(r.isListenable({}), false);
  assert.strictEqual(r.isListenable(null), false);
  assert.strictEqual(r.isListenable(undefined), false);
});

test('a roster that never learned the flag hides nothing', () => {
  // A box synced before mode_support decorated the roster carries downlinks with
  // no `supported` key at all. Treating absent as false would empty the list on
  // exactly the station least able to fix it — no internet to rebuild the roster.
  const sat = { downlinks: [{ mode: 'FM', freq_mhz: 145.8 }] };
  assert.strictEqual(r.isListenable(sat), true);
});

test('unknown outvotes a known no', () => {
  const sat = { downlinks: [dl('BPSK', false), { mode: 'FM', freq_mhz: 437.8 }] };
  assert.strictEqual(r.isListenable(sat), true);
});

test('only a real boolean true counts as supported', () => {
  // Guards against a truthy string like "false" or "no" sneaking through from a
  // hand-edited roster and quietly re-admitting every bird.
  assert.strictEqual(r.isListenable({ downlinks: [dl('FM', 'false')] }), false);
  assert.strictEqual(r.isListenable({ downlinks: [dl('FM', 0)] }), false);
  assert.strictEqual(r.isListenable({ downlinks: [dl('FM', 1)] }), false);
});

test('a malformed downlink entry does not throw or hide the good one', () => {
  const sat = { downlinks: [null, dl('FM', true)] };
  assert.strictEqual(r.isListenable(sat), true);
  assert.strictEqual(r.isListenable({ downlinks: [null] }), false);
});

test('downlinks that is not an array is treated as absent, not as empty', () => {
  assert.strictEqual(r.isListenable({ downlinks: 'FM 145.8' }), false);
});

test('listenableCount counts birds, not downlinks', () => {
  const roster = [
    { downlinks: [dl('FM', true), dl('CW', true)] },   // one bird, two downlinks
    { downlinks: [dl('BPSK', false)] },
    { downlinks: [] },
    { downlinks: [{ mode: 'FM' }] },                   // unknown -> counted
  ];
  assert.strictEqual(r.listenableCount(roster), 2);
  assert.strictEqual(r.listenableCount([]), 0);
  assert.strictEqual(r.listenableCount(null), 0);
});
