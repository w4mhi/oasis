'use strict';
// Unit tests for the shared pass-alert bell reconcile decision.
//
// Every case here is a way the operator can silently LOSE an alert (or have a
// disarmed bird start chiming again), which is exactly why the decision is a
// pure function instead of inline page logic.
const test = require('node:test');
const assert = require('node:assert');
const B = require('../../common/js/sat-bells.js');

test('first load with local bells migrates them up, and does NOT mark done yet', () => {
  const p = B.oasisBellPlan([25544, 33591], [], false);
  assert.strictEqual(p.action, 'migrate');
  assert.deepStrictEqual(p.push, [25544, 33591]);
  // The caller marks migrated only after the server confirms the write. Marking
  // it here would strand the bells: the next load adopts a roster that never
  // received them, and the operator's alerts are gone with no symptom.
  assert.strictEqual(p.markMigrated, false);
});

test('a fresh browser adopts the roster, so a bell armed elsewhere shows up', () => {
  const p = B.oasisBellPlan([], [25544], false);
  assert.strictEqual(p.action, 'adopt');
  assert.deepStrictEqual(p.adopt, [25544]);
  // Nothing local to lose, so its migration is trivially complete — otherwise it
  // re-checks on every load forever.
  assert.strictEqual(p.markMigrated, true);
});

test('after migrating, the server wins — local bells are never pushed again', () => {
  const p = B.oasisBellPlan([25544, 33591], [43017], true);
  assert.strictEqual(p.action, 'adopt');
  assert.deepStrictEqual(p.push, []);
  assert.deepStrictEqual(p.adopt, [43017]);
  assert.strictEqual(p.markMigrated, false);   // already recorded
});

test('a DISARM on another device sticks — this is why reconcile is not a union', () => {
  // Local still believes 25544 is armed (stale copy from before the disarm); the
  // roster says nothing is armed. A union would re-arm it and push it back up, so
  // a noisy bird could never be silenced for good.
  const p = B.oasisBellPlan([25544], [], true);
  assert.strictEqual(p.action, 'adopt');
  assert.deepStrictEqual(p.adopt, []);
  assert.deepStrictEqual(p.push, []);
});

test('a migrated browser adopting an empty roster disarms cleanly', () => {
  const p = B.oasisBellPlan([], [], true);
  assert.strictEqual(p.action, 'adopt');
  assert.deepStrictEqual(p.adopt, []);
});

test('missing/undefined inputs are treated as empty, never thrown on', () => {
  const p = B.oasisBellPlan(undefined, undefined, false);
  assert.strictEqual(p.action, 'adopt');
  assert.deepStrictEqual(p.adopt, []);
  assert.strictEqual(p.markMigrated, true);
});

test('the plan never mutates its inputs', () => {
  const local = [33591, 25544], roster = [43017];
  B.oasisBellPlan(local, roster, false);
  assert.deepStrictEqual(local, [33591, 25544]);
  assert.deepStrictEqual(roster, [43017]);
});
