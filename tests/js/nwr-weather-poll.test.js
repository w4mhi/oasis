'use strict';
// nwr-weather-poll.test.js — services/nwr/static/weather.html.
//
// Two generations of findings live in here:
//
// Finding 7: setInterval(poll, 5000) had no in-flight guard, and api() did not
// catch a fetch() rejection. Normally cosmetic -- but /api/nwr/status can sit
// behind a 6 s rtl_test call (Finding 6), which is long enough that one poll()
// can still be running when the next tick fires, and a dropped connection or
// offline tab turns fetch() rejecting into an unhandled promise rejection.
//
// v2: the page stopped owning the capture. The watch is the oasis-nwr daemon,
// service ops starts and stops it, and the page's Listen/Stop/Scan buttons and
// their routes are gone. What is pinned here is what replaced them: the channel
// pin, an <audio> element whose pause TEARS THE STREAM DOWN rather than merely
// pausing it, and the guarantee that no path on this page starts a capture.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', '..');
const src = fs.readFileSync(
  path.join(root, 'services', 'nwr', 'static', 'weather.html'), 'utf8');

// The page paints its status line with the SAME module both dashboards' cards
// use, so load the real one rather than a stand-in: a stub would let the page
// and the card drift again, which is the whole reason nwr-card.js exists.
const cardSandbox = {};
new Function('window', fs.readFileSync(
  path.join(root, 'common', 'js', 'nwr-card.js'), 'utf8')).call(cardSandbox, cardSandbox);
const nwrCardState = cardSandbox.nwrCardState;

function braceBody(start) {
  const braceStart = src.indexOf('{', start);
  let depth = 0, end = braceStart;
  for (; end < src.length; end++) {
    if (src[end] === '{') depth++;
    else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
  }
  return src.slice(start, end);
}

function extract(name) {
  const marker = 'async function ' + name + '(';
  const start = src.indexOf(marker);
  assert.ok(start !== -1, name + ' not found in weather.html');
  return braceBody(start);
}

function extractPlain(name) {
  // Same brace-matching as extract(), but for a bare `function name(`
  // declaration rather than an `async function name(`.
  const marker = 'function ' + name + '(';
  const start = src.indexOf(marker);
  assert.ok(start !== -1, name + ' not found in weather.html');
  return braceBody(start);
}

function extractHandlerBody(anchor) {
  // Event handlers are anonymous functions passed to addEventListener, so they
  // have no name to anchor on -- anchor on the call site instead.
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

const apiSrc = extract('api');
const pollSrc = extract('poll');
const postSrc = extractPlain('post');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

// The page's own constant, read from the page: a test that hardcoded the path
// would keep passing if the page started streaming from somewhere else.
const STREAM_URL = (/const STREAM_URL = '([^']+)';/.exec(src) || [])[1];
assert.ok(STREAM_URL, 'weather.html does not define STREAM_URL');

// ── A DOM stub with only what these functions touch ──────────────────────────

function fakeAudio() {
  return {
    paused: true, style: {}, _attrs: {}, plays: 0, loads: 0,
    getAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n)
      ? this._attrs[n] : null; },
    setAttribute(n, v) { this._attrs[n] = String(v); },
    removeAttribute(n) { delete this._attrs[n]; },
    load() { this.loads++; },
    play() { this.plays++; this.paused = false; return Promise.resolve(); },
    pause() { this.paused = true; },
  };
}

function fakeDoc() {
  const els = {};
  const mk = (id) => ({
    id: id || '', disabled: false, textContent: '', className: '',
    value: '', style: {}, children: [],
    appendChild(e) { this.children.push(e); },
  });
  ['state', 'pre', 'err', 'msg', 'pin', 'chan', 'log'].forEach((id) => { els[id] = mk(id); });
  els.audio = fakeAudio();
  return { getElementById: (id) => els[id] || null, createElement: () => mk(),
           activeElement: null };
}

// Every page-level helper, built once against one fake document so they share
// the CHANNELS the poll fills in -- exactly as they do in the browser.
const HELPERS = ['fmtTime', 'chanLabel', 'pinPatch', 'retuneNote', 'watchLine',
                 'armStream', 'detachStream', 'handlePlay', 'handlePause',
                 'setMsg', 'renderStatus', 'renderUnavailable'];

function buildPage(doc) {
  // renderStatus also drives the bell card and the watch-list chips now. Both
  // have their own harness in nwr-weather-bell.test.js, with the real
  // quiet-hours and bell-glyph modules behind them; here they are stubs, so
  // these tests keep being about what they were written for -- the status line,
  // the channel pin and the audio stream.
  const preamble =
    'const $ = function (id) { return document.getElementById(id); };\n' +
    'let CHANNELS = [];\n' +
    'let CFG = {};\n' +
    'function renderBell() {}\n' +
    'function syncWatch() {}\n' +
    'const STREAM_URL = ' + JSON.stringify(STREAM_URL) + ';\n';
  const body = HELPERS.map(extractPlain).join('\n');
  return new Function('document', 'nwrCardState',
    preamble + body + '\nreturn {' + HELPERS.join(',') + '};')(doc, nwrCardState);
}

// The shape GET /api/nwr/status actually returns (services/nwr/routes.py):
// local preconditions and config, plus the daemon's own account under `watch`.
// `capture` has not existed since the capture left Flask.
function statusOf(over) {
  const o = over || {};
  return {
    ok: true,
    preconditions: Object.assign({
      missing_deps: [], dongle_present: true, assigned: true, device: 'RTL #1',
      busy: false, holder: null, can_stream: true, can_scan: true,
    }, o.preconditions),
    watch: Object.assign({
      reachable: true, detail: null, phase: 'listening', listening: true,
      channel: 'WX7', channel_hz: 162550000, alerts_seen: 0, last_decode: null,
      last_error: null, scan: null, scan_weak: false, retune_pending: false,
      retry_in_s: 0, streams: 0, elapsed_s: 120,
    }, o.watch),
    config: Object.assign({ channel_hz: 162550000, pinned_channel: null }, o.config),
    channels: [{ hz: 162400000, label: 'WX1' }, { hz: 162550000, label: 'WX7' }],
  };
}

const NOW_MS = 1755000000000;      // fixed clock; the status line prints a start time

// ── api() / poll() ───────────────────────────────────────────────────────────

test('api() reports a rejected fetch as a failed request, not a throw', async () => {
  const fn = new Function('fetch', apiSrc + '\nreturn api;');
  const api = fn(() => Promise.reject(new Error('network down')));
  const r = await api('/api/nwr/status');
  assert.deepStrictEqual(r, { ok: false, status: 0, data: null });
});

test('poll() will not run two passes at once', async () => {
  let inFlight = 0, calls = 0, maxConcurrent = 0;
  let releaseFirst;
  const gate = new Promise((res) => { releaseFirst = res; });

  const api = async () => {
    calls++;
    inFlight++;
    maxConcurrent = Math.max(maxConcurrent, inFlight);
    if (calls === 1) { await gate; }        // hold the first status call open
    inFlight--;
    return { ok: true, status: 200, data: null };
  };

  const fn = new Function('api', 'renderStatus', 'renderAlerts', 'renderUnavailable',
    'let polling = false;\n' + pollSrc + '\nreturn poll;');
  const poll = fn(api, () => {}, () => {}, () => {});

  const first = poll();
  const second = poll();          // must be a no-op: a pass is already in flight
  releaseFirst();
  await Promise.all([first, second]);

  assert.strictEqual(calls, 2, 'exactly one full pass (status + alerts) should run');
  assert.strictEqual(maxConcurrent, 1, 'two poll() passes must never overlap');
});

test('a status the server cannot answer takes the line down instead of leaving it stale', async () => {
  // /api/nwr/status answers ok:true even when the watch is dead -- a down
  // daemon is watch.reachable false, an ANSWER. So !ok means FLASK could not be
  // asked, and the page used to skip the render entirely there: #state kept
  // reading LISTENING - WX7 162.550 - since ... for as long as the server
  // stayed down.
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({ config: { pinned_channel: 162550000 } }), NOW_MS);
  assert.match(doc.getElementById('state').textContent, /^LISTENING/);

  const fn = new Function('api', 'renderStatus', 'renderAlerts', 'renderUnavailable',
    'let polling = false;\n' + pollSrc + '\nreturn poll;');
  await fn(async () => ({ ok: false, status: 500, data: null }),
           p.renderStatus, () => {}, p.renderUnavailable)();

  const st = doc.getElementById('state');
  assert.doesNotMatch(st.textContent, /LISTENING/,
    'the page is still claiming a channel it can no longer check');
  assert.match(st.textContent, /status unavailable/);
  assert.strictEqual(st.className, 'down');
  // A bare renderStatus({ok:false}) would have done the line AND reset the
  // dropdown to Auto, reporting a pin the operator never cleared.
  assert.strictEqual(doc.getElementById('chan').value, '162550000',
    'an unanswerable status must not rewrite the pin');
});

// ── The channel pin ──────────────────────────────────────────────────────────

test('selecting a channel sends that channel as pinned_channel', () => {
  const p = buildPage(fakeDoc());
  assert.deepStrictEqual(p.pinPatch('162550000'), { pinned_channel: 162550000 });
});

test('selecting Auto clears the pin with null, not 0 or a string', () => {
  const p = buildPage(fakeDoc());
  const patch = p.pinPatch('auto');
  assert.deepStrictEqual(patch, { pinned_channel: null });
  // settings.save() takes null as "auto-scan picks the strongest"; anything
  // else falsy would be rejected as not one of the seven channels.
  assert.strictEqual(patch.pinned_channel, null);
});

test('the change handler POSTs the pin to /api/nwr/config, and nothing else', async () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  const posted = [];
  const postStub = async (url, body) => { posted.push([url, body]);
                                          return { ok: true, data: { ok: true } }; };
  const handler = new AsyncFunction('$', 'post', 'poll', 'pinPatch', 'setMsg',
    extractHandlerBody("$('chan').addEventListener('change', async function () {"));

  doc.getElementById('chan').value = '162400000';
  await handler((id) => doc.getElementById(id), postStub, () => {}, p.pinPatch, p.setMsg);

  assert.deepStrictEqual(posted, [['/api/nwr/config', { pinned_channel: 162400000 }]]);
  assert.strictEqual(doc.getElementById('chan').disabled, false,
    'the dropdown must be re-enabled once the POST lands');
});

test('a refused pin survives the poll that immediately follows it', async () => {
  // The v1 shape of this bug: the click handler painted a precise diagnosis and
  // renderStatus() wiped it milliseconds later. A config refusal is the page's
  // own message and the daemon records it nowhere, so it gets its own element
  // and the poll never writes there.
  const doc = fakeDoc();
  const p = buildPage(doc);
  const REFUSED = 'pinned_channel is not one of the seven NWR channels';

  const postStub = async () => ({ ok: false, status: 400,
    data: { ok: false, error: REFUSED, code: 'NWR_BAD_CONFIG' } });
  const handler = new AsyncFunction('$', 'post', 'poll', 'pinPatch', 'setMsg',
    extractHandlerBody("$('chan').addEventListener('change', async function () {"));

  doc.getElementById('chan').value = '162400000';
  await handler((id) => doc.getElementById(id), postStub, () => {}, p.pinPatch, p.setMsg);
  assert.strictEqual(doc.getElementById('msg').textContent, REFUSED);

  p.renderStatus(statusOf({}), NOW_MS);
  assert.strictEqual(doc.getElementById('msg').textContent, REFUSED,
    'the refusal must survive the poll triggered right after it');
});

test('the poll re-reads the pin, but not while the dropdown is open', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  const sel = doc.getElementById('chan');

  p.renderStatus(statusOf({ config: { pinned_channel: 162400000 } }), NOW_MS);
  assert.strictEqual(sel.value, '162400000');

  p.renderStatus(statusOf({ config: { pinned_channel: null } }), NOW_MS);
  assert.strictEqual(sel.value, 'auto', 'a cleared pin must show as Auto');

  doc.activeElement = sel;
  sel.value = '162550000';                       // the operator, mid-choice
  p.renderStatus(statusOf({ config: { pinned_channel: null } }), NOW_MS);
  assert.strictEqual(sel.value, '162550000',
    'the poll must not rewrite the dropdown under the operator');
});

test('an Auto entry is offered alongside the seven channels', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({}), NOW_MS);
  const opts = doc.getElementById('chan').children;
  assert.strictEqual(opts.length, 3, 'Auto plus the channels the API listed');
  assert.strictEqual(opts[0].value, 'auto');
});

// ── The retune a pin now causes ──────────────────────────────────────────────
// The page used to disclose a pin as "stored, takes effect when the watch next
// changes channel". POST /api/nwr/config now asks the daemon to retune, so that
// wait no longer exists; what is left to say is the gap while it happens.

test('a retune in progress is named, and names the channel it is heading for', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({
    config: { pinned_channel: 162400000 },
    watch: { listening: false, phase: 'retuning', retune_pending: true,
             channel_hz: 162550000 },
  }), NOW_MS);
  assert.match(doc.getElementById('pin').textContent, /changing to WX1/);
});

test('the retune line is not an alarm and does not join the red block', () => {
  // #pre is the environment: no audio encoder, no rtl_power -- things needing a
  // person with a keyboard. An operator-initiated channel change in that same
  // red reads far louder than it is.
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({
    config: { pinned_channel: 162400000 },
    watch: { listening: false, phase: 'retuning', retune_pending: true },
  }), NOW_MS);
  assert.doesNotMatch(doc.getElementById('pre').textContent, /changing to/,
    'the retune note must not land in the red environment block');
  assert.strictEqual(doc.getElementById('pin').className, '',
    'the retune note must never be painted .warn');
});

test('a pin the capture has adopted says nothing at all', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({ config: { pinned_channel: 162550000 },
                            watch: { listening: true, channel_hz: 162550000 } }), NOW_MS);
  assert.strictEqual(doc.getElementById('pin').textContent, '',
    'a retune that has landed is not news');
});

test('a retune whose capture will not start stops promising a channel change', () => {
  // retune_pending stays true until the new capture is RUNNING, so it is still
  // true through every failed attempt. Reading it alone would leave the page
  // saying "changing to WX1" forever at a watch that cannot start.
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({
    config: { pinned_channel: 162400000 },
    watch: { listening: false, phase: 'retrying', retune_pending: true,
             retry_in_s: 60, last_error: 'device or resource busy' },
  }), NOW_MS);
  assert.match(doc.getElementById('pin').textContent, /still trying to tune WX1/);
  assert.doesNotMatch(doc.getElementById('pin').textContent, /^changing to/);
});

test('a retune the watch never heard about is not announced', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({
    config: { pinned_channel: 162400000 },
    watch: { reachable: false, listening: false, retune_pending: true },
  }), NOW_MS);
  assert.strictEqual(doc.getElementById('pin').textContent, '',
    'an unreachable watch is retuning nothing');
});

test('the obsolete "takes effect later" disclosure is gone', () => {
  // The daemon retunes on request now (services/nwr/routes.py asks it on every
  // write that names pinned_channel). Telling the operator to wait for a
  // channel change that a healthy capture may never make was already wrong.
  assert.doesNotMatch(src, /takes effect when/,
    'the page still promises the pin will apply at some later time');
});

test('a saved pin the watch could not be told about is said out loud', async () => {
  // routes.py: the write SUCCEEDS with the daemon down -- losing the operator's
  // choice would be worse -- so ok stays true and retune.reachable is false.
  // Nothing else on the page would tell them the channel they just chose is not
  // going to start being received.
  const doc = fakeDoc();
  const p = buildPage(doc);
  const postStub = async () => ({ ok: true, status: 200, data: { ok: true,
    config: { pinned_channel: 162400000 },
    retune: { requested: true, reachable: false, accepted: false,
              detail: 'the watch is not reachable (Connection refused)' } } });
  const handler = new AsyncFunction('$', 'post', 'poll', 'pinPatch', 'setMsg',
    extractHandlerBody("$('chan').addEventListener('change', async function () {"));

  doc.getElementById('chan').value = '162400000';
  await handler((id) => doc.getElementById(id), postStub, () => {}, p.pinPatch, p.setMsg);
  assert.match(doc.getElementById('msg').textContent, /saved/);
  assert.match(doc.getElementById('msg').textContent, /Connection refused/);
});

test('a write with no reason to retune says nothing', async () => {
  // reachable is NULL, not false, when the patch never named pinned_channel --
  // "we did not ask" and "nobody answered" are different worlds, and only the
  // second is worth a message.
  const doc = fakeDoc();
  const p = buildPage(doc);
  const postStub = async () => ({ ok: true, status: 200, data: { ok: true,
    retune: { requested: false, reachable: null, accepted: false, detail: null } } });
  const handler = new AsyncFunction('$', 'post', 'poll', 'pinPatch', 'setMsg',
    extractHandlerBody("$('chan').addEventListener('change', async function () {"));

  await handler((id) => doc.getElementById(id), postStub, () => {}, p.pinPatch, p.setMsg);
  assert.strictEqual(doc.getElementById('msg').textContent, '');
});

// ── The audio element ────────────────────────────────────────────────────────

test('pause tears the stream down instead of merely pausing it', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  const el = doc.getElementById('audio');

  p.armStream(el, NOW_MS);
  el.play();
  el.pause();                                   // the operator presses pause
  const before = el.loads;
  assert.strictEqual(p.handlePause(el, NOW_MS + 1000), true);

  // A paused <audio> keeps its TCP connection open, so the daemon's subscriber
  // and its ffmpeg survive -- and it allows MAX_STREAMS = 3, so a few pauses
  // exhaust the budget. Only removing src AND calling load() drops it.
  assert.ok(el.loads > before, 'load() is the call that actually ends the stream');
  assert.notStrictEqual(el.getAttribute('src'), STREAM_URL + '?t=' + NOW_MS,
    'the torn-down URL must not still be attached');
});

test('the next play opens a NEW stream, not the one just torn down', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  const el = doc.getElementById('audio');

  p.armStream(el, NOW_MS);
  const first = el.getAttribute('src');
  el.play();
  el.pause();
  p.handlePause(el, NOW_MS + 7000);
  const second = el.getAttribute('src');

  assert.notStrictEqual(second, first, 'the cache-busting URL must be fresh');
  assert.match(second, new RegExp('^' + STREAM_URL.replace(/\//g, '\\/') + '\\?t=\\d+$'));
});

test('play with nothing attached arms a fresh URL and starts it', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  const el = doc.getElementById('audio');

  assert.strictEqual(el.getAttribute('src'), null);
  assert.strictEqual(p.handlePlay(el, NOW_MS), true);
  assert.match(el.getAttribute('src'), new RegExp('\\?t=' + NOW_MS + '$'));
  assert.strictEqual(el.plays, 1);

  // Already attached: the browser is playing it, and re-arming would restart
  // the stream out from under the operator.
  assert.strictEqual(p.handlePlay(el, NOW_MS + 1000), false);
  assert.strictEqual(el.plays, 1);
});

test("attaching a src fires a pause the page must not act on", () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  const el = doc.getElementById('audio');

  // The media load algorithm queues its own 'pause' when a new src is attached,
  // and play() has already superseded it by the time that task runs -- so the
  // element reports paused === false. Tearing down there would undo the attach
  // that is still in flight.
  p.handlePlay(el, NOW_MS);
  assert.strictEqual(el.paused, false);
  assert.strictEqual(p.handlePause(el, NOW_MS), false,
    'a pause event with el.paused false is the loader, not the operator');
  assert.match(el.getAttribute('src'), new RegExp('\\?t=' + NOW_MS + '$'));
});

test('a watch that is not listening leaves no stream attached', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  const el = doc.getElementById('audio');

  p.renderStatus(statusOf({}), NOW_MS);
  assert.ok(el.getAttribute('src'), 'a listening watch arms the element');
  assert.strictEqual(el.style.display, 'block');

  p.renderStatus(statusOf({ watch: { listening: false, phase: 'retrying' } }), NOW_MS);
  assert.strictEqual(el.getAttribute('src'), null,
    'an element left attached behind display:none is the orphaned-encoder shape');
  assert.strictEqual(el.style.display, 'none');
});

test('leaving the page tears the stream down', () => {
  // The page ships a same-origin Dashboard link, which is the back/forward-cache
  // case: Chromium keeps the frame and its media element alive. The daemon has
  // no reaper -- its handler holds the slot until a write to the socket raises,
  // which needs the browser to actually close -- so a bfcached page pins one of
  // MAX_STREAMS = 3 and its ffmpeg indefinitely.
  const doc = fakeDoc();
  const p = buildPage(doc);
  const el = doc.getElementById('audio');
  p.armStream(el, NOW_MS);
  el.play();
  const before = el.loads;

  const fn = new Function('$', 'detachStream',
    extractHandlerBody("window.addEventListener('pagehide', function () {"));
  fn((id) => doc.getElementById(id), p.detachStream);

  assert.strictEqual(el.getAttribute('src'), null, 'the stream is still attached');
  assert.ok(el.loads > before, 'load() is the call that actually ends the stream');
});

test('the teardown is on pagehide, and nothing tears down a merely hidden page', () => {
  // unload disqualifies the page from the very bfcache this is guarding, and is
  // deprecated. And a backgrounded kiosk tab must keep playing: the gap is
  // leave/navigate, not visibility.
  assert.doesNotMatch(src, /addEventListener\('unload'/,
    'unload disqualifies the page from bfcache');
  assert.doesNotMatch(src, /visibilitychange/,
    'a hidden tab must keep playing -- do not tear down on visibility');
});

test('a stream that recovers clears the warning it left standing', () => {
  // The error handler's message used to stand until the next channel change, so
  // a successful retry left "the audio stream would not open" on screen beside
  // audio that was playing.
  const doc = fakeDoc();
  const p = buildPage(doc);
  const STREAM_ERR = (/const STREAM_ERR = '([^']+)';/.exec(src) || [])[1];
  assert.ok(STREAM_ERR, 'weather.html does not define STREAM_ERR');

  const fn = new Function('$', 'setMsg', 'STREAM_ERR',
    extractHandlerBody("$('audio').addEventListener(ev, function () {"));
  p.setMsg(STREAM_ERR);
  fn((id) => doc.getElementById(id), p.setMsg, STREAM_ERR);
  assert.strictEqual(doc.getElementById('msg').textContent, '');

  // ...but only its own message: #msg is also where a refused pin lands, and a
  // stream starting says nothing about that.
  const REFUSED = 'pinned_channel is not one of the seven NWR channels';
  p.setMsg(REFUSED);
  fn((id) => doc.getElementById(id), p.setMsg, STREAM_ERR);
  assert.strictEqual(doc.getElementById('msg').textContent, REFUSED);
});

// ── The status line ──────────────────────────────────────────────────────────

test('the status line says running, on what channel, and since when', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({ watch: { elapsed_s: 3600 } }), NOW_MS);
  const st = doc.getElementById('state');
  assert.match(st.textContent, /^LISTENING/);
  assert.match(st.textContent, /WX7 162\.550/);
  assert.match(st.textContent, /since 2025-08-12 11:00Z/);
  assert.strictEqual(st.className, 'live');
});

test('a watch that cannot be reached says so, in red', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({ watch: { reachable: false, listening: false,
                                     detail: 'watch unavailable (Connection refused)' } }),
                 NOW_MS);
  const st = doc.getElementById('state');
  assert.match(st.textContent, /WATCH DOWN/);
  assert.match(st.textContent, /Connection refused/);
  assert.strictEqual(st.className, 'down');
  assert.doesNotMatch(st.textContent, /since/, 'a watch that is down started nothing');
});

test('no dongle assigned is amber, and names the fix', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({ preconditions: { assigned: false } }), NOW_MS);
  assert.strictEqual(doc.getElementById('state').className, 'warn');
  assert.match(doc.getElementById('state').textContent, /assign a dongle in Setup/);
});

// ── watch.last_error: the v1 fix, on the key the API actually returns ────────

test('watch.last_error is rendered when present', () => {
  const ERR = 'another service is already using the RTL-SDR dongle';
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({ watch: { listening: false, phase: 'retrying',
                                     last_error: ERR } }), NOW_MS);
  assert.strictEqual(doc.getElementById('err').textContent, ERR);
  assert.strictEqual(doc.getElementById('err').className, 'warn');
});

test('no last_error: the error slot stays empty', () => {
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({}), NOW_MS);
  assert.strictEqual(doc.getElementById('err').textContent, '');
});

test('a stale last_error is not shown once the watch is listening again', () => {
  const ERR = 'another service is already using the RTL-SDR dongle';
  const doc = fakeDoc();
  const p = buildPage(doc);

  p.renderStatus(statusOf({ watch: { listening: false, phase: 'retrying',
                                     last_error: ERR } }), NOW_MS);
  assert.strictEqual(doc.getElementById('err').textContent, ERR);

  // supervise() overwrites last_error with the result of every start attempt in
  // the same locked update that sets the phase, so a successful start clears it
  // to null. The client mirrors that invariant rather than tracking its own
  // staleness flag -- it must not keep shouting the old message once the box
  // reports it is working.
  p.renderStatus(statusOf({}), NOW_MS);
  assert.strictEqual(doc.getElementById('err').textContent, '',
    'a stale start failure is still showing after the watch recovered');
});

test('the environment notes and the daemon error cannot erase each other', () => {
  const ERR = 'rtl_fm exited immediately';
  const doc = fakeDoc();
  const p = buildPage(doc);
  p.renderStatus(statusOf({ preconditions: { can_stream: false },
                            watch: { listening: false, phase: 'retrying',
                                     last_error: ERR } }), NOW_MS);
  assert.match(doc.getElementById('pre').textContent, /no audio encoder/);
  assert.strictEqual(doc.getElementById('err').textContent, ERR);
});

// ── No path from this page starts a capture ──────────────────────────────────

test('the page reads the daemon and starts nothing', () => {
  // services/nwr/common/listener.py start() is still live and reachable from
  // the process, and preconditions.busy reads FALSE for this page against its
  // own daemon (sdr_rx.dongle_busy excludes the caller's own service, and
  // oasis-nwr is nwr's unit). So a Listen button here would spawn a second
  // rtl_fm that dies at usb_claim_interface, with nothing on the page to warn
  // about it. Removing the controls is what closes that, and this pins it shut.
  assert.doesNotMatch(src, /\/api\/nwr\/scan/, 'the retired scan route is back');
  assert.doesNotMatch(src, /\/api\/nwr\/listen\/stop/, 'the retired stop route is back');
  assert.doesNotMatch(src, /post\(\s*['"]\/api\/nwr\/listen['"]/,
    'the retired listen route is back');
  assert.doesNotMatch(src, /id="(listen|stop|scan|usebest)"/,
    'a claim control is back on the page');

  // Every API path the page NAMES, however it names it. The previous guard read
  // only `post('...')` literals, which a double quote, a template literal, a
  // variable -- or the realistic one, api('/api/nwr/whatever', {method:'POST'})
  // through the general helper the page already uses for its GETs -- would all
  // walk straight past. Whole-set equality over the source covers fetches,
  // forms and links in one assertion, and a route arriving by any of them has
  // to be added here deliberately.
  const routes = new Set(src.match(/\/api\/nwr\/[a-z/]+/g) || []);
  assert.deepStrictEqual([...routes].sort(), [
    '/api/nwr/alerts',        // read: the decode log
    '/api/nwr/config',        // write: the channel pin, the bell, the watch list
    '/api/nwr/counties',      // read: the watch-list picker's source
    '/api/nwr/listen/stream', // read: the daemon's audio, relayed
    '/api/nwr/status',        // read: preconditions, config and the watch
  ], 'weather.html names an API route it has no business touching');
});

test('the page reads `watch`, never the retired `capture` key', () => {
  assert.doesNotMatch(src, /\.capture\b/,
    'weather.html still reads the `capture` key /api/nwr/status stopped returning');
  assert.ok(src.includes('/common/js/nwr-card.js'),
    'the status line must use the shared state module, not a private copy');
});
