'use strict';
// The two bottom-card headers open the same way: a glyph, then a label.
//
//   TRAFFIC:  (dot)  LIVE  384  <=24h  RF ...
//   SAT:      (bell) SATELLITES  ...
//
// They are read together — side by side on a desktop panel, one after the other
// as you fold between them on a 7-inch one — so a difference between them reads
// as drift, not as design. The two things that have to agree are the SIZE of
// that leading glyph and its POSITION, and neither is visible from the JS.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(
  path.join(__dirname, '..', '..', 'oasis-dashboard', 'dashboard.html'), 'utf8');

// A rule body, matched on a WHOLE selector at the start of its line — otherwise
// `.pulse` finds `.sat.soon .pulse` first and reports on the override instead of
// the rule that sets the size.
function rule(selector) {
  // Anchored on a WHOLE selector — at the start of its line, or after another
  // rule on the same line. A bare indexOf('.pulse{') finds `.sat.soon .pulse{`
  // first and reports on the override instead of the rule that sets the size.
  let at = page.indexOf('\n  ' + selector + '{');
  if (at === -1) at = page.indexOf('} ' + selector + '{');
  assert.notStrictEqual(at, -1, 'no rule for ' + selector);
  return page.slice(at, page.indexOf('}', page.indexOf(selector + '{', at)) + 1);
}

test('the two station dots are one size, from one variable', () => {
  // DOTS MATCH DOTS. The LIVE indicator and the pulse that leads a satellite
  // record whose pass is close are the same shape doing the same job, so they
  // are the same size — and neither rule may hold its own number, or the next
  // change to one silently un-matches the other.
  //
  // Sizing the LIVE dot against the mute bell beside it was tried first and was
  // wrong: a filled disc carries more ink than a line-drawn bell, so equal
  // boxes read as unequal weights and the dot looked outsized next to every
  // other dot on the screen.
  assert.match(page, /--station-dot:[\d.]+rem;/, 'the shared dot size is gone');
  for (const sel of ['.livebadge .lvd', '.pulse']) {
    const r = rule(sel);
    assert.match(r, /width:var\(--station-dot\)/, sel + ' must take the shared size');
    assert.match(r, /height:var\(--station-dot\)/,
      sel + ': a dot that is not round had its width and height set apart');
  }
});

test('the dot is drawn, not typed', () => {
  // It was a ● at .85rem. A glyph\'s disc is an unknown fraction of its em, so
  // matching a 1.3rem icon through font-size is a guess that lands differently
  // on whatever font the Pi actually has. A border-radius box is exact.
  assert.match(rule('.livebadge .lvd'), /border-radius:50%/,
    'the dot must be a drawn circle');
  assert.ok(!/class="lvd">[^<]/.test(page),
    'the dot span must be empty — a glyph inside a drawn circle shows through');
  const at = page.indexOf('id="k-aprs-live"');
  const badge = page.slice(at, page.indexOf('</span></span>', at) + 14);
  const bad = [...badge].filter(c => c.codePointAt(0) >= 0x2000);
  assert.deepStrictEqual(bad, [], 'the LIVE badge still carries a glyph');
});

test('the glow is a static shadow, never an animation', () => {
  // An infinite box-shadow animation pins this kiosk\'s renderer at 60fps. The
  // dot glows; it does not pulse.
  const glow = rule('.livebadge.ok .lvd');
  assert.match(glow, /box-shadow:/, 'the ok state should still glow');
  assert.ok(!/animation/.test(glow), 'never animate a box-shadow on this screen');
});

test('the mute bell leads the satellites header', () => {
  const hd = page.slice(page.indexOf('<div class="card sats">'));
  const header = hd.slice(hd.indexOf('<div class="hd"'), hd.indexOf('</div>\n'));
  const bell = header.indexOf('id="k-sat-mute"');
  const label = header.indexOf('id="k-sat-title"');
  assert.ok(bell !== -1 && label !== -1, 'the satellites header lost a control');
  assert.ok(bell < label,
    'the bell must come BEFORE the SATELLITES label, matching the traffic ' +
    'card\'s dot-then-LIVE');
});

test('the bell sits flush with the card edge, hit area intact', () => {
  // Padding grows the finger target; matching negative margins put the pixels
  // back, so the icon lines up with the traffic dot instead of being indented
  // by its own padding. Lose the margins and the two headers start at
  // different x.
  const bell = rule('.satmute');
  assert.match(bell, /padding:\.5rem \.6rem/, 'the finger-sized target is gone');
  assert.match(bell, /margin:-\.5rem -\.6rem -\.5rem -\.6rem/,
    'the horizontal padding must be cancelled on BOTH sides now that the bell ' +
    'leads the row');
  assert.match(bell, /min-width:2\.75rem; min-height:2\.75rem/,
    'this is tapped in the dark — do not shrink it');
});

test('the pass counter is a chip beside the title, not a stray readout on the right', () => {
  // Traffic reads: glyph, label, then the numbers that qualify it -- 384, <=24h
  // -- each one header-gap apart. Satellites now reads the same way, with its
  // "N moni - M in 1h" in the same pill shape at the same distance. It used to
  // sit far right inside .hd-right, which put the same KIND of information in a
  // different PLACE on the two cards that are read side by side.
  const hd = page.slice(page.indexOf('<div class="card sats">'));
  const header = hd.slice(hd.indexOf('<div class="hd"'), hd.indexOf('</div>\n'));
  const title = header.indexOf('id="k-sat-title"');
  const meta = header.indexOf('id="k-sat-meta"');
  const group = header.indexOf('class="hd-right"');
  assert.ok(title !== -1 && meta !== -1 && group !== -1, 'the satellites header lost a part');
  assert.ok(title < meta, 'the counter must follow the SATELLITES label');
  assert.ok(meta < group, 'the counter belongs on the LEFT, not in the right-hand group');
  assert.match(header, /class="fchip satmeta" id="k-sat-meta"/,
    'it must wear the traffic header\'s chip, not a shape of its own');
});

test('the counter chip frames like the age window but does not pretend to be one', () => {
  const chip = rule('.fchip.satmeta');
  assert.match(chip, /--src:var\(--warn\)/, 'amber frame, copied from the age window');
  assert.match(chip, /color:var\(--dim\)/,
    'dim contents: on .fchip the coloured text IS the affordance, and this one ' +
    'is a readout that does nothing');
  assert.match(chip, /cursor:default/,
    '.fchip sets cursor:pointer — a readout must not promise a control');
  assert.ok(!/\.hd \.meta\{/.test(page), 'the old .meta rule is dead and should be gone');
});
