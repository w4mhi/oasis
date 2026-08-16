'use strict';
const test = require('node:test');
const assert = require('node:assert');
const FR = require('../../common/js/freshness.js');

const src = (id, state, age) => ({
  id, label: id, state, age_days: age, max_age_days: 3, tier: 'small'
});

test('summarize picks the worst state', () => {
  assert.strictEqual(FR.summarize([src('a', 'fresh', 1)]).worst, 'fresh');
  assert.strictEqual(
    FR.summarize([src('a', 'fresh', 1), src('b', 'stale', 9)]).worst, 'stale');
  assert.strictEqual(
    FR.summarize([src('a', 'stale', 9), src('b', 'missing', null)]).worst,
    'missing');
  assert.strictEqual(
    FR.summarize([src('a', 'stale', 9), src('b', 'deferred', 9)]).worst,
    'deferred');
});

test('unconfigured never dominates, and never looks broken', () => {
  // A source switched off must not mask a stale one, and must not make an
  // otherwise-current station read as a problem.
  assert.strictEqual(
    FR.summarize([src('a', 'fresh', 1), src('b', 'unconfigured', null)]).worst,
    'fresh');
  assert.strictEqual(
    FR.summarize([src('a', 'unconfigured', null)]).cls, 'fx-ok');
});

test('summarize counts every state', () => {
  const s = FR.summarize([src('a', 'fresh', 1), src('b', 'stale', 9),
                          src('c', 'stale', 9)]);
  assert.strictEqual(s.counts.fresh, 1);
  assert.strictEqual(s.counts.stale, 2);
  assert.strictEqual(s.counts.missing, 0);
});

test('empty and missing input are safe', () => {
  assert.strictEqual(FR.summarize([]).worst, 'fresh');
  assert.strictEqual(FR.summarize([]).label, 'CURRENT');
  assert.strictEqual(FR.summarize(undefined).worst, 'fresh');
});

test('unknown states do not crash the summary', () => {
  const s = FR.summarize([src('a', 'wat', 1)]);
  assert.strictEqual(s.worst, 'fresh');
});

test('fmtAge buckets', () => {
  assert.strictEqual(FR.fmtAge(null), 'never');
  assert.strictEqual(FR.fmtAge(undefined), 'never');
  assert.strictEqual(FR.fmtAge(0.5), '12h');
  assert.strictEqual(FR.fmtAge(9), '9d');
  assert.strictEqual(FR.fmtAge(800), '2y');
});

test('rowText formats ages', () => {
  assert.strictEqual(FR.rowText(src('a', 'fresh', 0.5)).age, '12h');
  assert.strictEqual(FR.rowText(src('a', 'stale', 9)).age, '9d');
  assert.strictEqual(FR.rowText(src('a', 'missing', null)).age, 'never');
});

test('only deferred and unconfigured offer an action', () => {
  assert.strictEqual(FR.rowText(src('a', 'deferred', 9)).action, 'Update now');
  assert.strictEqual(FR.rowText(src('a', 'unconfigured', null)).action,
                     'Add token');
  assert.strictEqual(FR.rowText(src('a', 'stale', 9)).action, null);
  assert.strictEqual(FR.rowText(src('a', 'fresh', 1)).action, null);
  assert.strictEqual(FR.rowText(src('a', 'missing', null)).action, null);
});

test('every state explains itself in plain language', () => {
  for (const s of FR.STATE_ORDER) {
    const r = FR.rowText(src('a', s, 1));
    assert.ok(r.reason.length > 0, s);
  }
  // The two states an operator can do nothing about must say why.
  assert.match(FR.rowText(src('a', 'stale', 9)).reason, /internet/i);
  assert.match(FR.rowText(src('a', 'missing', null)).reason, /internet/i);
  assert.match(FR.rowText(src('a', 'deferred', 9)).reason, /metered/i);
  assert.match(FR.rowText(src('a', 'unconfigured', null)).reason, /token/i);
});

test('classes are distinct enough to tell states apart', () => {
  assert.strictEqual(FR.rowText(src('a', 'fresh', 1)).cls, 'fx-ok');
  assert.strictEqual(FR.rowText(src('a', 'missing', null)).cls, 'fx-bad');
  assert.strictEqual(FR.rowText(src('a', 'unconfigured', null)).cls, 'fx-off');
  // stale and deferred intentionally share the warn colour; the action
  // distinguishes them.
  assert.strictEqual(FR.rowText(src('a', 'stale', 9)).cls, 'fx-warn');
  assert.strictEqual(FR.rowText(src('a', 'deferred', 9)).cls, 'fx-warn');
});
