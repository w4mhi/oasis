'use strict';
// Unit tests for common/js/sat-detail.js — the satellite detail card shared by
// the Satellites page's popup and the kiosk's row sheet.
//
// The point of the module is that ONE bird cannot read differently on the wall
// and in your hand, so these tests care most about the claims the card makes:
// which mode an uplink belongs to, whether a pass is marked, and the difference
// between "no uplink exists" and "we were not told".
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

// The module reads these off the global at call time, exactly as the pages do
// with classic <script> tags in load order.
global.OasisSatUplink = require(path.join(__dirname, '..', '..', 'common', 'js', 'sat-uplink.js'));
global.OasisHorizon = require(path.join(__dirname, '..', '..', 'common', 'js', 'horizon.js'));
const D = require('../../common/js/sat-detail.js');

const HHMM = t => String(t).slice(11, 16);
const COMPASS = deg => ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][
  Math.round((((deg % 360) + 360) % 360) / 45) % 8];

const ISS = {
  norad: 25544, name: 'ISS (ZARYA)', labels: ['CREWED', 'VOICE'],
  downlinks: [
    { freq_mhz: 437.8, mode: 'FM', modes: ['FM', 'FSK', 'SSTV'], one_way: false,
      uplinks: [{ freq_mhz: 145.99, freq_high_mhz: null, invert: false,
                  ctcss_hz: 67.0, simplex: false, mode: 'FM' }] },
    { freq_mhz: 137.6257, mode: 'CW', modes: ['CW'], one_way: true, uplinks: [] },
    { freq_mhz: 145.825, mode: 'APRS', modes: ['APRS'], one_way: false,
      uplinks: [{ freq_mhz: 145.825, freq_high_mhz: null, invert: false,
                  ctcss_hz: null, simplex: true, mode: 'APRS' }] },
  ],
};

const OPTS = { passes: [], horizon: {}, minElev: 10, fmtTime: HHMM, compass: COMPASS };

test('rows are ordered by frequency, not by roster order', () => {
  const html = D.freqTableHTML(ISS);
  assert.ok(html.indexOf('137.6257') < html.indexOf('145.825'));
  assert.ok(html.indexOf('145.825') < html.indexOf('437.8'));
});

test('the unit lives in the header, not on every row', () => {
  const html = D.freqTableHTML(ISS);
  assert.ok(html.includes('FDN (MHz)') && html.includes('FUP (MHz)'));
  assert.strictEqual((html.match(/MHz/g) || []).length, 2);
});

test('a multi-mode entry names the mode its uplink belongs to', () => {
  // The row says FM/FSK/SSTV. Without naming FM, it would tell an operator they
  // can transmit to the SSTV service.
  assert.ok(D.freqTableHTML(ISS).includes('145.99 FM · CTCSS 67'));
});

test('one_way renders n/a and an unknown uplink renders unknown', () => {
  const html = D.freqTableHTML(ISS);
  assert.ok(html.includes('n/a'), 'the CW beacon is one_way');
  assert.ok(!html.includes('unknown'), 'nothing here claims an unrecorded uplink');

  const mystery = { downlinks: [{ freq_mhz: 145.9, mode: 'FM', modes: ['FM'],
                                  one_way: false, uplinks: [] }] };
  const h2 = D.freqTableHTML(mystery);
  assert.ok(h2.includes('unknown'));
  assert.ok(!h2.includes('n/a'));
});

test('a bird with no downlinks says so rather than rendering an empty table', () => {
  const html = D.freqTableHTML({ downlinks: [] });
  assert.ok(html.includes('No downlinks'));
  assert.ok(!html.includes('<table'));
});

test('a pass peaking below the skyline is MARKED, never dropped', () => {
  const pass = { rise: '2026-08-11T16:04:00', set: '2026-08-11T16:16:00',
                 max_el: 17, peak_az: 0, rise_az: 42 };
  const html = D.passesHTML(ISS, Object.assign({}, OPTS,
    { passes: [pass], horizon: { N: 30 } }));
  assert.ok(html.includes('peak below skyline'));
  assert.ok(html.includes('16:04'), 'the pass is still listed');
  assert.ok(html.includes('blocked'));
});

test('the same pass under a clear horizon carries no mark', () => {
  const pass = { rise: '2026-08-11T16:04:00', set: '2026-08-11T16:16:00',
                 max_el: 17, peak_az: 180, rise_az: 42 };
  const html = D.passesHTML(ISS, Object.assign({}, OPTS,
    { passes: [pass], horizon: { N: 30 } }));
  assert.ok(!html.includes('below skyline'));
  assert.ok(!html.includes('blocked'));
});

test('a pass cached before peak_az existed is never marked', () => {
  // peak_az is absent on old cached passes; guessing would mark real passes.
  const pass = { rise: '2026-08-11T16:04:00', set: '2026-08-11T16:16:00',
                 max_el: 5, rise_az: 42 };
  const html = D.passesHTML(ISS, Object.assign({}, OPTS,
    { passes: [pass], horizon: { N: 30 } }));
  assert.ok(!html.includes('below skyline'));
});

test('no onPassClick means no onclick attribute at all', () => {
  // The kiosk has nothing to plot a pass ONTO, so its rows must be read-only
  // rather than tappable and inert.
  const pass = { rise: '2026-08-11T16:04:00', set: '2026-08-11T16:16:00',
                 max_el: 17, peak_az: 180, rise_az: 42 };
  const kiosk = D.passesHTML(ISS, Object.assign({}, OPTS, { passes: [pass] }));
  assert.ok(!kiosk.includes('onclick'));
  assert.ok(!kiosk.includes('tappable'));

  const page = D.passesHTML(ISS, Object.assign({}, OPTS,
    { passes: [pass], onPassClick: 'pickPass' }));
  assert.ok(page.includes('onclick="pickPass(25544,'));
  assert.ok(page.includes('tappable'));
});

test('no passes says so rather than rendering nothing', () => {
  assert.ok(D.passesHTML(ISS, OPTS).includes('No passes in 24 h'));
});

test('the pass list is capped', () => {
  const p = i => ({ rise: `2026-08-11T1${i}:00:00`, set: `2026-08-11T1${i}:10:00`,
                    max_el: 20, peak_az: 180, rise_az: 10 });
  const html = D.passesHTML(ISS, Object.assign({}, OPTS,
    { passes: [p(0), p(1), p(2), p(3), p(4), p(5)] }));
  assert.strictEqual((html.match(/pop-pass/g) || []).length, 4);
});

test('the plot is a caller-supplied fragment, and absent when none is given', () => {
  // The polar projection is page-local, so the module never builds one.
  assert.ok(!D.satDetailHTML(ISS, OPTS).includes('sp-plot'));
  const withPlot = D.satDetailHTML(ISS, Object.assign({}, OPTS,
    { plotHTML: '<svg id="x"></svg>' }));
  assert.ok(withPlot.includes('sp-plot'));
  assert.ok(withPlot.includes('<svg id="x">'));
});

test('names and labels are escaped', () => {
  // Names come from CelesTrak and SatNOGS. None of it is ours.
  const html = D.satDetailHTML(
    { norad: 1, name: 'EVIL <script>x</script>', labels: ['A&B'], downlinks: [] }, OPTS);
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('A&amp;B'));
});

test('the card renders with no horizon at all, which is the common case', () => {
  const pass = { rise: '2026-08-11T16:04:00', set: '2026-08-11T16:16:00',
                 max_el: 17, peak_az: 0, rise_az: 42 };
  const html = D.satDetailHTML(ISS, Object.assign({}, OPTS, { passes: [pass] }));
  assert.ok(html.includes('16:04'));
  assert.ok(!html.includes('below skyline'));
  assert.ok(!html.includes('workable'));
});

test('no codepoint above U+FFFF anywhere in the card', () => {
  // The Pi ships no emoji font and renders tofu.
  const html = D.satDetailHTML(ISS, Object.assign({}, OPTS,
    { passes: [{ rise: '2026-08-11T16:04:00', set: '2026-08-11T16:16:00',
                 max_el: 17, peak_az: 300, rise_az: 42 }] }));
  for (const ch of html) assert.ok(ch.codePointAt(0) <= 0xFFFF, ch);
});
