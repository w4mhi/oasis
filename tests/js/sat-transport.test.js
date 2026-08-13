'use strict';
// Unit tests for sattransport.transportState — the Rec / Play / Stop matrix.
//
// This exists as a pure function because the interesting cases are the ones you
// cannot reach by clicking on a laptop: a tracked capture running on the csdr
// backend, the same capture on rtl_fm, and a listener attached to a recording
// that is still writing to disk. Eyeballing three buttons across seven inputs is
// how a button ends up dead in the field with nothing to say why.
//
// The rule the whole matrix serves: STOP IS THE ONLY THING THAT ENDS A
// RECORDING. Play attaches and detaches; it must never be able to destroy an
// artifact that cannot be re-taken.
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const T = require(path.join(__dirname, '..', '..', 'services', 'satellites',
                            'static', 'sat-transport.js'));

// Idle, armed, everything available — the baseline every case below varies from.
const READY = {
  recording: false, streaming: false, backend: null, listening: false,
  armed: true, dongleFree: true, inWindow: true,
};
const st = (over) => T.transportState(Object.assign({}, READY, over));

test('armed and idle: both starts live, Stop is dead', () => {
  const s = st({});
  assert.strictEqual(s.rec.disabled, false);
  assert.strictEqual(s.play.disabled, false);
  assert.strictEqual(s.play.action, 'start');
  assert.strictEqual(s.stop.disabled, true);
});

test('nothing armed: nothing can start', () => {
  const s = st({ armed: false });
  assert.strictEqual(s.rec.disabled, true);
  assert.strictEqual(s.play.disabled, true);
  assert.strictEqual(s.stop.disabled, true);
  assert.match(s.rec.title, /arm/i);
});

test('dongle taken, or the pass not in range: no start, and the title says which', () => {
  assert.strictEqual(st({ dongleFree: false }).rec.disabled, true);
  assert.strictEqual(st({ inWindow: false }).rec.disabled, true);
  assert.match(st({ inWindow: false }).rec.title, /range|armable/i);
});

test('recording on csdr: Play is live and Rec is not', () => {
  // The payoff case. One capture, many sinks — a listener can join a recording
  // that is already running, so Play must be reachable precisely when Rec is not.
  const s = st({ recording: true, backend: 'csdr' });
  assert.strictEqual(s.rec.disabled, true, 'Rec cannot restart a running capture');
  assert.strictEqual(s.rec.active, true);
  assert.strictEqual(s.play.disabled, false, 'Play attaches to the running capture');
  assert.strictEqual(s.play.action, 'start');
  assert.strictEqual(s.stop.disabled, false);
  assert.strictEqual(s.stop.active, true);
});

test('recording on rtl_fm: Play is dead, and the title says why', () => {
  // A shell pipeline has one stdout. Leaving Play enabled here would produce a
  // 409 mid-pass; leaving it silently grey would look like a bug.
  const s = st({ recording: true, backend: 'rtl_fm' });
  assert.strictEqual(s.play.disabled, true);
  assert.match(s.play.title, /uncorrected|one audio stream|stop the recording/i);
});

test('recording with an unknown backend is treated as uncorrected', () => {
  // `backend` is always populated while capturing (that is what v3.44.2 fixed),
  // so a missing one means something is wrong. Offering an attach that the server
  // will refuse is worse than withholding it, because the refusal lands mid-pass.
  assert.strictEqual(st({ recording: true, backend: null }).play.disabled, true);
  assert.strictEqual(st({ recording: true, backend: 'weird' }).play.disabled, true);
});

test('listening while recording: Play detaches, and detach is NOT a stop', () => {
  const s = st({ recording: true, backend: 'csdr', listening: true });
  assert.strictEqual(s.play.active, true);
  assert.strictEqual(s.play.disabled, false);
  assert.strictEqual(s.play.action, 'detach');
  // The whole point: the recording survives it, and the title promises so.
  assert.match(s.play.title, /recording continues|keeps recording/i);
  assert.strictEqual(s.stop.disabled, false);
});

test('a standalone stream is stopped, not detached', () => {
  // No recording underneath: the stream itself holds the dongle, so letting go
  // means ending the capture — the existing /listen/stop path, unchanged.
  const s = st({ streaming: true, listening: true, backend: 'rtl_fm' });
  assert.strictEqual(s.play.action, 'stop-stream');
  assert.strictEqual(s.play.active, true);
  assert.strictEqual(s.rec.disabled, true, 'one dongle: cannot record under a stream');
  assert.strictEqual(s.stop.disabled, false);
});

test('streaming outranks a stale listening flag', () => {
  // Both true is the normal shape for a standalone stream we started ourselves;
  // the server flag decides, so we never detach something that owns the dongle.
  assert.strictEqual(st({ streaming: true, listening: true }).play.action, 'stop-stream');
});

test('listening with the server idle detaches harmlessly', () => {
  // The stream died and the poll has not caught up. Tearing down the audio
  // element is right; POSTing a stop for a capture that is gone is not.
  const s = st({ listening: true });
  assert.strictEqual(s.play.action, 'detach');
});

test('Stop is live whenever anything is running, and dead otherwise', () => {
  assert.strictEqual(st({}).stop.disabled, true);
  assert.strictEqual(st({ recording: true, backend: 'csdr' }).stop.disabled, false);
  assert.strictEqual(st({ streaming: true }).stop.disabled, false);
});

test('Rec never carries a stop action, in any state', () => {
  // The guard against the label-morphing design: whatever else changes, the
  // button that starts a recording must never be the button that ends one.
  for (const over of [{}, { recording: true, backend: 'csdr' },
                      { recording: true, backend: 'rtl_fm' },
                      { streaming: true }, { listening: true }]) {
    const s = st(over);
    assert.ok(!('action' in s.rec) || s.rec.action === 'start',
              'Rec action must never be a stop: ' + JSON.stringify(over));
  }
});

test('a garbage state object does not throw', () => {
  assert.doesNotThrow(() => T.transportState(null));
  assert.doesNotThrow(() => T.transportState({}));
  const s = T.transportState(null);
  assert.strictEqual(s.rec.disabled, true);
  assert.strictEqual(s.stop.disabled, true);
});
