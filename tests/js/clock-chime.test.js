'use strict';
// Unit tests for the dashboard's hour bell.
//
// Every failure here is silent in the field. A bell that never rings looks
// exactly like an hour that has not arrived; one that rings through quiet hours
// is only discovered at 03:00; and a phrase built wrong is not visible anywhere
// on screen — the operator simply hears the wrong time and has no reason to
// doubt it.
const test = require('node:test');
const assert = require('node:assert');
const C = require('../../common/js/clock-chime.js');

// 2026-08-10T18:00:00Z. `parts` are passed explicitly everywhere, so these tests
// do not depend on the host's timezone.
const H18 = Date.UTC(2026, 7, 10, 18, 0, 0);
const HOUR = 3600000;
const parts = (utcH, locH, locM) => ({ utcH, locH, locM: locM || 0 });

// A state that has already seen the previous hour, which is the steady state
// after the first tick of a session.
const seen = ms => ({ key: Math.floor((ms - HOUR) / 3600000) });

test('the cycle is off then chime then voice, and wraps', () => {
  assert.strictEqual(C.oasisClockNextMode('off'), 'chime');
  assert.strictEqual(C.oasisClockNextMode('chime'), 'voice');
  assert.strictEqual(C.oasisClockNextMode('voice'), 'off');
  // Junk in localStorage must land somewhere sane rather than wedging the
  // control on a state the operator cannot cycle out of.
  assert.strictEqual(C.oasisClockNextMode('nonsense'), 'chime');
  assert.strictEqual(C.oasisClockNextMode(undefined), 'chime');
});

test('quiet hours run 22:00 to 07:00 LOCAL', () => {
  [22, 23, 0, 3, 6].forEach(h => assert.strictEqual(C.oasisClockQuiet(h), true, `${h}`));
  [7, 12, 18, 21].forEach(h => assert.strictEqual(C.oasisClockQuiet(h), false, `${h}`));
});

test('chime mode strikes the hour and says nothing', () => {
  const r = C.oasisClockDue(H18, parts(18, 11), 'chime', 0, seen(H18));
  assert.strictEqual(r.chime, true);
  assert.strictEqual(r.speak, '');
});

test('voice mode strikes AND speaks', () => {
  const r = C.oasisClockDue(H18, parts(18, 11), 'voice', 0, seen(H18));
  assert.strictEqual(r.chime, true);
  assert.strictEqual(r.speak, 'The time is eighteen hundred Zulu. Local time is eleven hundred.');
});

test('off does nothing at all', () => {
  const r = C.oasisClockDue(H18, parts(18, 11), 'off', 0, seen(H18));
  assert.strictEqual(r.chime, false);
  assert.strictEqual(r.speak, '');
});

test('the hour fires once, not on every tick for an hour', () => {
  // The bug this guards: a 1 s tick striking the bell 3600 times.
  const st = seen(H18);
  assert.strictEqual(C.oasisClockDue(H18, parts(18, 11), 'voice', 0, st).chime, true);
  assert.strictEqual(C.oasisClockDue(H18 + 1000, parts(18, 11), 'voice', 0, st).chime, false);
  assert.strictEqual(C.oasisClockDue(H18 + 59 * 60000, parts(18, 11), 'voice', 0, st).chime, false);
  assert.strictEqual(C.oasisClockDue(H18 + HOUR, parts(19, 12), 'voice', 0, st).chime, true);
});

test('a page opened mid-hour does not announce the hour it missed', () => {
  // Same rule as a satellite pass already risen: an alert about something that
  // has already happened is noise. The first tick only records where we are.
  const st = {};
  assert.strictEqual(C.oasisClockDue(H18 + 30000, parts(18, 11), 'voice', 0, st).chime, false);
  assert.strictEqual(C.oasisClockDue(H18 + 31000, parts(18, 11), 'voice', 0, st).chime, false);
  assert.strictEqual(C.oasisClockDue(H18 + HOUR, parts(19, 12), 'voice', 0, st).chime, true);
});

test('quiet hours suppress the strike, by LOCAL hour not UTC', () => {
  // 06:00 local is inside quiet hours even though 13:00 Zulu plainly is not.
  // Reading quiet hours off the UTC hour would invert the whole feature.
  const r = C.oasisClockDue(H18, parts(13, 6), 'voice', 0, seen(H18));
  assert.strictEqual(r.chime, false);
  assert.strictEqual(r.speak, '');
});

test('an override defeats quiet hours', () => {
  const r = C.oasisClockDue(H18, parts(13, 6), 'voice', H18 + HOUR, seen(H18));
  assert.strictEqual(r.chime, true);
});

test('an EXPIRED override does not', () => {
  const r = C.oasisClockDue(H18, parts(13, 6), 'voice', H18 - 1000, seen(H18));
  assert.strictEqual(r.chime, false);
});

test('a quiet hour still advances the state — no backlog when it ends', () => {
  // If the hour were left unrecorded while suppressed, 07:00 would fire for
  // 06:00, which the operator would hear as the bell being an hour wrong.
  const st = seen(H18);
  C.oasisClockDue(H18, parts(13, 6), 'voice', 0, st);            // suppressed
  const same = C.oasisClockDue(H18 + 60000, parts(13, 6), 'voice', H18 + HOUR, st);
  assert.strictEqual(same.chime, false, 'the suppressed hour must not fire late');
});

test('an override expires at the NEXT 07:00 local', () => {
  // Timezone-independent: assert the shape, not an absolute instant.
  const night = new Date(2026, 7, 10, 22, 30, 0);       // armed for a contest
  const end = new Date(C.oasisClockOverrideUntil(night));
  assert.strictEqual(end.getHours(), 7);
  assert.strictEqual(end.getMinutes(), 0);
  assert.ok(end.getTime() > night.getTime());
  assert.ok(end.getTime() - night.getTime() < 12 * HOUR, 'the same night, not next week');

  const small = new Date(2026, 7, 11, 2, 0, 0);          // armed after midnight
  const end2 = new Date(C.oasisClockOverrideUntil(small));
  assert.strictEqual(end2.getHours(), 7);
  assert.strictEqual(end2.getDate(), 11, 'this morning, not tomorrow morning');
});

test('the spoken hour is words, never digits', () => {
  // "18:00" through Piper is a coin flip — "eighteen colon zero zero" is a real
  // outcome. Nothing in the sentence may be a numeral.
  const s = C.oasisClockPhrase(parts(18, 11));
  assert.match(s, /^The time is eighteen hundred Zulu\. Local time is eleven hundred\.$/);
  assert.doesNotMatch(s, /[0-9:]/);
});

test('the small hours read as military, and midnight is midnight', () => {
  assert.match(C.oasisClockPhrase(parts(1, 1)), /oh one hundred Zulu/);
  assert.match(C.oasisClockPhrase(parts(9, 9)), /oh nine hundred Zulu/);
  assert.match(C.oasisClockPhrase(parts(0, 0)), /^The time is midnight Zulu\. Local time is midnight\.$/);
  assert.match(C.oasisClockPhrase(parts(23, 23)), /twenty three hundred Zulu/);
});

test('a half-hour timezone says the half hour rather than rounding into a lie', () => {
  // India is UTC+05:30: the top of the Zulu hour is 23:30 locally. Saying
  // "twenty three hundred" there would be wrong by half an hour, every hour.
  const s = C.oasisClockPhrase(parts(18, 23, 30));
  assert.match(s, /Local time is twenty three thirty\.$/);
  assert.match(C.oasisClockPhrase(parts(18, 5, 45)), /Local time is oh five forty five\.$/);
  assert.match(C.oasisClockPhrase(parts(18, 0, 30)), /Local time is zero zero thirty\.$/);
});

test('every hour of the day produces a sayable phrase', () => {
  for (let h = 0; h < 24; h++) {
    const s = C.oasisClockPhrase(parts(h, h));
    assert.doesNotMatch(s, /[0-9:]/, `hour ${h}`);
    assert.doesNotMatch(s, /undefined|NaN/, `hour ${h}`);
  }
});

test('parts are read off one Date, UTC and local kept apart', () => {
  const d = new Date(H18);
  const p = C.oasisClockParts(d);
  assert.strictEqual(p.utcH, 18);
  assert.strictEqual(p.locH, d.getHours());
  assert.strictEqual(p.locM, d.getMinutes());
});
