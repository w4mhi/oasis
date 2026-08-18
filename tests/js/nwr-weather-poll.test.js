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
