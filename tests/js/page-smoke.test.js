'use strict';
// Does the page actually COME UP? Headless render, against a running server.
//
// WHY THIS EXISTS. Twice now a delete-and-rewire left a live reference behind
// and every existing gate passed it:
//
//   * withinHour  — deleted, still called from passesFilters. Mon-1h filtered
//                   nothing, because ROSTER.filter threw and renderRoster never
//                   finished.
//   * _denoise    — deleted, still read by boot(). ReferenceError inside boot,
//                   so the roster placeholder never got replaced.
//
// Neither is a syntax error, so `node --check` and the front-end gate are blind
// to them, and the unit tests never execute boot(). The only thing that catches
// this class is loading the page and looking at what came out.
//
// THE ASSERTION THAT MATTERS IS "DID ANYTHING RENDER AT ALL". While chasing the
// second bug I ran three headless checks against `chromium-browser`, which does
// not exist on that machine, greped empty output, and nearly read "0 occurrences
// of 'Loading roster'" as proof the page worked. So the first assertion here is
// that the DOM is big enough to be a real page — a check whose failure mode is
// silence is not a check.
//
// Skipped unless a browser AND a server are both present, so CI and a laptop
// stay green. Point it elsewhere with OASIS_SMOKE_URL.
const test = require('node:test');
const assert = require('node:assert');
const { execFileSync, spawnSync } = require('node:child_process');

const BASE = process.env.OASIS_SMOKE_URL || 'http://localhost:8083';
// Overridable so the guard can be pointed at a deliberately broken copy and
// watched to FAIL. A check that has only ever been seen passing is not evidence
// it can detect anything — that is the mistake this whole file exists to stop.
const PAGE = process.env.OASIS_SMOKE_PATH || '/server/satellites/';

// The binary is named differently on Debian, on a Pi, and on a desktop. Assuming
// one name is exactly how the earlier checks silently did nothing.
function findBrowser() {
  if (process.env.OASIS_SMOKE_BROWSER) return process.env.OASIS_SMOKE_BROWSER;
  for (const b of ['chromium', 'chromium-browser', 'google-chrome', 'chrome']) {
    const r = spawnSync('command', ['-v', b], { shell: true, encoding: 'utf8' });
    if (r.status === 0 && r.stdout.trim()) return r.stdout.trim().split('\n')[0];
  }
  // macOS keeps it in a bundle, off PATH entirely. The Pi has `chromium`; the
  // Pi has no node, so in practice this test runs HERE against a Pi over the
  // network (OASIS_SMOKE_URL) rather than on the box being tested.
  for (const p of ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                   '/Applications/Chromium.app/Contents/MacOS/Chromium']) {
    if (require('node:fs').existsSync(p)) return p;
  }
  return null;
}

function serverUp() {
  const r = spawnSync('curl', ['-s', '-o', '/dev/null', '-w', '%{http_code}',
                               '--max-time', '4', BASE + PAGE],
                      { encoding: 'utf8' });
  return r.status === 0 && r.stdout.trim() === '200';
}

const BROWSER = findBrowser();
const UP = BROWSER ? serverUp() : false;
const skip = !BROWSER ? 'no chromium/chrome on this box'
           : !UP ? `no OASIS server at ${BASE}`
           : false;

function render(path) {
  const out = execFileSync(BROWSER, [
    '--headless', '--disable-gpu', '--no-sandbox',
    '--virtual-time-budget=9000', '--dump-dom', BASE + path,
  ], { encoding: 'utf8', timeout: 60000, maxBuffer: 64 * 1024 * 1024,
       stdio: ['ignore', 'pipe', 'pipe'] });
  return out;
}

test('the satellites page renders its roster', { skip }, () => {
  const dom = render(PAGE);

  // FIRST, and deliberately first: did we get a page at all? Every assertion
  // below is meaningless if this one is not true, and its failure mode when
  // unchecked is a passing test.
  assert.ok(dom.length > 50000,
            `DOM was ${dom.length} bytes — the render did not happen, so nothing ` +
            `below this line means anything`);

  // The placeholder the page ships with. Still present = boot() never finished,
  // which is precisely what the _denoise ReferenceError did.
  assert.ok(!dom.includes('Loading roster'),
            'roster placeholder still present — boot() did not complete');

  // Positive evidence, not just absence of the placeholder. It must match the
  // ATTRIBUTE, not the bare string: `sat-row` also appears as a CSS rule name,
  // and a deliberately broken build still contained it 23 times while rendering
  // no rows at all. Measured against that broken build: 102 here, 1 there.
  const rows = (dom.match(/class="sat-row/g) || []).length;
  assert.ok(rows >= 5, `only ${rows} roster rows rendered`);

  // BELOW THIS LINE the checks are markup presence, not render success — they
  // are in the served HTML whether boot() finished or not, and the broken build
  // passed both. They catch a bad delete in the markup; they do NOT catch a
  // boot failure, and must never be mistaken for the checks above.
  for (const id of ['t-rec', 't-play', 't-stop']) {
    assert.ok(dom.includes(`id="${id}"`), `transport button ${id} is missing`);
  }
  const chips = (dom.match(/data-view=/g) || []).length;
  assert.ok(chips >= 4, `expected the view chips, found ${chips}`);
});

test('the roster tally reports real numbers', { skip }, () => {
  const dom = render(PAGE);
  assert.ok(dom.length > 50000, `DOM was ${dom.length} bytes — render did not happen`);
  const m = dom.match(/id="roster-count"[^>]*>([^<]*)</);
  assert.ok(m, 'roster-count element not found');
  // "N monitored - M sats". A page that threw mid-render leaves this at its
  // initial em-dash, so the SHAPE of the text is the evidence. Verified against
  // a deliberately broken build, which reads exactly "—" here.
  assert.match(m[1], /\d+\s+monitored\s+-\s+\d+/,
               `roster tally reads ${JSON.stringify(m[1])}`);
});
