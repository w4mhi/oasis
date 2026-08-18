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
  assert.strictEqual(FR.summarize([]).label, 'DATA OK');
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

// ── the short vocabulary (the kiosk's system-bar cell) ──────────────────────
// It prints after a "DATA" key, so it says the state and nothing else. These
// assertions are what stop it drifting into a SECOND vocabulary for the same
// facts — the failure this module was written to prevent, one size down.

test('every state has a short form, and it is the distinguishing part of the long one', () => {
  const pairs = {
    fresh: ['DATA OK', 'OK'],
    stale: ['OLD DATA', 'OLD'],
    deferred: ['ON HOLD', 'ON HOLD'],
    missing: ['NO DATA', 'MISSING'],
    unconfigured: ['OFF', 'OFF'],
  };
  for (const [state, [long, short]] of Object.entries(pairs)) {
    const s = FR.summarize([src('a', state, 9)]);
    // summarize() reports the WORST state; a lone source of that state is it,
    // except fresh-vs-unconfigured which both resolve to fresh by design.
    if (state === 'unconfigured') { continue; }
    assert.strictEqual(s.label, long, state + ' long label');
    assert.strictEqual(s.short, short, state + ' short label');
  }
});

test('the short form never repeats the key it sits under', () => {
  // "DATA · DATA OK" and "DATA · NO DATA" both stutter. The long labels are
  // built to stand alone; these are built to follow a label.
  for (const state of ['fresh', 'stale', 'deferred', 'missing']) {
    const s = FR.summarize([src('a', state, 9)]);
    assert.ok(!/DATA/.test(s.short),
      state + ' short label repeats DATA: ' + s.short);
    assert.strictEqual(s.short, s.short.toUpperCase(), state + ' must be upper case');
    assert.ok(s.short.length <= 7,
      state + ' short label is too wide for a stats-bar cell: ' + s.short);
  }
});

test('stale is named for what is wrong, not for how alarmed to be', () => {
  // WARNING says "be concerned" and leaves the operator guessing at what. OLD
  // says the thing they need to act on. Same rule as the long label's refusal
  // of STALLED.
  const s = FR.summarize([src('a', 'stale', 9)]);
  assert.ok(!/WARN/.test(s.short), 'stale must not be called WARNING');
  assert.ok(!/STALL/.test(s.short));
});
