'use strict';
// Unit tests for the shared pass-alert bell state.
//
// Every case here is a way the operator can silently LOSE an alert, or have a
// disarmed bird start chiming again. The bell is per-DEVICE and there is no
// server copy to fall back on, so a bug here has no second source of truth to
// repair it — which is why this logic is a module and not inline page script.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const MODULE = path.join(__dirname, '..', '..', 'common', 'js', 'sat-bells.js');

/** A fresh module instance over a fresh fake localStorage.
 *
 * The module captures its store at load, so each test re-requires it with the
 * cache busted. Without that they would share one map and pass or fail on the
 * order they happen to run in.
 */
function fresh(seed) {
  const store = Object.assign({}, seed || {});
  const fake = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  Object.defineProperty(globalThis, 'localStorage', {
    value: fake, writable: true, configurable: true,
  });
  delete require.cache[require.resolve(MODULE)];
  return { B: require(MODULE), store };
}

test('arming persists, and disarming deletes rather than storing false', () => {
  const { B, store } = fresh();
  B.setArmed(25544, true);
  assert.deepStrictEqual(JSON.parse(store['oasis_sat_bells']), { 25544: true });
  B.setArmed(25544, false);
  // A stored `false` is an entry that means nothing, and every reader would have
  // to filter it out forever.
  assert.deepStrictEqual(JSON.parse(store['oasis_sat_bells']), {});
});

test('armed state survives a reload', () => {
  const first = fresh();
  first.B.setArmed(33591, true);
  const again = fresh(first.store);
  assert.strictEqual(again.B.armed(33591), true);
});

test('toggle flips and reports the new state', () => {
  const { B } = fresh();
  assert.strictEqual(B.toggle(7530), true);
  assert.strictEqual(B.armed(7530), true);
  assert.strictEqual(B.toggle(7530), false);
  assert.strictEqual(B.armed(7530), false);
});

test('armedNorads is numeric and sorted, never string keys', () => {
  const { B } = fresh();
  B.setMany([33591, 7530, 25544], true);
  assert.deepStrictEqual(B.armedNorads(), [7530, 25544, 33591]);
  assert.strictEqual(B.count(), 3);
});

test('setMany arms and disarms a whole set in one go', () => {
  const { B } = fresh();
  B.setMany([1, 2, 3], true);
  assert.strictEqual(B.count(), 3);
  B.setMany([1, 2, 3], false);
  assert.strictEqual(B.count(), 0);
});

test('prune drops bells for birds no longer monitored', () => {
  // An orphan bell is inert (alert paths walk the monitored set), so it would sit
  // unseen and then fire months later when the bird was re-selected.
  const { B } = fresh();
  B.setMany([1, 2, 3], true);
  assert.strictEqual(B.prune([1, 3]), true);
  assert.deepStrictEqual(B.armedNorads(), [1, 3]);
  assert.strictEqual(B.prune([1, 3]), false, 'no change means no needless write');
});

test('a private-mode browser with no storage still works, just without memory', () => {
  Object.defineProperty(globalThis, 'localStorage', {
    get() { throw new Error('denied'); }, configurable: true,
  });
  delete require.cache[require.resolve(MODULE)];
  const B = require(MODULE);
  assert.doesNotThrow(() => B.setArmed(25544, true));
  assert.strictEqual(B.armed(25544), true, 'in-memory state still answers');
});

// ── Adoption from the legacy roster field ───────────────────────────────────
test('a browser with no local bells adopts the roster once', () => {
  const { B } = fresh();
  const p = B.adoptPlan([], [25544, 33591], false);
  assert.deepStrictEqual(p.adopt, [25544, 33591]);
  assert.strictEqual(p.markAdopted, true);
});

test('a browser that already has local bells keeps them and adopts nothing', () => {
  // It armed those under the new rules; the roster is the stale copy.
  const { B } = fresh();
  const p = B.adoptPlan([7530], [25544, 33591], false);
  assert.deepStrictEqual(p.adopt, []);
  assert.strictEqual(p.markAdopted, true);
});

test('adoption never runs twice', () => {
  // Adopting again would re-arm a bird the operator has since disarmed, with
  // nothing on screen to say why.
  const { B } = fresh();
  const p = B.adoptPlan([], [25544], true);
  assert.strictEqual(p.adopt, null);
  assert.strictEqual(p.markAdopted, false);
});

test('adoptOnce applies the roster set and records that it is done', () => {
  const { B, store } = fresh();
  B.adoptOnce([25544, 33591]);
  assert.deepStrictEqual(B.armedNorads(), [25544, 33591]);
  assert.strictEqual(store['oasis_sat_bells_adopted'], '1');
  // Second call must be inert even with a different roster.
  B.adoptOnce([7530]);
  assert.deepStrictEqual(B.armedNorads(), [25544, 33591]);
});

test('the adopted flag is NOT the old migrated flag', () => {
  // A browser carrying the old local->roster flag must still adopt under the new
  // rules; reusing the key would skip exactly the devices that need this.
  const { B } = fresh({ oasis_sat_bells_migrated: '1' });
  B.adoptOnce([25544]);
  assert.deepStrictEqual(B.armedNorads(), [25544]);
});

// ── The control ─────────────────────────────────────────────────────────────
test('the glyph shows three states, not two', () => {
  const { B, store } = fresh();
  const off = B.bellHTML(25544);
  B.setArmed(25544, true);
  const on = B.bellHTML(25544);
  store['oasis_sat_muted'] = '1';
  const armedButMuted = B.bellHTML(25544);

  assert.ok(!off.includes('sat-bell on'), 'disarmed must not carry the on class');
  assert.ok(on.includes('sat-bell on'), 'armed carries the on class');
  // Armed-but-muted is a real state: bright (still .on) but crossed, so it never
  // reads as disarmed.
  assert.ok(armedButMuted.includes('sat-bell on'), 'muted must not look disarmed');
  assert.notStrictEqual(on, armedButMuted, 'muted must change the glyph');
});

test('the glyph reports pressed state for assistive tech', () => {
  const { B } = fresh();
  assert.ok(B.bellHTML(1).includes('aria-pressed="false"'));
  B.setArmed(1, true);
  assert.ok(B.bellHTML(1).includes('aria-pressed="true"'));
});

test('the glyph uses inline SVG, never an emoji', () => {
  // The bell emoji renders blank on Pi Chromium.
  const { B } = fresh();
  const html = B.bellHTML(1);
  assert.ok(html.includes('<svg'));
  for (const ch of html) {
    assert.ok(ch.codePointAt(0) < 0x1F000, 'non-BMP codepoint in the bell markup');
  }
});

test('tap stops propagation so a bell inside a row never opens the sheet', () => {
  const { B } = fresh();
  let stopped = false, defaulted = false;
  B.tap({ stopPropagation: () => { stopped = true; },
          preventDefault: () => { defaulted = true; } }, 25544);
  assert.strictEqual(stopped, true);
  assert.strictEqual(defaulted, true);
  assert.strictEqual(B.armed(25544), true);
});

test('tap survives being called without an event', () => {
  const { B } = fresh();
  assert.doesNotThrow(() => B.tap(null, 1));
  assert.strictEqual(B.armed(1), true);
});

test('onChange fires on every state write, so both surfaces repaint', () => {
  const { B } = fresh();
  let n = 0;
  B.onChange(() => { n++; });
  B.setArmed(1, true);
  B.toggle(1);
  B.setMany([2, 3], true);
  assert.strictEqual(n, 3);
});

test('a throwing listener does not stop the others', () => {
  const { B } = fresh();
  let reached = false;
  B.onChange(() => { throw new Error('bad listener'); });
  B.onChange(() => { reached = true; });
  assert.doesNotThrow(() => B.setArmed(1, true));
  assert.strictEqual(reached, true);
});
