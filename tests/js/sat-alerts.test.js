'use strict';
// Unit tests for the shared satellite pass-alert engine.
//
// These failures are all SILENT in the field: a missed edge means the operator
// simply never hears the pass, and a re-fire bug means the chime nags every 2 s
// until the bird rises. Neither shows up on screen.
//
// NOTE on how these are written: the engine keys its fire-once state on the RISE
// TIME, which is what makes a new pass re-arm on its own. So a test that means
// "time passes" must hold `rise` FIXED and advance `now` — minting a new rise per
// tick silently models a different pass every time, which is a test that proves
// nothing.
const test = require('node:test');
const assert = require('node:assert');
const A = require('../../common/js/sat-alerts.js');

const T0 = Date.parse('2026-08-08T12:00:00Z');
const RISE = T0 + 10 * 60000;                 // the pass under test rises at T0+10m
const MIN = 60000;

// A bird whose pass rises at `at` (default RISE).
const bird = (extra, at) => Object.assign(
  { norad: 25544, name: 'ISS', rise: new Date(at || RISE).toISOString(), max_el: 62.4 },
  extra || {});
// "now" expressed as minutes before the rise, which is how the thresholds read.
const tMinus = mins => RISE - mins * MIN;

test('nothing fires while the pass is further out than T-10', () => {
  const r = A.oasisSatAlertsDue([bird()], tMinus(11), {});
  assert.strictEqual(r.fire, false);
  assert.deepStrictEqual(r.announce, []);
});

test('T-10 fires once, at the relaxed pitch, and announces the bird', () => {
  const r = A.oasisSatAlertsDue([bird()], tMinus(9), {});
  assert.strictEqual(r.fire, true);
  assert.strictEqual(r.freq, 620);
  assert.deepStrictEqual(r.announce.map(b => b.norad), [25544]);
});

test('the same pass does not fire again on the next tick', () => {
  // The bug this guards: a 2 s poll re-firing the chime every tick for ten
  // solid minutes.
  const st = {};
  A.oasisSatAlertsDue([bird()], tMinus(9), st);
  const again = A.oasisSatAlertsDue([bird()], tMinus(9) + 2000, st);
  assert.strictEqual(again.fire, false);
  assert.deepStrictEqual(again.announce, []);
});

test('T-5 fires separately, at the urgent pitch, with no second announcement', () => {
  const st = {};
  A.oasisSatAlertsDue([bird()], tMinus(9), st);          // T-10 consumed
  const r = A.oasisSatAlertsDue([bird()], tMinus(4), st);
  assert.strictEqual(r.fire, true);
  assert.strictEqual(r.freq, 780);
  assert.deepStrictEqual(r.announce, []);   // spoken once per pass, at T-10 only
});

test('a page opened inside the last 5 minutes gets BOTH edges and the urgent pitch', () => {
  // Not an else-branch: the operator who walks up at T-4 must not be given the
  // relaxed tone for a warning they already missed.
  const r = A.oasisSatAlertsDue([bird()], tMinus(4), {});
  assert.strictEqual(r.fire, true);
  assert.strictEqual(r.freq, 780);
  assert.deepStrictEqual(r.announce.map(b => b.norad), [25544]);
});

test('a pass already in progress never fires — a late warning is noise', () => {
  const r = A.oasisSatAlertsDue([bird()], tMinus(-1), {});
  assert.strictEqual(r.fire, false);
});

test('the NEXT pass of the same bird re-arms both edges', () => {
  const st = {};
  A.oasisSatAlertsDue([bird()], tMinus(9), st);
  A.oasisSatAlertsDue([bird()], tMinus(4), st);
  // A different rise time = a different pass, so the flags reset on their own.
  const next = RISE + 90 * MIN;
  const r = A.oasisSatAlertsDue([bird({ max_el: 30 }, next)], next - 9 * MIN, st);
  assert.strictEqual(r.fire, true);
  assert.deepStrictEqual(r.announce.map(b => b.norad), [25544]);
});

test('one chime covers a tick where several birds cross at once', () => {
  const r = A.oasisSatAlertsDue([
    bird(),                                                    // T-9
    bird({ norad: 33591, name: 'NOAA 19' }, tMinus(9) + 4 * MIN),  // T-4
  ], tMinus(9), {});
  assert.strictEqual(r.fire, true);
  assert.strictEqual(r.freq, 780);          // the most urgent edge wins the pitch
  assert.deepStrictEqual(r.announce.map(b => b.norad).sort(), [25544, 33591]);
});

test('epoch-ms rise times work as well as ISO strings', () => {
  const r = A.oasisSatAlertsDue([{ norad: 1, name: 'X', rise: RISE, max_el: 10 }],
                                tMinus(9), {});
  assert.strictEqual(r.fire, true);
});

test('junk rows are skipped, never thrown on', () => {
  const r = A.oasisSatAlertsDue(
    [null, {}, { norad: 5 }, { norad: 6, rise: 'not-a-date' }, bird()], tMinus(9), {});
  assert.strictEqual(r.fire, true);
  assert.deepStrictEqual(r.announce.map(b => b.norad), [25544]);
});

test('no birds, no state, no inputs — all safe', () => {
  assert.strictEqual(A.oasisSatAlertsDue([], T0, {}).fire, false);
  assert.strictEqual(A.oasisSatAlertsDue(undefined, T0, undefined).fire, false);
});

test('the spoken line names the bird and rounds the elevation', () => {
  assert.strictEqual(A.oasisSatSpeech({ name: 'ISS', max_el: 62.4 }),
                     'ISS, in ten minutes, maximum elevation 62 degrees');
});

test('a missing elevation still produces a usable sentence', () => {
  assert.strictEqual(A.oasisSatSpeech({ name: 'ISS' }), 'ISS, in ten minutes');
  assert.strictEqual(A.oasisSatSpeech(null), '');
});

test('thresholds are exported so callers cannot re-declare them differently', () => {
  assert.strictEqual(A.OASIS_SAT_T10_MS, 600000);
  assert.strictEqual(A.OASIS_SAT_T5_MS, 300000);
});

// ── The fire-once key ────────────────────────────────────────────────────────
// /api/satellites/passes returns rise times with MICROSECOND precision, from
// Skyfield's root-finder. Recomputing the same pass (cache refresh, TLE update,
// a different search window) shifts the trailing digits. The guard used to key
// on the raw string, so that read as a brand-new pass and the bird was
// announced a second time — the kiosk saying the same satellite twice.
test('a re-computed rise time is the SAME pass, not a new one', () => {
  const st = {};
  // Exactly the shape /api/satellites/passes returns, microseconds and all.
  const first  = new Date(RISE).toISOString().replace('.000Z', '.349112+00:00');
  const second = new Date(RISE).toISOString().replace('.000Z', '.349835+00:00');
  const at = (rise) => ({ norad: 25544, name: 'ISS', rise, max_el: 62.4 });

  const a = A.oasisSatAlertsDue([at(first)], tMinus(9), st);
  assert.deepStrictEqual(a.announce.map(b => b.norad), [25544]);

  // Same pass, microseconds apart — must NOT speak again.
  const b = A.oasisSatAlertsDue([at(second)], tMinus(8), st);
  assert.strictEqual(b.fire, false);
  assert.deepStrictEqual(b.announce, []);
});

test('a rise that moves by seconds within the same minute is still one pass', () => {
  const st = {};
  const at = (ms) => ({ norad: 25544, name: 'ISS', rise: ms, max_el: 10 });
  A.oasisSatAlertsDue([at(RISE)], tMinus(9), st);
  const again = A.oasisSatAlertsDue([at(RISE + 900)], tMinus(9) + 1000, st);
  assert.strictEqual(again.fire, false);
  assert.deepStrictEqual(again.announce, []);
});

test('a genuinely different pass still re-arms', () => {
  // The guard must not become so coarse that the NEXT pass is swallowed.
  const st = {};
  A.oasisSatAlertsDue([bird()], tMinus(9), st);
  const next = RISE + 90 * MIN;
  const r = A.oasisSatAlertsDue([bird({}, next)], next - 9 * MIN, st);
  assert.strictEqual(r.fire, true);
  assert.deepStrictEqual(r.announce.map(b => b.norad), [25544]);
});
