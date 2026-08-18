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

// The stylesheet, so an @media mentioned in a JS comment cannot be mistaken for
// a rule.
function stylesheet() {
  const a = page.indexOf('<style>');
  const b = page.indexOf('</style>', a);
  assert.ok(a !== -1 && b > a, 'no stylesheet');
  return page.slice(a, b);
}

test('the small-panel block is the last thing in the stylesheet', () => {
  // This is not tidiness. Nearly every rule in that block is a BARE-CLASS
  // override of something defined above it, so it wins on source order alone.
  // It shipped ~250 lines above `.sats{ width:calc(28rem + 100px) }` and lost:
  // the open sat card kept its 1920 width, 464px of a 769px row — 60% — with
  // every other part of the fold behaving exactly as designed.
  const css = stylesheet();
  const query = css.indexOf('@media (max-width:1100px){');
  assert.notStrictEqual(query, -1, 'the small-panel query is not in the stylesheet');
  for (const base of ['.sats{ flex:none;', '.row.bot{ flex:1 1 auto;', '.foldpill{ display:none;']) {
    const at = css.indexOf(base);
    assert.notStrictEqual(at, -1, 'base rule vanished: ' + base);
    assert.ok(at < query,
      base + ' is declared AFTER the small-panel query — at equal specificity it ' +
      'wins, and the small panel silently keeps the desktop value');
  }
  // Nothing but whitespace may follow it, or the next rule added has the same
  // problem in reverse.
  const after = css.slice(css.indexOf('}', css.lastIndexOf('}')) + 1);
  assert.strictEqual(after.trim(), '',
    'a rule was added after the small-panel block — move it inside, or above');
});

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

test('the refresh countdown is the last thing in BOTH headers', () => {
  // Same question on both cards ("is this list still alive?"), so the same place
  // on both — hard right, side by side on a desktop panel or stacked on a 7-inch
  // one. The pill sits immediately to its left.
  for (const [card, cd] of [['traffic', 'k-traffic-cd'], ['sats', 'k-sat-cd']]) {
    const hd = header(card);
    const group = hd.slice(hd.indexOf('<span class="hd-right"'));
    assert.ok(group, card + ' header has no right-hand group');
    const pill = group.indexOf('class="foldpill"');
    const clock = group.indexOf('id="' + cd + '"');
    assert.ok(pill !== -1 && clock !== -1, card + ' header lost the pill or the countdown');
    assert.ok(pill < clock, card + ': the countdown must come AFTER the pill');
    // Nothing may follow the countdown inside the group.
    assert.match(group.slice(clock), /^[^<]*><\/span><\/span>\s*$/,
      card + ': something was added after the countdown — it is no longer hard right');
  }
});

test('one anchor pushes the right-hand group, not a chain of them', () => {
  // The old shape was margin-left:auto on .meta AND on .satcd, plus an #id
  // override to stop the two splitting the free space. That holds for exactly
  // two items; the pill was a third. One auto on the container instead.
  assert.match(page, /\.hd-right\{[^}]*margin-left:auto/,
    'the group must carry the push');
  assert.ok(!/\.hd \.satcd\{ margin-left:auto/.test(page),
    'the countdown still carries its own auto margin — with the group that ' +
    'splits the free space and the pill drifts away from it');
  assert.ok(!/#k-sat-meta\{/.test(page),
    'the #id override existed only to referee the auto-margin chain');
  assert.match(page, /\.hd-right > \*\{ margin-left:0; \}/,
    'a member with its own auto margin would re-open the same bug inside the group');
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

test('a pass record puts its countdown where the in-pass row puts LOS', () => {
  // Both answer "how long have I got", so both end the detail line — which the
  // flattened layout pushes hard right. The countdown used to sit up in l1 with
  // margin-left:auto, i.e. at the TOP-right of a card whose BOTTOM line carried
  // the other time.
  assert.match(page, /const l2 = `rise \$\{esc\(dir\)\} · \$\{el\}° max\$\{ready\} · <span class="cd">↑ \$\{it\.mins\}m<\/span>`/,
    'the countdown must end the detail line, after any "get ready"');
  assert.ok(!/<div class="l1">\$\{nm\}<span class="aos">\$\{hm\(it\.p\._r\)\}<\/span><span class="cd">/.test(page),
    'the countdown is back in l1 — that is the top-right of the card again');
  assert.match(page, /· LOS \$\{hm\(it\.p\._s\)\}<\/div>/,
    'the in-pass row must still end on LOS, or the two rows disagree about ' +
    'where the time lives');
});

test('an in-pass row says "up", not "overhead"', () => {
  // Short enough to sit in the .aos slot a rise TIME occupies on every other
  // row, which is what keeps the flattened one-line record from reflowing when
  // a bird comes over the horizon.
  assert.match(page, /<span class="aos">up<\/span>/, 'the in-pass row lost its "up"');
  assert.ok(!/<span class="aos">overhead<\/span>/.test(page), '"overhead" is back');
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

test('the traffic pill carries the per-path breakdown the chips would have shown', () => {
  // The RF / IS / ADS-B chips fold away with the list, and the total alone
  // cannot say whether 384 stations is a busy band or a busy internet feed.
  const count = page.indexOf("getElementById('k-aprs-count')");
  const block = page.slice(count, page.indexOf('renderAprsRows(list.slice(', count));
  assert.match(block, /_SRC_KEYS\.forEach\(k => bits\.push\(_SRC_LABEL\[k\] \+ ' ' \+ \(srcCounts\[k\] \|\| 0\)\)\)/,
    'the pill must report each path, using the chips\' own labels and counts');
  // Order is load-bearing: the ellipsis eats the TAIL, so the total and the
  // active filter have to be built before the breakdown.
  const total = block.indexOf("total + ' heard'");
  const narrow = block.indexOf('srcSelected.length');
  const breakdown = block.indexOf('_SRC_KEYS.forEach');
  assert.ok(total < narrow && narrow < breakdown,
    'the breakdown must be last — it is what a narrow pill can afford to lose');
});

test('nothing caps the pill\'s width but the row it sits in', () => {
  // A 15rem cap truncated headlines the satellites header had room for. Let the
  // flex box decide: shrink only when the row actually runs out.
  assert.ok(!/\.foldpill \.fs-head\{[^}]*max-width/.test(page),
    'the headline is capped again — the row is what should decide its width');
  assert.match(page, /\.foldpill\{[^}]*min-width:0;/,
    'without min-width:0 the pill cannot shrink, and a long headline shoves the ' +
    'countdown off the right edge instead of ellipsising');
});

test('an overhead bird still colours the pill', () => {
  assert.match(page, /it0\.inPass \? 'now' : \(it0\.mins <= 10 \? 'soon' : ''\)/,
    'folding the sat card must not hide that a pass is happening');
  // The resting state is GREEN — a folded list that is alive and has rows in it
  // is good news, and dim beside a header full of live colour reads as stale.
  // So `now` cannot separate itself by hue; it does it by weight.
  assert.match(page, /\.foldpill \.fs-head\{[^}]*color:var\(--accent\); \}/,
    'the resting headline must be green, not dim');
  assert.match(page, /\.foldpill \.fs-head\.soon\{ color:var\(--warn\); \}/,
    'a pass inside ten minutes is the one that wants amber');
  assert.match(page, /\.foldpill \.fs-head\.now\{ color:var\(--accent\); font-weight:700; \}/,
    'overhead is good news too — it separates by weight, not by hue');
});

test('every pass record spans the pane — one column, never two', () => {
  const small = decomment(smallScreenBlock());
  // Two columns fits one more bird and makes every record half as wide as the
  // pane. A pass record is horizontal — name, time, countdown, capability, rise
  // — and at 370px it stacks back into three lines, which is the shape the fold
  // exists to escape.
  assert.match(small, /\.row\.bot\[data-open="sat"\] \.satscroll\{ display:block; \}/,
    'the sat list must be a single full-width column');
  assert.ok(!/grid-template-columns/.test(small),
    'no multi-column grid for the pass list on a small panel');
});

test('a pass record folds onto one line instead of stacking three', () => {
  const small = decomment(smallScreenBlock());
  // The width goes into flattening the record, which is what pays for dropping
  // the second column: ~46px per record instead of ~77px.
  assert.match(small, /\.row\.bot\[data-open="sat"\] \.sat\{ display:flex; flex-wrap:wrap;/,
    'the record must lay its three lines out in a row');
  assert.match(small, /\.sat \.l1\{ flex:0 0 auto;/,
    'l1 must stay a tight group, or .cd\'s own auto margin flings the countdown ' +
    'to the far end of the row, away from the AOS time it qualifies');
  assert.match(small, /\.sat \.l2\{ flex:0 1 auto; margin-top:0; margin-left:auto; \}/,
    'the rise detail takes the free space and sits hard right');
  assert.ok(/flex-wrap:wrap/.test(small),
    'an in-pass record carries elevation, range and LOS — it must wrap, not clip');
  // The markup is untouched, so the desktop panel keeps its three-line cards.
  assert.ok(!/\.sat\{ display:flex/.test(page.slice(0, page.indexOf('@media (max-width:1100px){'))),
    'the flattening must not escape the small-panel block');
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
