'use strict';
// Unit tests for common/js/quiet-hours.js — when the shack is asleep.
//
// One definition of the window, shared by the hour bell and satellite pass
// alerts. The two keep SEPARATE state on purpose but must never disagree about
// when night is, so the semantics are pinned here once.
const test = require('node:test');
const assert = require('node:assert');
const Q = require('../../common/js/quiet-hours.js');

const at = (h, m) => new Date(2026, 7, 11, h, m || 0, 0);

test('the window spans midnight', () => {
  // An OR, not a range test — 23:00 and 03:00 are both night.
  for (const h of [22, 23, 0, 3, 6]) assert.ok(Q.quietAt(h), `${h}:00 should be quiet`);
  for (const h of [7, 8, 12, 20, 21]) assert.ok(!Q.quietAt(h), `${h}:00 should not be`);
});

test('the boundaries are inclusive at 22 and exclusive at 07', () => {
  assert.ok(Q.quietAt(22), 'quiet starts AT 22:00');
  assert.ok(Q.quietAt(6), '06:xx is still quiet');
  assert.ok(!Q.quietAt(7), 'quiet ends AT 07:00');
});

test('an override set in the evening expires at the NEXT 07:00', () => {
  const until = new Date(Q.overrideUntil(at(23, 30)));
  assert.strictEqual(until.getHours(), 7);
  assert.strictEqual(until.getMinutes(), 0);
  assert.strictEqual(until.getDate(), 12, 'tomorrow morning, not today');
});

test('an override set after midnight expires the SAME morning', () => {
  // The trap: 03:00 is already past midnight, so "next 07:00" is today's.
  // Rolling the date forward here would leave the shack loud for 28 hours.
  const until = new Date(Q.overrideUntil(at(3, 15)));
  assert.strictEqual(until.getHours(), 7);
  assert.strictEqual(until.getDate(), 11, 'this morning');
});

test('an override is not permanent — it lapses on its own', () => {
  const now = at(23, 0);
  const until = Q.overrideUntil(now);
  assert.ok(Q.overrideActive(now.getTime(), until), 'active the moment it is set');
  assert.ok(!Q.overrideActive(until + 1, until), 'lapsed one ms after 07:00');
  assert.ok(!Q.overrideActive(now.getTime(), 0), 'never set is never active');
});

test('state(): quiet silences, and an override lifts it', () => {
  const night = at(23, 30), untilMorning = Q.overrideUntil(night);

  let s = Q.state(false, night.getHours(), night.getTime(), 0);
  assert.deepStrictEqual([s.quiet, s.overridden, s.silent], [true, false, true]);

  s = Q.state(false, night.getHours(), night.getTime(), untilMorning);
  assert.deepStrictEqual([s.quiet, s.overridden, s.silent], [true, true, false]);
});

test('state(): the mute outranks everything, including an override', () => {
  // Silent means silent. A mute the operator set must not be undone by a
  // quiet-hours override they set earlier the same night.
  const night = at(23, 30);
  const s = Q.state(true, night.getHours(), night.getTime(), Q.overrideUntil(night));
  assert.ok(s.silent);
});

test('state(): daytime with no mute is not silent', () => {
  const noon = at(12, 0);
  const s = Q.state(false, noon.getHours(), noon.getTime(), 0);
  assert.deepStrictEqual([s.quiet, s.overridden, s.silent], [false, false, false]);
});

test('state() reports quiet and overridden separately from silent', () => {
  // The bell needs all three: "you muted this" and "the clock did, until 07:00"
  // are different facts and must not render alike.
  const night = at(1, 0);
  const s = Q.state(false, night.getHours(), night.getTime(), Q.overrideUntil(night));
  assert.ok(s.quiet, 'it IS still night');
  assert.ok(s.overridden, 'but the operator is awake');
  assert.ok(!s.silent, 'so alerts sound');
});
