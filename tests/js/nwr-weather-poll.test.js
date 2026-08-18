'use strict';
// Finding 7: services/nwr/static/weather.html's setInterval(poll, 5000) had
// no in-flight guard, and api() did not catch a fetch() rejection. Normally
// cosmetic -- but /api/nwr/status can sit behind a 6 s rtl_test call
// (Finding 6), which is long enough that one poll() can still be running
// when the next setInterval tick fires, and a dropped connection/offline tab
// turns fetch() rejecting into an unhandled promise rejection.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', '..', 'services', 'nwr', 'static', 'weather.html'), 'utf8');

function extract(name) {
  const marker = 'async function ' + name + '(';
  const start = src.indexOf(marker);
  assert.ok(start !== -1, name + ' not found in weather.html');
  const braceStart = src.indexOf('{', start);
  let depth = 0, end = braceStart;
  for (; end < src.length; end++) {
    if (src[end] === '{') depth++;
    else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
  }
  return src.slice(start, end);
}

const apiSrc = extract('api');
const pollSrc = extract('poll');

test('api() reports a rejected fetch as a failed request, not a throw', async () => {
  const fn = new Function('fetch', apiSrc + '\nreturn api;');
  const api = fn(() => Promise.reject(new Error('network down')));
  const r = await api('/api/nwr/status');
  assert.deepStrictEqual(r, { ok: false, status: 0, data: null });
});

// --- Coverage for the 192.168.1.46 repro: POST /listen 409s with a precise
// diagnosis ("another service is already using the RTL-SDR dongle"), but the
// page renders NOTHING -- renderStatus() clobbers whatever the click handler
// just painted with an empty `notes` string (every precondition reads green,
// because the conflict engine does not model a bare rtl_433 holding the
// dongle), and capture.last_error is never read by the page at all.
//
// These tests extract renderStatus(), post(), and the #listen click handler
// the same way the tests above extract api()/poll(), and drive them against
// a fake DOM + mocked fetch so the real clobber path is exercised, not a
// hand-rolled stand-in for it.

function extractPlain(name) {
  // Same brace-matching as extract() above, but for a bare `function name(`
  // declaration rather than an `async function name(`.
  const marker = 'function ' + name + '(';
  const start = src.indexOf(marker);
  assert.ok(start !== -1, name + ' not found in weather.html');
  const braceStart = src.indexOf('{', start);
  let depth = 0, end = braceStart;
  for (; end < src.length; end++) {
    if (src[end] === '{') depth++;
    else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
  }
  return src.slice(start, end);
}

function extractClickBody() {
  // The #listen click handler is an anonymous function passed to
  // addEventListener, so it has no name to anchor on -- anchor on the call
  // site instead and brace-match from the first '{'.
  const anchor = "$('listen').addEventListener('click', async function () {";
  const start = src.indexOf(anchor);
  assert.ok(start !== -1, 'listen click handler not found in weather.html');
  const braceStart = start + anchor.length - 1;
  let depth = 0, end = braceStart;
  for (; end < src.length; end++) {
    if (src[end] === '{') depth++;
    else if (src[end] === '}') { depth--; if (depth === 0) { end++; break; } }
  }
  return src.slice(braceStart + 1, end - 1);
}

const renderStatusSrc = extractPlain('renderStatus');
const postSrc = extractPlain('post');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

// A DOM stub with only what these functions touch: getElementById (against a
// fixed id set) and createElement/appendChild for the channel <select>.
function fakeDoc() {
  const els = {};
  const mk = (id) => ({
    id: id || '', disabled: false, textContent: '', className: '',
    value: '', style: {}, src: '', children: [],
    appendChild(e) { this.children.push(e); },
  });
  ['listen', 'stop', 'scan', 'state', 'pre', 'err', 'chan', 'audio',
   'usebest', 'scanbars', 'scancard', 'log'].forEach((id) => { els[id] = mk(id); });
  return { getElementById: (id) => els[id] || null, createElement: () => mk() };
}

function buildRenderStatus(doc) {
  return new Function('document',
    'const $ = function (id) { return document.getElementById(id); };\n' +
    'let listening = false;\nlet CHANNELS = [];\n' +
    renderStatusSrc + '\nreturn renderStatus;')(doc);
}

const GREEN_PRE = { assigned: true, busy: false, holder: null, dongle_present: true,
                     missing_deps: [], can_stream: true, can_scan: true };

// Mirrors the real box: every precondition is green, because the conflict
// engine does not model a bare rtl_433 holding the dongle -- so `notes` in
// renderStatus() always comes back empty in this scenario. Callers override
// `capture` to set last_error/listening for the case under test.
function statusOf(overrides) {
  return {
    ok: true,
    capture: Object.assign({ listening: false, channel_hz: 162400000, channel: 'WX1',
                              elapsed_s: 0, alerts_seen: 0, last_error: null },
                            (overrides && overrides.capture) || {}),
    preconditions: Object.assign({}, GREEN_PRE, (overrides && overrides.preconditions) || {}),
    channels: [{ hz: 162400000, label: 'WX1' }],
    config: { channel_hz: 162400000 },
  };
}

test('a start failure survives the poll that immediately follows it', async () => {
  const ERR = 'another service is already using the RTL-SDR dongle';
  const doc = fakeDoc();

  const fetchStub = async (url, opts) => {
    if (String(url).indexOf('/api/nwr/listen') !== -1 && opts && opts.method === 'POST') {
      return { ok: false, status: 409, text: async () => JSON.stringify(
        { ok: false, code: 'NWR_START_FAILED', error: ERR }) };
    }
    if (String(url).indexOf('/api/nwr/status') !== -1) {
      // The server has already recorded this in capture.last_error by the
      // time this GET lands -- same start() call, same HTTP round trip.
      return { ok: true, status: 200,
               text: async () => JSON.stringify(statusOf({ capture: { last_error: ERR } })) };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true, alerts: [], active: [] }) };
  };

  const apiFn = new Function('fetch', apiSrc + '\nreturn api;')(fetchStub);
  const postFn = new Function('api', postSrc + '\nreturn post;')(apiFn);
  const renderStatusFn = buildRenderStatus(doc);
  const realPoll = new Function('api', 'renderStatus', 'renderAlerts',
    'let polling = false;\n' + pollSrc + '\nreturn poll;')(apiFn, renderStatusFn, () => {});

  // The click handler calls poll() without awaiting it -- capture the promise
  // it kicks off so the test can wait on the exact poll pass the handler
  // triggers, rather than racing poll()'s own re-entrancy guard with a second
  // independent call.
  let pending = null;
  const pollStub = () => { pending = realPoll(); return pending; };
  const dollar = (id) => doc.getElementById(id);
  const clickFn = new AsyncFunction('$', 'post', 'poll', extractClickBody());

  await clickFn(dollar, postFn, pollStub);
  await pending;

  assert.strictEqual(doc.getElementById('err').textContent, ERR,
    'the diagnosed failure must survive the poll triggered right after it');
});

test('capture.last_error is rendered when present', () => {
  const ERR = 'another service is already using the RTL-SDR dongle';
  const doc = fakeDoc();
  const renderStatusFn = buildRenderStatus(doc);
  renderStatusFn(statusOf({ capture: { last_error: ERR } }));
  assert.strictEqual(doc.getElementById('err').textContent, ERR);
  assert.strictEqual(doc.getElementById('err').className, 'warn');
});

test('no last_error: the error slot stays empty', () => {
  const doc = fakeDoc();
  const renderStatusFn = buildRenderStatus(doc);
  renderStatusFn(statusOf({}));
  assert.strictEqual(doc.getElementById('err').textContent, '');
});

test('a stale last_error is not shown once listening succeeds', () => {
  const ERR = 'another service is already using the RTL-SDR dongle';
  const doc = fakeDoc();
  const renderStatusFn = buildRenderStatus(doc);

  renderStatusFn(statusOf({ capture: { last_error: ERR } }));
  assert.strictEqual(doc.getElementById('err').textContent, ERR);

  // A later attempt succeeds: the server clears last_error in the same
  // _state update that flips listening true (services/nwr/common/listener.py
  // start()). The client mirrors that invariant rather than tracking its own
  // staleness flag -- it must not keep shouting the old message once the box
  // reports it is working.
  renderStatusFn(statusOf({ capture: { listening: true, last_error: null } }));
  assert.strictEqual(doc.getElementById('err').textContent, '',
    'a stale start failure is still showing after a successful listen');
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

  const fn = new Function('api', 'renderStatus', 'renderAlerts',
    'let polling = false;\n' + pollSrc + '\nreturn poll;');
  const poll = fn(api, () => {}, () => {});

  const first = poll();
  const second = poll();          // must be a no-op: a pass is already in flight
  releaseFirst();
  await Promise.all([first, second]);

  assert.strictEqual(calls, 2, 'exactly one full pass (status + alerts) should run');
  assert.strictEqual(maxConcurrent, 1, 'two poll() passes must never overlap');
});
