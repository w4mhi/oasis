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
  assert.deepStrictEqual(r.speak, []);
});

test('voice mode strikes AND speaks, as TWO separate utterances', () => {
  // Two, not one: the caller puts a second of silence between them, which it
  // cannot do to a single string. An empty array is truthy, so the caller tests
  // .length — a regression to one joined string would still "work" and quietly
  // lose the pause, which is why this asserts the shape and not just the words.
  const r = C.oasisClockDue(H18, parts(18, 11), 'voice', 0, seen(H18));
  assert.strictEqual(r.chime, true);
  // Each utterance carries the readable label its cached WAV gets on the server,
  // travelling WITH its text — two parallel lists is how a label ends up on the
  // wrong phrase.
  assert.deepStrictEqual(r.speak, [
    { text: 'The time is eighteen hundred Zulu.', kind: 'TIME_UTC_1800' },
    { text: 'Local time is eleven hundred.',      kind: 'TIME_LOC_1100' },
  ]);
});

test('the local label carries MINUTES, because the bell fires on the UTC hour', () => {
  // In a half-hour zone the top of the UTC hour is 04:30 locally, and a label
  // saying 0400 would be a lie.
  const said = C.oasisClockSpeech({ utcH: 18, locH: 4, locM: 30 });
  assert.strictEqual(said[0].kind, 'TIME_UTC_1800');
  assert.strictEqual(said[1].kind, 'TIME_LOC_0430');
});

test('clock labels are filename-safe: no colon, no separator', () => {
  // ':' is legal on Linux and nowhere else that matters — illegal on FAT/exFAT,
  // and it needs quoting in every shell command touching the cache.
  for (let h = 0; h < 24; h++) {
    C.oasisClockSpeech({ utcH: h, locH: h, locM: h % 2 ? 30 : 0 })
      .forEach(it => assert.match(it.kind, /^[A-Z][A-Z0-9_]{0,31}$/));
  }
});

test('off does nothing at all', () => {
  const r = C.oasisClockDue(H18, parts(18, 11), 'off', 0, seen(H18));
  assert.strictEqual(r.chime, false);
  assert.deepStrictEqual(r.speak, []);
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
  assert.deepStrictEqual(r.speak, []);
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

test('each clock is its own sentence, and each stands alone', () => {
  // Split so the pause has somewhere to go — and each half has to be a whole
  // sentence, because a listener who misses the first one still has to be able
  // to make sense of the second.
  const [zulu, local] = C.oasisClockPhrases(parts(18, 11));
  assert.strictEqual(zulu, 'The time is eighteen hundred Zulu.');
  assert.strictEqual(local, 'Local time is eleven hundred.');
});

test('the spoken hour is words, never digits', () => {
  // "18:00" through Piper is a coin flip — "eighteen colon zero zero" is a real
  // outcome. Nothing in either sentence may be a numeral.
  C.oasisClockPhrases(parts(18, 11)).forEach(s => assert.doesNotMatch(s, /[0-9:]/));
});

test('the small hours read as military, and midnight is midnight', () => {
  const z = p => C.oasisClockPhrases(p)[0], l = p => C.oasisClockPhrases(p)[1];
  assert.match(z(parts(1, 1)), /oh one hundred Zulu/);
  assert.match(z(parts(9, 9)), /oh nine hundred Zulu/);
  assert.strictEqual(z(parts(0, 0)), 'The time is midnight Zulu.');
  assert.strictEqual(l(parts(0, 0)), 'Local time is midnight.');
  assert.match(z(parts(23, 23)), /twenty three hundred Zulu/);
});

test('a half-hour timezone says the half hour rather than rounding into a lie', () => {
  // India is UTC+05:30: the top of the Zulu hour is 23:30 locally. Saying
  // "twenty three hundred" there would be wrong by half an hour, every hour.
  const l = p => C.oasisClockPhrases(p)[1];
  assert.strictEqual(l(parts(18, 23, 30)), 'Local time is twenty three thirty.');
  assert.strictEqual(l(parts(18, 5, 45)), 'Local time is oh five forty five.');
  assert.strictEqual(l(parts(18, 0, 30)), 'Local time is zero zero thirty.');
});

test('every hour of the day produces two sayable sentences', () => {
  for (let h = 0; h < 24; h++) {
    const said = C.oasisClockPhrases(parts(h, h));
    assert.strictEqual(said.length, 2, `hour ${h}`);
    said.forEach(s => {
      assert.doesNotMatch(s, /[0-9:]/, `hour ${h}`);
      assert.doesNotMatch(s, /undefined|NaN/, `hour ${h}`);
      assert.match(s, /\.$/, `hour ${h}: each half is a sentence`);
    });
  }
});

test('the gap between the two is a real, non-zero pause', () => {
  // A zero here would collapse the split back into one run-on announcement
  // without anything else failing.
  assert.ok(C.OASIS_CLOCK_GAP_MS >= 500, 'long enough to hear as deliberate');
  assert.ok(C.OASIS_CLOCK_GAP_MS <= 2000, 'short enough to stay one announcement');
});

test('parts are read off one Date, UTC and local kept apart', () => {
  const d = new Date(H18);
  const p = C.oasisClockParts(d);
  assert.strictEqual(p.utcH, 18);
  assert.strictEqual(p.locH, d.getHours());
  assert.strictEqual(p.locM, d.getMinutes());
});

// ── where the bell lives on the kiosk ───────────────────────────────────────
// Markup, not behaviour, but the failure mode is the same kind: silent. The
// bell moved from the UTC card's bottom-right corner up beside the reload
// glyph, and both buttons sit inside one positioned group instead of being two
// absolutes that each had to know where the other ended.
{
  const fs = require('node:fs');
  const path = require('node:path');
  const page = fs.readFileSync(
    path.join(__dirname, '..', '..', 'oasis-dashboard', 'dashboard.html'), 'utf8');

  test('the hour bell sits immediately left of the reload glyph', () => {
    const group = page.slice(page.indexOf('<div class="clk-tools">'));
    const end = group.indexOf('</div>');
    assert.ok(end !== -1, 'no .clk-tools group on the UTC card');
    const inner = group.slice(0, end);
    const bell = inner.indexOf('class="clk-bell');
    const reload = inner.indexOf('class="clk-refresh"');
    assert.ok(bell !== -1 && reload !== -1, 'both controls must be in the group');
    assert.ok(bell < reload,
      'the reload keeps the corner it has always been in; the bell takes the ' +
      'space to its left');
  });

  test('neither control is absolutely positioned any more', () => {
    // Two absolutes in one corner means the second one is placed by arithmetic
    // over the first one's width — and the bell's box is a ~2.75rem finger
    // target around a 1.3rem glyph, so that arithmetic is not what it looks.
    assert.match(page, /\.clk-tools\{ position:absolute;/, 'the group must carry the position');
    assert.ok(!/\.clk-bell\{ position:absolute/.test(page), '.clk-bell still positions itself');
    assert.ok(!/\.clk-refresh\{ position:absolute/.test(page), '.clk-refresh still positions itself');
  });

  test('both corner glyphs are drawn, at one shared size', () => {
    // The reload was a typed glyph at font-size:2.4rem. An em is not a mark —
    // how much of it the font inks is the font's business — so it could not be
    // compared with an SVG box, let alone matched to one. Both are boxes now.
    assert.match(page, /--clk-glyph:[\d.]+rem;/, 'the shared glyph size is gone');
    assert.match(page,
      /\.clk-bell svg, \.clk-refresh svg\{ height:var\(--clk-glyph\); width:auto;/,
      'both glyphs must take the shared height');
    assert.ok(!/\.clk-refresh\{[^}]*font-size/.test(page),
      'the reload is sized by font-size again — that is an em, not a glyph');
    assert.match(page, /id="clk-refresh"[^>]*><svg viewBox="0 0 24 24"/,
      'the reload must be inline SVG');
  });

  test('sizing by height is what retires the wide bell\'s magic number', () => {
    // The bell+voice glyph has a 32-wide viewBox against everything else's 24.
    // width:auto lets the aspect ratio do that arithmetic, so its INK weighs the
    // same as its neighbours instead of matching a number somebody measured once.
    assert.ok(!/\.clk-bell\.wide svg\{/.test(page),
      'the hardcoded 1.75rem width is back; height + width:auto covers it');
  });

  test('the reload is as easy to hit as the bell beside it', () => {
    // It had .1rem of padding against the bell's finger-sized box. On a panel
    // that is tapped rather than clicked, that gap mattered more than the size
    // difference did — and shrinking the glyph to match would have made it worse.
    const reload = page.slice(page.indexOf('.clk-refresh{'));
    const body = reload.slice(0, reload.indexOf('}'));
    assert.match(body, /min-width:2\.75rem; min-height:2\.75rem/,
      'the reload must carry the same ~2.75rem target as the bell');
    assert.match(body, /padding:\.5rem \.6rem/, 'same padding as the bell, too');
  });

  test('a thumb in the gap between them does not repaint the clock', () => {
    // The whole face is a colour-cycle target. The two buttons were already
    // excluded; the GROUP has to be too, or a tap landing in its gap misses
    // both buttons and recolours the clock instead of doing nothing.
    assert.match(page, /closest\('\.clk-tools, \.clk-refresh, \.clk-bell'\)/,
      'the colour-cycle guard must cover the group as well as its buttons');
  });
}
