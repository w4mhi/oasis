'use strict';
// The kiosk's bottom-row accordion: TRAFFIC and SAT stack on a small panel and
// exactly one is open, the other reporting from a one-line strip.
//
// Everything worth breaking here is invisible from the JS alone:
//
//   * the fold must apply ONLY below 1100px. Above it the two lists sit side by
//     side with room to spare, and a rule that leaked out of the media query
//     would fold a 1920x1200 wall display that never asked to be folded.
//   * collapsing must hide the DOM and NOT stop the data. The strip reports
//     from the same render pass the list does, so a render skipped "to save
//     work" leaves the strip printing a number that stopped being true.
//   * the strip must name an active filter. The chips that explain the count
//     fold away with the list.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(
  path.join(__dirname, '..', '..', 'oasis-dashboard', 'dashboard.html'), 'utf8');

// The @media (max-width:1100px) block, brace-matched.
function smallScreenBlock() {
  const start = page.indexOf('@media (max-width:1100px){');
  assert.notStrictEqual(start, -1, 'the 1100px query is gone — did the breakpoint move?');
  let depth = 0, i = page.indexOf('{', start);
  for (let j = i; j < page.length; j++) {
    if (page[j] === '{') depth++;
    else if (page[j] === '}') { depth--; if (depth === 0) return page.slice(start, j + 1); }
  }
  throw new Error('unbalanced braces in the 1100px query');
}

// CSS with its comments removed. The comments in this file argue AGAINST the
// things the rules must not contain ("no animation here"), so a naive grep over
// the raw block finds the warning and calls it the violation.
function decomment(css) { return css.replace(/\/\*[\s\S]*?\*\//g, ''); }

// A card's markup, from its opening div to the next card / row end.
function strip(id) {
  const at = page.indexOf('id="' + id + '"');
  assert.notStrictEqual(at, -1, 'no ' + id + ' strip');
  const open = page.lastIndexOf('<div', at);
  return page.slice(open, page.indexOf('</div>', at) + 6);
}

test('the fold applies only below 1100px', () => {
  const small = smallScreenBlock();
  for (const rule of ['.row.bot{ flex-direction:column; }', '.sats{ width:auto; }']) {
    assert.ok(small.includes(rule), 'missing from the small-screen query: ' + rule);
    assert.strictEqual(page.split(rule).length - 1, 1,
      rule + ' appears more than once — one of them is outside the query, and ' +
      'that one folds the 1920 panel');
  }
  // Every [data-open] rule must be inside. This is the regression that would be
  // invisible until someone looked at the big wall display.
  const total = (page.match(/\[data-open=/g) || []).length;
  const inside = (small.match(/\[data-open=/g) || []).length;
  assert.strictEqual(inside, total,
    (total - inside) + ' [data-open] rule(s) escaped the 1100px query');
});

test('the strip is invisible until the query shows it', () => {
  assert.match(page, /\.foldstrip\{ display:none;/,
    'the collapsed face must default to display:none, or it doubles every card ' +
    'header on the panel that is not folded');
});

test('both strips are real buttons that open their own card', () => {
  for (const [id, which] of [['fold-traffic', 'traffic'], ['fold-sat', 'sat']]) {
    const el = strip(id);
    assert.match(el, new RegExp('onclick="foldOpen\\(\'' + which + '\'\\)"'),
      id + ' does not open ' + which);
    assert.match(el, /role="button"/, id + ' is not announced as a button');
    assert.match(el, /tabindex="0"/, id + ' cannot be reached');
    assert.match(el, /onkeydown=/, id + ' answers a click but not a key');
    assert.match(el, /class="fs-head" id="fold-\w+-head"/, id + ' has no headline');
  }
});

test('the row remembers which card was open', () => {
  assert.match(page, /<div class="row bot" id="botrow" data-open="traffic">/,
    'the row must carry the state attribute, and start on traffic');
  assert.match(page, /const FOLD_KEY = 'oasis_kiosk_bot_open';/);
  assert.match(page, /localStorage\.setItem\(FOLD_KEY, which\)/, 'the choice is not saved');
  assert.match(page, /localStorage\.getItem\(FOLD_KEY\)/, 'the choice is not restored');
  assert.match(page, /foldOpen\(saved === 'sat' \? 'sat' : 'traffic'\)/,
    'anything that is not an explicit "sat" must fall back to traffic');
});

test('collapsing does not stop the data: both strips are written by the render pass', () => {
  // Traffic: between the count it shares and the rows it draws.
  const count = page.indexOf("getElementById('k-aprs-count')");
  const rows = page.indexOf('renderAprsRows(list.slice(', count);
  assert.ok(count !== -1 && rows > count, 'applyAprsFilter no longer looks the same');
  const between = page.slice(count, rows);
  assert.match(between, /foldHead\('traffic'/,
    'the traffic strip is not updated by the render that counts the rows — it ' +
    'will report a stale number the moment the list is folded');

  // Satellites: from the same sorted `items` the cards are built from.
  const meta = page.indexOf("meta.textContent = _satSel.length");
  const body = page.indexOf('body.innerHTML = items.map', meta);
  assert.ok(meta !== -1 && body > meta, 'renderSats no longer looks the same');
  assert.match(page.slice(meta, body), /foldHead\('sat'/,
    'the sat strip is not updated where the passes are');
  assert.match(page, /const it0 = items\[0\];/,
    'the headline must come from the sorted list, not a second computation');
});

test('the strip says WHICH filter produced the count', () => {
  const count = page.indexOf("getElementById('k-aprs-count')");
  const block = page.slice(count, page.indexOf('renderAprsRows(list.slice(', count));
  assert.match(block, /_ageMin !== _AGE_STEPS\[0\]/,
    'a non-default age window must be named — the chip that says so is folded away');
  assert.match(block, /srcSelected\.length/,
    'an active source narrowing must be named for the same reason');
  assert.match(block, /_SRC_LABEL\[k\]/,
    'the strip must use the chips\' own labels, not a second set of names');
});

test('an overhead bird still colours the strip', () => {
  assert.match(page, /it0\.inPass \? 'now' : \(it0\.mins <= 10 \? 'soon' : ''\)/,
    'folding the sat card must not hide that a pass is happening');
  const small = smallScreenBlock();
  assert.match(page, /\.foldstrip \.fs-head\.now\{ color:var\(--accent\); \}/);
  assert.match(page, /\.foldstrip \.fs-head\.soon\{ color:var\(--warn\); \}/);
  assert.ok(small.includes('grid-template-columns:1fr 1fr'),
    'the open sat card should spend its new width on a second column');
});

test('the fold costs no frames and no fonts', () => {
  // A transition here is a Chromium renderer at 60fps on a Pi that is already
  // painting a map — the same trap as the box-shadow pulse.
  const small = decomment(smallScreenBlock());
  assert.ok(!/transition|animation/.test(small),
    'no animation in the fold: this panel cannot afford it');
  // Pi OS Lite ships no emoji font. The caret is drawn with borders precisely so
  // no glyph can turn into tofu.
  assert.match(page, /\.foldstrip \.fs-car\{[^}]*border-left:/,
    'the caret must be drawn, not typed');
  for (const id of ['fold-traffic', 'fold-sat']) {
    const bad = [...strip(id)].filter(c => c.codePointAt(0) >= 0x1F000);
    assert.deepStrictEqual(bad, [], id + ' carries a glyph the panel cannot render');
  }
});
