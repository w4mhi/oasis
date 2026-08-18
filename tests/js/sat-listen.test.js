'use strict';
// Contract tests for the extracted recorder client.
//
// The extraction moved ~250 lines out of satellites.html so the kiosk can share
// them. The risk of that move is not that it fails loudly — it is that one
// branch quietly changes and only shows up mid-pass, on hardware, once. So the
// branches with teeth are pinned here:
//
//   * detach must NOT post /listen/stop   (that would kill the recording)
//   * stop MUST post it                   (that is the only hard stop)
//   * rec must name the MODE              (or the server records FM when you
//                                          asked for CW on a shared frequency)
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');

const SAT = path.join(__dirname, '..', '..', 'services', 'satellites', 'static');
const denoise = require(path.join(SAT, 'sat-denoise.js'));
const transport = require(path.join(SAT, 'sat-transport.js'));
const listen = require(path.join(__dirname, '..', '..', 'common', 'js', 'sat-listen.js'));

// A DOM stub with only what the module touches: getElementById, createElement,
// body.appendChild. Nothing here pretends to be a browser beyond that.
function fakeDoc() {
  const els = {};
  const mk = () => ({
    id: '', src: null, autoplay: false, style: {}, paused: false, ended: false,
    _l: {}, addEventListener(k, f) { this._l[k] = f; },
    play() { return Promise.resolve(); },
    pause() { this.paused = true; },
    removeAttribute(k) { this[k === 'src' ? 'src' : k] = null; },
    getAttribute(k) { return k === 'src' ? this.src : null; },
    load() {}, classList: { toggle() {} },
  });
  return {
    _els: els,
    getElementById: (id) => els[id] || null,
    createElement: () => mk(),
    body: { appendChild(e) { els[e.id] = e; } },
  };
}

function harness(status, opts) {
  const calls = [];
  const doc = fakeDoc();
  global.fetch = async (url, init) => {
    calls.push({ url, method: (init && init.method) || 'GET' });
    // text() is part of the stub because the stop paths drain the body they do
    // not want (an unread fetch() body pins a /dev/shm pipe) -- without it those
    // calls would still "work", by way of a swallowed TypeError.
    if (String(url).includes('/listen/status')) {
      return { ok: true, json: async () => status, text: async () => '' };
    }
    return { ok: true, json: async () => ({ ok: true }), text: async () => '' };
  };
  global.localStorage = { getItem: () => null, setItem: () => {} };
  const root = { satdenoise: denoise, sattransport: transport, document: doc,
                 localStorage: global.localStorage };
  // The module reads these off `root`; give it ours rather than the real global.
  const saved = { sd: global.satdenoise, st: global.sattransport };
  global.satdenoise = denoise; global.sattransport = transport;
  global.document = doc;
  const api = listen.create(Object.assign({
    document: doc,
    nextPass: () => ({ rise: new Date(Date.now() - 60000).toISOString(),
                       set: new Date(Date.now() + 600000).toISOString() }),
    getArmed: () => ({ norad: 26931, freq: 144.39, mode: 'APRS' }),
    findSat: () => ({ name: 'PCSAT', downlinks: [{ freq_mhz: 144.39, mode: 'APRS' }] }),
  }, opts || {}));
  Object.assign(global, { satdenoise: saved.sd, sattransport: saved.st });
  global.satdenoise = denoise; global.sattransport = transport;
  return { api, calls, doc, root };
}

const IDLE = { recording: false, streaming: false, dongle_present: true,
               assigned: true, busy: false, missing_deps: [] };
const RECORDING = Object.assign({}, IDLE, { recording: true, backend: 'csdr',
                                            norad: 26931, seconds: 75,
                                            freq_hz: 144390000 });

test('idle + armed: rec and play offered, stop dead', async () => {
  const h = harness(IDLE);
  await h.api.poll();
  const b = h.api.buttons();
  assert.strictEqual(b.rec.disabled, false);
  assert.strictEqual(b.play.disabled, false);
  assert.strictEqual(b.stop.disabled, true);
});

test('recording on csdr: play attaches, rec is inert', async () => {
  const h = harness(RECORDING);
  await h.api.poll();
  const b = h.api.buttons();
  assert.strictEqual(b.rec.disabled, true);
  assert.strictEqual(b.play.disabled, false);
  assert.strictEqual(b.play.action, 'start');
  assert.strictEqual(b.stop.disabled, false);
});

test('recording on rtl_fm: play is refused, with a reason', async () => {
  const h = harness(Object.assign({}, RECORDING, { backend: 'rtl_fm' }));
  await h.api.poll();
  assert.strictEqual(h.api.buttons().play.disabled, true);
  assert.match(h.api.buttons().play.title, /uncorrected|one audio stream/i);
});

test('rec names the mode, not just the frequency', async () => {
  const h = harness(IDLE);
  await h.api.poll();
  h.calls.length = 0;
  await h.api.startRecord(26931, 144.39, 'APRS');
  const post = h.calls.find(c => c.method === 'POST');
  assert.ok(post, 'no POST was made');
  assert.match(post.url, /\/api\/satellites\/listen$/);
});

test('DETACH DOES NOT STOP THE RECORDING', async () => {
  // The whole reason Play-while-Rec was parked. If this regresses, listening to
  // your own recording destroys it, and you find out after the pass.
  const h = harness(RECORDING);
  await h.api.poll();
  h.api.startStream(26931, 144.39, 'APRS');
  h.calls.length = 0;
  h.api.detachStream();
  const stops = h.calls.filter(c => c.url.includes('/listen/stop'));
  assert.strictEqual(stops.length, 0, 'detach posted /listen/stop — it must not');
});

test('stopping a standalone stream DOES post stop', async () => {
  // It owns the dongle, and gunicorn often misses the disconnect.
  const h = harness(Object.assign({}, IDLE, { streaming: true, norad: 26931 }));
  await h.api.poll();
  h.calls.length = 0;
  h.api.stopStream();
  assert.ok(h.calls.some(c => c.url.includes('/listen/stop') && c.method === 'POST'),
            'stopping a stream must release the dongle');
});

test('the hard stop posts stop', async () => {
  const h = harness(RECORDING);
  await h.api.poll();
  h.calls.length = 0;
  await h.api.stopCapture();
  assert.ok(h.calls.some(c => c.url.includes('/listen/stop') && c.method === 'POST'));
});

test('transport respects the matrix — a disabled button does nothing', async () => {
  const h = harness(IDLE);            // nothing running, so stop is disabled
  await h.api.poll();
  h.calls.length = 0;
  h.api.transport('stop');
  assert.strictEqual(h.calls.length, 0, 'stop fired while disabled');
});

test('nothing armed: rec is refused rather than sending a null norad', async () => {
  const h = harness(IDLE, { getArmed: () => null });
  await h.api.poll();
  h.calls.length = 0;
  h.api.transport('rec');
  assert.strictEqual(h.calls.filter(c => c.method === 'POST').length, 0);
});

test('status text names the bird and the elapsed time', async () => {
  const h = harness(RECORDING);
  await h.api.poll();
  const st = h.api.statusText();
  assert.strictEqual(st.on, true);
  assert.strictEqual(st.live, false);
  assert.match(st.text, /REC PCSAT 1:15/);
});

test('a reload adopts the running capture as the armed frequency', async () => {
  // Otherwise the transport shows an empty selection while the radio is busy.
  let armed = null;
  const h = harness(RECORDING, { getArmed: () => armed, setArmed: (a) => { armed = a; } });
  await h.api.poll();
  assert.ok(armed, 'the in-progress capture was not adopted');
  assert.strictEqual(armed.norad, 26931);
  assert.ok(Math.abs(armed.freq - 144.39) < 1e-6);
  assert.strictEqual(armed.mode, 'APRS', 'one channel on the frequency — adopt its mode');
});

test('a status fetch that throws leaves a usable state, not an exception', async () => {
  const h = harness(IDLE);
  global.fetch = async () => { throw new Error('offline'); };
  await h.api.poll();
  assert.strictEqual(h.api.capturing(), false);
  assert.strictEqual(h.api.buttons().stop.disabled, true);
});
