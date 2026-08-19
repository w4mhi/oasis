'use strict';
// The shared password eye-toggle used by setup.html (Winlink, GrayWolf, Wi-Fi)
// and the Wi-Fi connect form in index.html + the kiosk.
//
// attach()/initAll() need a DOM, and this project has no jsdom (no npm, offline
// first), so the decision logic lives in a pure revealState() and that is what
// is covered here. The glyph assertions are not decoration: an emoji here would
// render as tofu on the Pi, which is the recorded failure these SVGs exist to
// avoid.
const test = require('node:test');
const assert = require('node:assert');
const P = require('../../common/js/password-reveal.js').PasswordReveal;

test('revealState: hidden is the safe default shape', () => {
  const s = P.revealState(false);
  assert.strictEqual(s.type, 'password');
  assert.strictEqual(s.pressed, 'false');
  assert.strictEqual(s.label, 'Show password');
  assert.strictEqual(s.glyph, P.EYE);
});

test('revealState: shown flips every facet together', () => {
  const s = P.revealState(true);
  assert.strictEqual(s.type, 'text');
  assert.strictEqual(s.pressed, 'true');
  assert.strictEqual(s.label, 'Hide password');
  assert.strictEqual(s.glyph, P.EYE_OFF);
});

test('revealState: aria-pressed is a string, not a boolean', () => {
  // setAttribute stringifies anyway, but `false` would arrive as "false" only
  // by luck; being explicit keeps the attribute honest.
  assert.strictEqual(typeof P.revealState(true).pressed, 'string');
  assert.strictEqual(typeof P.revealState(false).pressed, 'string');
});

test('revealState: the label always names the NEXT action', () => {
  // A button reading "Show password" while the password is already shown is
  // the classic toggle bug, and it misleads a screen reader too.
  assert.strictEqual(P.revealState(false).label, 'Show password');
  assert.strictEqual(P.revealState(true).label, 'Hide password');
});

test('revealState: type is only ever password or text', () => {
  for (const shown of [true, false]) {
    assert.ok(['password', 'text'].includes(P.revealState(shown).type));
  }
});

test('glyphs: no emoji — the Pi has no emoji font and would render tofu', () => {
  for (const [name, svg] of [['EYE', P.EYE], ['EYE_OFF', P.EYE_OFF]]) {
    const bad = [...svg].filter(c => c.codePointAt(0) >= 0x1f000);
    assert.deepStrictEqual(bad, [], `${name} contains emoji codepoints`);
  }
});

test('glyphs: inline SVG that inherits the page colour', () => {
  for (const [name, svg] of [['EYE', P.EYE], ['EYE_OFF', P.EYE_OFF]]) {
    assert.ok(svg.startsWith('<svg '), `${name} is not inline SVG`);
    assert.ok(svg.endsWith('</svg>'), `${name} is not closed`);
    assert.ok(svg.includes('fill="currentColor"'), `${name} hardcodes a colour`);
    assert.ok(svg.includes('viewBox="0 0 24 24"'), `${name} is off the 24x24 grid`);
  }
});

test('glyphs: art is aria-hidden, since the button carries the label', () => {
  assert.ok(P.EYE.includes('aria-hidden="true"'));
  assert.ok(P.EYE_OFF.includes('aria-hidden="true"'));
});

test('glyphs: the two states are actually distinguishable', () => {
  assert.notStrictEqual(P.EYE, P.EYE_OFF);
});

test('glyphs: no curly quotes, which break attributes inside a tag', () => {
  for (const svg of [P.EYE, P.EYE_OFF]) {
    assert.ok(!/[“”‘’]/.test(svg));
  }
});

test('module exposes the DOM entry points pages call', () => {
  assert.strictEqual(typeof P.attach, 'function');
  assert.strictEqual(typeof P.initAll, 'function');
});
