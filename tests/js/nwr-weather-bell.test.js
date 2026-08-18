'use strict';
// nwr-weather-bell.test.js — the two controls services/nwr/static/weather.html
// grew last: the spoken-alert bell and the county watch list.
//
// WHY THIS FILE EXISTS. Both behaviours shipped with a back end, tests and
// documentation and NO user interface at all. `bell` defaults to false, so the
// station never spoke and there was no way to turn it on; `watch_fips` decides
// which alerts count as the operator's own and there was no way to say which
// counties those were. The handbook promised both. These are the controls, and
// what is pinned here is the part of them that can be got wrong silently:
//
//   * the quiet window is READ from the shared definition, never written here
//     (a second copy of "22 to 07" is how one half of a station goes silent at
//     06:00 while the other is chiming)
//   * an override the server REFUSES -- settings.py 400s a value that would
//     outlive the next quiet-hours boundary rather than clamping it -- reaches
//     the operator's eyes in the server's own words
//   * a watch list is stored as codes and read back as names, and a code with no
//     county entry is still watchable
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', '..');
const src = fs.readFileSync(
  path.join(root, 'services', 'nwr', 'static', 'weather.html'), 'utf8');

// The REAL modules, not stand-ins. A stub quiet-hours would let the page and the
// daemon drift about when night is, which is the single thing quiet-hours.js
// exists to prevent; a stub bell glyph would let this station draw two different
// bells.
const OasisQuietHours = require(path.join(root, 'common', 'js', 'quiet-hours.js'));
const OasisBells = require(path.join(root, 'common', 'js', 'sat-bells.js'));
const SHARED_WINDOW = JSON.parse(fs.readFileSync(
  path.join(root, 'common', 'quiet-hours.json'), 'utf8'));
OasisQuietHours.load(SHARED_WINDOW);

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

// ── Pulling the page's own functions out of the page ─────────────────────────

function braceBody(start) {
  const braceStart = src.indexOf('{', start);
  let depth = 0, end = braceStart;
  for (; end < src.length; end++) {
    if (src[end] === '{') depth++;
    else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
  }
  return src.slice(start, end);
}

function extractAt(marker) {
  const start = src.indexOf(marker);
  assert.ok(start !== -1, marker + ' not found in weather.html');
  return braceBody(start);
}

const extractPlain = (name) => extractAt('function ' + name + '(');
const extractAsync = (name) => extractAt('async function ' + name + '(');

function extractHandlerBody(anchor) {
  const start = src.indexOf(anchor);
  assert.ok(start !== -1, anchor + ' not found in weather.html');
  const braceStart = start + anchor.length - 1;
  let depth = 0, end = braceStart;
  for (; end < src.length; end++) {
    if (src[end] === '{') depth++;
    else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
  }
  return src.slice(braceStart + 1, end - 1);
}

const SYNC = ['hhmm', 'quietWindowText', 'bellView', 'overridePatch', 'configMsg',
              'setBellMsg', 'renderBell', 'countyLabel', 'unknownLabel',
              'pickerRows', 'watchPatch', 'setCtyMsg', 'renderWatch',
              'renderResults'];
const ASYNC = ['syncWatch', 'runCountyQuery', 'setWatch'];

// ── A DOM stub with only what these functions touch ──────────────────────────

function mk(id) {
  return {
    id: id || '', tagName: '', disabled: false, textContent: '', className: '',
    innerHTML: '', title: '', value: '', style: {}, children: [], handlers: {},
    appendChild(e) { this.children.push(e); },
    setAttribute(n, v) { this[n] = String(v); },
    addEventListener(ev, fn) { this.handlers[ev] = fn; },
    // The text a person would actually see, chips and buttons included.
    text() {
      return this.children.length
        ? this.children.map((c) => c.text()).join('|')
        : this.textContent;
    },
  };
}

function fakeDoc() {
  const els = {};
  ['bell', 'bell-state', 'ovr', 'bellmsg',
   'cty-q', 'cty-results', 'cty-list', 'ctymsg'].forEach((id) => { els[id] = mk(id); });
  return {
    els,
    getElementById: (id) => els[id] || null,
    createElement: (tag) => { const e = mk(); e.tagName = tag; return e; },
  };
}

function buildPage(opts) {
  const o = opts || {};
  const doc = fakeDoc();
  const preamble =
    'const $ = function (id) { return document.getElementById(id); };\n' +
    'let CFG = {};\n' +
    'let _watchSig = null, _ctySeq = 0, _ctyTimer = 0;\n';
  const body = SYNC.map(extractPlain).concat(ASYNC.map(extractAsync)).join('\n');
  const tail = '\nreturn { ' + SYNC.concat(ASYNC).join(', ') +
    ', setCfg: function (c) { CFG = c; }, getCfg: function () { return CFG; } };';
  const page = new AsyncFunction(
    'document', 'OasisQuietHours', 'OasisBells', 'api', 'post', 'poll',
    preamble + body + tail);
  // AsyncFunction returns a promise; every function inside is already defined by
  // the time it resolves, and nothing in the preamble awaits.
  return page(doc, OasisQuietHours, OasisBells,
              o.api || (async () => ({ ok: false, status: 0, data: null })),
              o.post || (async () => ({ ok: true, status: 200, data: { ok: true, config: {} } })),
              o.poll || (() => {})).then((api) => ({ page: api, doc: doc }));
}

// A Date at a given local hour, so a test can stand at 23:30 without the host's
// timezone deciding what that means.
function at(hour, minute) {
  const d = new Date(2026, 7, 17, hour, minute || 0, 0, 0);
  return d;
}

const QUIET_TO = SHARED_WINDOW.to;
const QUIET_FROM = SHARED_WINDOW.from;
const NIGHT = at((QUIET_FROM + 1) % 24, 30);        // inside the window
const DAY = at((QUIET_TO + 3) % 24, 15);            // outside it

// ── The bell's three states ──────────────────────────────────────────────────

test('the bell off is a crossed bell and says nothing is spoken', async () => {
  const { page } = await buildPage();
  const v = page.bellView({ bell: false }, DAY.getTime(), DAY.getHours());
  assert.strictEqual(v.on, false);
  assert.strictEqual(v.silent, true);
  assert.strictEqual(v.glyph, OasisBells.BELL_OFF);
  assert.match(v.text, /Off/);
  assert.strictEqual(v.ovrShown, false,
    'there is nothing to override while the bell is off');
});

test('the bell on in daylight rings, and still names the quiet window', async () => {
  const { page } = await buildPage();
  const v = page.bellView({ bell: true }, DAY.getTime(), DAY.getHours());
  assert.strictEqual(v.silent, false);
  assert.strictEqual(v.glyph, OasisBells.BELL_ON);
  assert.match(v.text, /On -/);
  // B1: the operator must be able to SEE that the bell goes quiet at night,
  // from the page, without having to be up at 22:00 to discover it.
  assert.ok(v.text.includes(page.quietWindowText()),
    'the bell never says when it will fall silent: ' + v.text);
});

test('on but inside quiet hours reads as on AND silent, not as off', async () => {
  const { page } = await buildPage();
  const v = page.bellView({ bell: true }, NIGHT.getTime(), NIGHT.getHours());
  assert.strictEqual(v.on, true);
  assert.strictEqual(v.quiet, true);
  assert.strictEqual(v.silent, true);
  assert.strictEqual(v.glyph, OasisBells.BELL_OFF, 'nothing will sound, so say so');
  assert.match(v.text, /^On, but/,
    'a bell held silent by the clock must not read as a bell the operator turned off');
  assert.strictEqual(v.ovrShown, true);
});

test('an active override speaks through the night and offers to stop', async () => {
  const { page } = await buildPage();
  const until = Math.floor(OasisQuietHours.overrideUntil(NIGHT) / 1000);
  const v = page.bellView({ bell: true, bell_override_until: until },
                          NIGHT.getTime(), NIGHT.getHours());
  assert.strictEqual(v.overridden, true);
  assert.strictEqual(v.silent, false);
  assert.strictEqual(v.glyph, OasisBells.BELL_ON);
  assert.match(v.ovrLabel, /quiet hours/);
});

test('an override that has expired is simply gone', async () => {
  const { page } = await buildPage();
  // Yesterday's override, still on disk: it must not keep the bell awake.
  const v = page.bellView({ bell: true, bell_override_until: 1 },
                          NIGHT.getTime(), NIGHT.getHours());
  assert.strictEqual(v.overridden, false);
  assert.strictEqual(v.silent, true);
});

test('the quiet window comes from the shared file, never from a literal', async () => {
  const { page } = await buildPage();
  assert.strictEqual(page.quietWindowText(),
    page.hhmm(QUIET_FROM) + '-' + page.hhmm(QUIET_TO));
  // Move the shared definition and the page must move with it. A hardcoded
  // "22:00-07:00" anywhere in this path would survive this and then teach the
  // operator the wrong hours for the rest of the install's life.
  OasisQuietHours.load({ from: 21, to: 5 });
  try {
    assert.strictEqual(page.quietWindowText(), '21:00-05:00');
    const v = page.bellView({ bell: true }, at(20, 0).getTime(), 20);
    assert.strictEqual(v.quiet, false, '20:00 is not quiet under a 21-05 window');
    assert.ok(v.text.includes('21:00-05:00'));
  } finally {
    OasisQuietHours.load(SHARED_WINDOW);
  }
});

test('weather.html writes no quiet-hours numbers of its own', () => {
  // The bell card's script region only. The page legitimately contains other
  // numbers; what must not appear is an hour of the quiet window spelled out.
  assert.doesNotMatch(src, /\b(22|07):00\s*(-|to|&ndash;)\s*(07|22):00/,
    'weather.html spells out the quiet window instead of reading it');
});

// ── The override, and the refusal ────────────────────────────────────────────

test('the override patch is the shared boundary, in SECONDS', async () => {
  const { page } = await buildPage();
  const patch = page.overridePatch(NIGHT, false);
  assert.strictEqual(patch.bell_override_until,
    Math.floor(OasisQuietHours.overrideUntil(NIGHT) / 1000),
    'the page must ask for the same boundary bell.override_until() computes');
  // settings.py stores an epoch in seconds; quiet-hours.js works in ms. A page
  // that posted milliseconds would be refused by the boundary check forever.
  assert.ok(patch.bell_override_until < 1e11, 'that is milliseconds, not seconds');
});

test('cancelling an override posts zero, which is always valid', async () => {
  const { page } = await buildPage();
  assert.deepStrictEqual(page.overridePatch(NIGHT, true), { bell_override_until: 0 });
});

test('a refused config write is shown in the server words, not ours', async () => {
  const { page } = await buildPage();
  const refusal = 'bell_override_until cannot outlive the next quiet-hours boundary';
  assert.strictEqual(
    page.configMsg({ ok: false, status: 400, data: { ok: false, error: refusal } },
                   'could not change the override'),
    refusal);
  // A write that succeeded says nothing at all.
  assert.strictEqual(page.configMsg({ ok: true, data: { ok: true } }, 'x'), '');
  // Flask itself unreachable: no body to quote, so our own words stand in.
  assert.strictEqual(page.configMsg({ ok: false, status: 0, data: null }, 'fallback'),
    'fallback');
});

test('the override button surfaces the 400 on the page', async () => {
  // The whole point of settings.py refusing rather than clamping: the operator
  // has to be able to SEE that the station did not do what they asked.
  const refusal = 'bell_override_until cannot outlive the next quiet-hours boundary';
  const posts = [];
  const { page, doc } = await buildPage();
  // The handler itself, run with the page's own helpers wired in behind it.
  const body = extractHandlerBody(
    "$('ovr').addEventListener('click', async function () {");
  await new AsyncFunction('$', 'post', 'configMsg', 'setBellMsg', 'poll',
                          'overridePatch', 'OasisQuietHours', 'CFG', body)(
    (id) => doc.getElementById(id),
    async (url, patch) => {
      posts.push([url, patch]);
      return { ok: false, status: 400,
               data: { ok: false, error: refusal, code: 'NWR_BAD_CONFIG' } };
    },
    page.configMsg, page.setBellMsg, () => {}, page.overridePatch,
    OasisQuietHours, { bell: true, bell_override_until: 0 });
  assert.strictEqual(posts.length, 1);
  assert.strictEqual(doc.els.bellmsg.textContent, refusal,
    'the page swallowed a refused override');
  assert.strictEqual(doc.els.bellmsg.className, 'warn');
  assert.strictEqual(doc.els.ovr.disabled, false,
    'a refused write must not leave the button dead');
});

test('renderBell paints the three states onto the card', async () => {
  const { page, doc } = await buildPage();
  page.renderBell({ bell: false }, DAY);
  assert.strictEqual(doc.els.bell.className, 'bellbtn off');
  assert.strictEqual(doc.els.bell['aria-pressed'], 'false');
  assert.strictEqual(doc.els.ovr.style.display, 'none');

  page.renderBell({ bell: true }, DAY);
  assert.strictEqual(doc.els.bell.className, 'bellbtn');
  assert.strictEqual(doc.els.bell['aria-pressed'], 'true');
  assert.strictEqual(doc.els.ovr.style.display, '');

  page.renderBell({ bell: true }, NIGHT);
  assert.strictEqual(doc.els.bell.className, 'bellbtn quiet');
  assert.strictEqual(doc.els.bell['aria-pressed'], 'true',
    'quiet hours must not misreport the switch as off');
  assert.ok(doc.els.bell.innerHTML.indexOf('<svg') === 0,
    'the glyph must be the shared inline SVG -- the bell emoji is blank on Pi Chromium');
});

// ── The county watch list ────────────────────────────────────────────────────

test('a picked county becomes a sorted, de-duplicated patch', async () => {
  const { page } = await buildPage();
  assert.deepStrictEqual(page.watchPatch(['53053'], '53033', true),
                         { watch_fips: ['53033', '53053'] });
  assert.deepStrictEqual(page.watchPatch(['53033'], '53033', true),
                         { watch_fips: ['53033'] }, 'a double click must not duplicate');
  assert.deepStrictEqual(page.watchPatch(['53033', '53053'], '53033', false),
                         { watch_fips: ['53053'] });
  assert.deepStrictEqual(page.watchPatch([], '53033', false), { watch_fips: [] });
});

test('a bare SAME code with no county entry is still offerable', async () => {
  const { page } = await buildPage();
  // 51560 is one of the four codes this Gazetteer vintage lost; marine zones
  // were never in it. An alert still matters when the map cannot draw it.
  const rows = page.pickerRows('51560', { counties: [] });
  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].fips5, '51560');
  assert.strictEqual(rows[0].name, null);
  // SAME sends PSSCCC; the leading subdivision digit is not stored.
  assert.strictEqual(page.pickerRows('051560', { counties: [] })[0].fips5, '51560');
  // A code the table DOES carry is offered once, from the table, with its name.
  const known = page.pickerRows('53033', {
    counties: [{ fips5: '53033', name: 'King', state: 'WA' }] });
  assert.strictEqual(known.length, 1);
  assert.strictEqual(known[0].name, 'King');
  // A word is not a code.
  assert.deepStrictEqual(page.pickerRows('king', { counties: [] }), []);
});

test('the chips name what is watched, and say when it cannot be plotted', async () => {
  const { page, doc } = await buildPage();
  page.renderWatch([{ fips5: '53033', name: 'King', state: 'WA' }], ['51560']);
  const chips = doc.els['cty-list'].children;
  assert.strictEqual(chips.length, 2);
  assert.strictEqual(chips[0].className, 'chip');
  assert.match(chips[0].text(), /King, WA \(53033\)/);
  assert.strictEqual(chips[1].className, 'chip unplottable');
  assert.match(chips[1].text(), /51560/);
  assert.match(chips[1].text(), /cannot be named or plotted/);
});

test('an empty watch list says what an empty watch list MEANS', async () => {
  const { page, doc } = await buildPage();
  page.renderWatch([], []);
  // "An empty list matches everything" is the single most misread fact about
  // this feature -- a blank box that said nothing would read as "nothing armed".
  assert.match(doc.els['cty-list'].text(), /every alert/i);
});

test('the watch list is resolved to names in ONE request', async () => {
  const calls = [];
  const { page, doc } = await buildPage({
    api: async (url) => {
      calls.push(url);
      return { ok: true, status: 200, data: { ok: true, limit: 200, total: 2,
        truncated: false, unknown: ['51560'],
        counties: [{ fips5: '53033', name: 'King', state: 'WA' }] } };
    },
  });
  await page.syncWatch({ watch_fips: ['53033', '51560'] });
  assert.strictEqual(calls.length, 1, 'one request for the whole list, not one each');
  assert.match(calls[0], /\/api\/nwr\/counties\?limit=200&fips=53033%2C51560/);
  assert.strictEqual(doc.els['cty-list'].children.length, 2);

  // A second poll with the same list must not rebuild the DOM or ask again --
  // this runs every 5 s for as long as the page is open.
  await page.syncWatch({ watch_fips: ['53033', '51560'] });
  assert.strictEqual(calls.length, 1);
});

test('a failed resolve still shows the codes, and tries again', async () => {
  let fail = true;
  const calls = [];
  const { page, doc } = await buildPage({
    api: async (url) => {
      calls.push(url);
      if (fail) { return { ok: false, status: 500, data: null }; }
      return { ok: true, status: 200, data: { ok: true, unknown: [],
        counties: [{ fips5: '53033', name: 'King', state: 'WA' }] } };
    },
  });
  await page.syncWatch({ watch_fips: ['53033'] });
  assert.match(doc.els['cty-list'].text(), /53033/,
    'the watch list is real whether or not we could name it');
  fail = false;
  await page.syncWatch({ watch_fips: ['53033'] });
  assert.strictEqual(calls.length, 2, 'a failed resolve must not latch');
  assert.match(doc.els['cty-list'].text(), /King, WA/);
});

test('the picker asks the bounded route and says when it truncated', async () => {
  const calls = [];
  const { page, doc } = await buildPage({
    api: async (url) => {
      calls.push(url);
      return { ok: true, status: 200, data: { ok: true, limit: 25, total: 61,
        truncated: true, unknown: [],
        counties: [{ fips5: '53033', name: 'King', state: 'WA' }] } };
    },
  });
  await page.runCountyQuery('ki');
  assert.match(calls[0], /limit=25/, 'the picker must never ask for the whole table');
  assert.match(calls[0], /q=ki/);
  const shown = doc.els['cty-results'].text();
  assert.match(shown, /King, WA \(53033\)/);
  assert.match(shown, /of 61/, 'a truncated answer must say so');
});

test('an empty query clears the picker without asking the server', async () => {
  const calls = [];
  const { page, doc } = await buildPage({
    api: async (url) => { calls.push(url); return { ok: true, data: { ok: true } }; },
  });
  await page.runCountyQuery('   ');
  assert.strictEqual(calls.length, 0);
  assert.strictEqual(doc.els['cty-results'].children.length, 0);
});

test('picking a county posts the patch and adopts the SERVER answer', async () => {
  const posts = [];
  const { page, doc } = await buildPage({
    post: async (url, patch) => {
      posts.push([url, patch]);
      // The server normalises 6-digit to 5 and sorts; the page must take its
      // word for what was stored rather than keeping its own idea.
      return { ok: true, status: 200,
               data: { ok: true, config: { watch_fips: ['53033'] } } };
    },
    api: async () => ({ ok: true, status: 200, data: { ok: true, unknown: [],
      counties: [{ fips5: '53033', name: 'King', state: 'WA' }] } }),
  });
  page.setCfg({ watch_fips: [] });
  await page.setWatch('053033', true);
  assert.deepStrictEqual(posts, [['/api/nwr/config', { watch_fips: ['053033'] }]]);
  assert.deepStrictEqual(page.getCfg().watch_fips, ['53033']);
  assert.match(doc.els['cty-list'].text(), /King, WA/);
  assert.strictEqual(doc.els['cty-q'].value, '', 'the box empties for the next one');
});

test('a refused watch-list write is shown and changes nothing locally', async () => {
  const { page, doc } = await buildPage({
    post: async () => ({ ok: false, status: 400,
      data: { ok: false, error: 'not a FIPS code: \'nope\'', code: 'NWR_BAD_CONFIG' } }),
  });
  page.setCfg({ watch_fips: ['53033'] });
  await page.setWatch('nope', true);
  assert.match(doc.els.ctymsg.textContent, /not a FIPS code/);
  assert.deepStrictEqual(page.getCfg().watch_fips, ['53033']);
});
