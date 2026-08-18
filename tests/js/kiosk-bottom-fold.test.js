'use strict';
// The kiosk's bottom-row accordion: on a small panel exactly ONE of TRAFFIC and
// SAT is on screen, and the folded one is reported by a pill inside the surviving
// card's header. One header, not two — a separate collapsed strip cost a row of
// chrome plus a row gap to say what fits in a pill.
//
// Everything worth breaking here is invisible from the JS alone:
//
//   * the fold must apply ONLY below 1100px. Above it the two lists sit side by
//     side with room to spare, and a rule that leaked out of the media query
//     would fold a 1920x1200 wall display that never asked to be folded.
//   * folding must hide the DOM and NOT stop the data. The pill reports from
//     the same render pass the list does, so a render skipped "to save work"
//     leaves the pill printing a number that stopped being true.
//   * the pill must name an active filter. The chips that explain the count go
//     off screen with the list.
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

// A fold pill's markup, from its own opening tag to the end of its subtree.
function pill(id) {
  const at = page.indexOf('id="' + id + '"');
  assert.notStrictEqual(at, -1, 'no ' + id + ' pill');
  const open = page.lastIndexOf('<span', at);
  return page.slice(open, page.indexOf('</span></span>', at) + 14);
}

// The header of a card, by its class.
function header(cardClass) {
  const card = page.indexOf('<div class="card ' + cardClass + '">');
  assert.notStrictEqual(card, -1, 'no ' + cardClass + ' card');
  const hd = page.indexOf('<div class="hd"', card);
  return page.slice(hd, page.indexOf('</div>\n', hd));
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

test('the pill is invisible until the query shows it', () => {
  assert.match(page, /\.foldpill\{ display:none;/,
    'the pill must default to display:none, or the 1920 panel grows a control ' +
    'for folding that never folds anything');
});

test('there is ONE header on a folded panel, not a header plus a strip', () => {
  const small = smallScreenBlock();
  // The folded card leaves the screen outright. Anything short of display:none
  // (a zero-height strip, a hidden child list) still costs its border and the
  // row gap, which is the row this design exists to give back.
  assert.match(small, /\.row\.bot\[data-open="traffic"\] \.sats,\s*\n\s*\.row\.bot\[data-open="sat"\] \.traffic\{ display:none; \}/,
    'the folded card must be display:none — a collapsed-but-present card gives ' +
    'back the row the pill just saved');
  assert.ok(!/foldstrip/.test(page), 'the old collapsed strip is still here');
});

test('each pill lives in the OTHER list\'s header', () => {
  // The SAT pill has to be in the traffic header and vice versa: a pill inside
  // the card it describes goes off screen exactly when it is needed.
  assert.ok(header('traffic').includes('id="fold-sat"'),
    'the SAT pill must sit in the traffic header');
  assert.ok(header('sats').includes('id="fold-traffic"'),
    'the TRAFFIC pill must sit in the satellites header');
});

test('both pills are real buttons that open the card they name', () => {
  for (const [id, which] of [['fold-traffic', 'traffic'], ['fold-sat', 'sat']]) {
    const el = pill(id);
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

test('folding does not stop the data: both pills are written by the render pass', () => {
  // Traffic: between the count it shares and the rows it draws.
  const count = page.indexOf("getElementById('k-aprs-count')");
  const rows = page.indexOf('renderAprsRows(list.slice(', count);
  assert.ok(count !== -1 && rows > count, 'applyAprsFilter no longer looks the same');
  const between = page.slice(count, rows);
  assert.match(between, /foldHead\('traffic'/,
    'the traffic pill is not updated by the render that counts the rows — it ' +
    'will report a stale number the moment the list is folded');

  // Satellites: from the same sorted `items` the cards are built from.
  const meta = page.indexOf("meta.textContent = _satSel.length");
  const body = page.indexOf('body.innerHTML = items.map', meta);
  assert.ok(meta !== -1 && body > meta, 'renderSats no longer looks the same');
  assert.match(page.slice(meta, body), /foldHead\('sat'/,
    'the sat pill is not updated where the passes are');
  assert.match(page, /const it0 = items\[0\];/,
    'the headline must come from the sorted list, not a second computation');
});

test('the pill says WHICH filter produced the count', () => {
  const count = page.indexOf("getElementById('k-aprs-count')");
  const block = page.slice(count, page.indexOf('renderAprsRows(list.slice(', count));
  assert.match(block, /_ageMin !== _AGE_STEPS\[0\]/,
    'a non-default age window must be named — the chip that says so goes off screen');
  assert.match(block, /srcSelected\.length/,
    'an active source narrowing must be named for the same reason');
  assert.match(block, /_SRC_LABEL\[k\]/,
    'the pill must use the chips\' own labels, not a second set of names');
});

test('an overhead bird still colours the pill', () => {
  assert.match(page, /it0\.inPass \? 'now' : \(it0\.mins <= 10 \? 'soon' : ''\)/,
    'folding the sat card must not hide that a pass is happening');
  assert.match(page, /\.foldpill \.fs-head\.now\{ color:var\(--accent\); \}/);
  assert.match(page, /\.foldpill \.fs-head\.soon\{ color:var\(--warn\); \}/);
});

test('a lone bird fills the width instead of sitting in half of it', () => {
  const small = smallScreenBlock();
  // auto-fit, not `1fr 1fr`. A fixed pair of tracks leaves ONE pass in the left
  // half of a 769px card with nothing beside it — which is what "the list is not
  // using the width" looks like. auto-fit collapses the empty track.
  assert.match(small, /grid-template-columns:repeat\(auto-fit, minmax\(20rem, 1fr\)\)/,
    'the sat grid must auto-fit, or a single bird occupies half the pane');
  assert.ok(!/grid-template-columns:1fr 1fr/.test(small),
    'a fixed two-track grid cannot collapse its empty column');
});

test('the fold costs no frames and no fonts', () => {
  // A transition here is a Chromium renderer at 60fps on a Pi that is already
  // painting a map — the same trap as the box-shadow pulse.
  const small = decomment(smallScreenBlock());
  assert.ok(!/transition|animation/.test(small),
    'no animation in the fold: this panel cannot afford it');
  // Pi OS Lite ships no emoji font. The caret is drawn with borders precisely so
  // no glyph can turn into tofu.
  assert.match(page, /\.foldpill \.fp-car\{[^}]*border-left:/,
    'the caret must be drawn, not typed');
  for (const id of ['fold-traffic', 'fold-sat']) {
    const bad = [...pill(id)].filter(c => c.codePointAt(0) >= 0x1F000);
    assert.deepStrictEqual(bad, [], id + ' carries a glyph the panel cannot render');
  }
});
