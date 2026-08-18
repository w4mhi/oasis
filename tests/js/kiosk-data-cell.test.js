'use strict';
// The kiosk's data-freshness readout, which lives in the system bar as a
// DATA cell rather than as a pill in the units row.
//
// It moved because of what it IS: the units toggle and OPS are modes the
// operator sets, while freshness is a measurement of the station, like CPU and
// DISK either side of it. Two things have to stay true for the move to hold,
// and neither is visible from the JS alone:
//
//   1. the cell is built from the same .srow/.k/.v parts as its neighbours, so
//      it inherits their font, size and baseline instead of carrying its own;
//   2. it is still a CONTROL -- the pill it replaced ran a refresh pass on tap,
//      and a readout that quietly stopped doing that would look identical.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(
  path.join(__dirname, '..', '..', 'oasis-dashboard', 'dashboard.html'), 'utf8');

// The sysbar, markup only.
function sysbar() {
  const start = page.indexOf('<div class="sysbar">');
  assert.notStrictEqual(start, -1, 'no .sysbar in the kiosk');
  const end = page.indexOf('<!-- Weather Radio', start);
  assert.notStrictEqual(end, -1, 'could not find the end of the sysbar');
  return page.slice(start, end);
}

test('DATA is a cell in the system bar, to the right of IP', () => {
  const bar = sysbar();
  const ip = bar.indexOf('id="k-ip"');
  const data = bar.indexOf('id="k-data"');
  assert.notStrictEqual(data, -1, 'no DATA cell in the system bar');
  assert.ok(ip !== -1 && ip < data, 'DATA must sit to the RIGHT of IP');
  // Nothing after it: the separator rule is :not(:last-child), so a cell added
  // below DATA would silently take its "|" and leave the row ending in a pipe.
  assert.ok(bar.indexOf('class="srow', data) === -1,
    'DATA must be the last cell — the "|" separator hangs off :not(:last-child)');
});

test('the cell is built from the same parts as CPU/RAM/DISK', () => {
  const bar = sysbar();
  assert.match(bar, /<span class="k">DATA<\/span><span class="v" id="k-data">/,
    'DATA must use the shared .k / .v spans — that is what makes its font and ' +
    'size match the cells beside it');
  assert.ok(!/id="k-data"[^>]*style=/.test(bar),
    'no inline style on the value: the state colour comes from the fx-* classes');
});

test('the pill is gone, and nothing still paints it', () => {
  assert.ok(!page.includes('id="fxchip"'), 'the old DATA pill is still in the markup');
  assert.ok(!/\.upill\.fx-/.test(page), 'dead pill colour rules left behind');
});

test('tapping the cell still runs a refresh pass', () => {
  const bar = sysbar();
  assert.match(bar, /class="srow data"[\s\S]{0,300}onclick="fxKioskRefresh\(\)"/,
    'the cell no longer runs a refresh on tap');
  assert.match(bar, /class="srow data"[\s\S]{0,300}role="button"/);
  assert.match(bar, /class="srow data"[\s\S]{0,300}tabindex="0"/);
  assert.match(bar, /class="srow data"[\s\S]{0,400}onkeydown=/,
    'a role="button" that only answers clicks is a lie to a keyboard');
  assert.match(page, /\.sysbar \.srow\.data\{[^}]*cursor:pointer/,
    'a tappable cell that does not say so with the cursor');
});

test('the cell paints from the shared summary, short form', () => {
  // .short, not .label: "DATA · DATA OK" stutters. Both come out of the same
  // summarize() call, so the kiosk cannot disagree with the desktop about a state.
  assert.match(page, /cell\.textContent = s\.short \|\| s\.label \|\| '\\u2014';/,
    'the cell must print the short vocabulary, and must FALL BACK — textContent ' +
    'is nullable, so `= s.short` alone stores null for a stale freshness.js and ' +
    'erases the value instead of failing loudly');
  assert.match(page, /cell\.setAttribute\('class', 'v ' \+ s\.cls\)/,
    'the state colour must come from the shared cls, and must not drop the v class');
  assert.match(page, /cell\.setAttribute\('class', 'v fx-busy'\)/,
    'the tap-in-progress state must also keep the v class');
});

test('a stale freshness.js says so once, instead of just going quiet', () => {
  // The whole failure mode was silence: the value vanished, nothing threw, and
  // every other cell in the bar kept working. One warning is what turns that
  // into a diagnosis.
  assert.match(page, /if \(!s\.short && !_shortWarned\)/,
    'no staleness check on the freshness module');
  assert.match(page, /console\.warn\('\[oasis\] common\/js\/freshness\.js is older/,
    'the warning must name the file to copy');
  assert.match(page, /var _shortWarned = false;/,
    'once per page — this runs on a poll, and a warning per poll is noise');
});

test('every state the summary can return has a colour in this bar', () => {
  const FR = require('../../common/js/freshness.js');
  const states = ['fresh', 'stale', 'deferred', 'missing', 'unconfigured'];
  const classes = new Set(states.map(s =>
    FR.summarize([{ id: 'a', state: s, age_days: 9 }]).cls));
  classes.add('fx-off');    // unconfigured resolves to fresh in summarize()
  for (const cls of classes) {
    assert.match(page, new RegExp('\\.sysbar \\.v\\.' + cls + '\\{'),
      'no .sysbar colour rule for ' + cls + ' — that state would render in the ' +
      'default text colour and read as OK');
  }
});
