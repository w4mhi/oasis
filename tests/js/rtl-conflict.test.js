'use strict';
// The dashboard's same-dongle conflict prompt (index.html
// resolveHardwareConflict), exercised as CODE rather than asserted as text:
// the function is sliced out of the page and run with fakes for the globals it
// reads, so a change to the list it walks is a test result, not a diff.
//
// Why it matters: rtl-sdr is an ADVISORY assignment (can_assign lets several
// services share one dongle), and there is no server-side gate — the comment
// above the list says so. This prompt is the only thing standing between the
// operator and a dump1090 that dies on usb_claim_interface with a DOWN card
// and no explanation.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

// The slice: the consumer list through the end of the resolver.
function conflictSource() {
  const start = html.indexOf('const RTL_SDR_CARDS =');
  assert.notStrictEqual(start, -1, 'index.html: RTL_SDR_CARDS not found');
  const end = html.indexOf('\nasync function togglePower', start);
  assert.notStrictEqual(end, -1, 'index.html: resolveHardwareConflict not found');
  return html.slice(start, end);
}

// Build a live resolver over fake page state. `services` maps a card id to
// {state, device}; every card in it is assigned that device_id.
function resolver(services) {
  const calls = { confirms: [], stopped: [], pings: 0 };
  const svcStates = {};
  const hwServices = {};
  const HW_SERVICE_FOR_CARD = { adsb: 'adsb', wlrf: 'winlink', feed: 'aprs',
                                owrx: 'openwebrx', nwr: 'nwr' };
  const SVC_UNITS = { feed: 'aprs-sdr-feed', adsb: 'dump1090-fa',
                      owrx: 'openwebrx', nwr: 'oasis-nwr', gw: 'graywolf' };
  Object.keys(services).forEach((id) => {
    svcStates[id] = services[id].state;
    hwServices[HW_SERVICE_FOR_CARD[id]] = { device_id: services[id].device };
  });
  const fakeConfirm = (msg) => { calls.confirms.push(msg); return true; };
  const fakeFetch = async (url, opts) => {
    calls.stopped.push(JSON.parse(opts.body));
    return { ok: true, json: async () => ({ ok: true }) };
  };
  const factory = new Function(
    '_svcStates', '_hwLast', 'HW_SERVICE_FOR_CARD', 'SVC_UNITS',
    'confirm', 'fetch', 'pingAll',
    conflictSource() + '\nreturn resolveHardwareConflict;');
  const fn = factory(svcStates, { services: hwServices }, HW_SERVICE_FOR_CARD,
                     SVC_UNITS, fakeConfirm, fakeFetch, () => { calls.pings++; });
  return { resolve: fn, calls };
}

test('the always-on weather watch is offered for stopping before ADS-B starts', async () => {
  // Single-dongle Pi: the dongle is co-assigned to nwr and adsb (advisory,
  // permitted), oasis-nwr is up and holding it, operator presses START on
  // ADS-B. Without nwr in the consumer list no prompt appears at all and
  // dump1090 dies on usb_claim_interface with the card DOWN and no reason.
  const { resolve, calls } = resolver({
    adsb: { state: 'down', device: 'rtl-1' },
    nwr:  { state: 'up',   device: 'rtl-1' },
  });
  const proceed = await resolve('adsb');
  assert.strictEqual(calls.confirms.length, 1,
    'no conflict prompt for a dongle the weather watch is holding');
  assert.match(calls.confirms[0], /weather watch/i,
    'the prompt must name the service actually holding the dongle');
  assert.strictEqual(proceed, false, 'the start must be abandoned while the other stops');
  assert.deepStrictEqual(calls.stopped, [{ unit: 'oasis-nwr', action: 'stop' }]);
  assert.strictEqual(calls.pings, 1);
});

test('the APRS SDR feed gets the same warning about the watch', async () => {
  const { resolve, calls } = resolver({
    feed: { state: 'down', device: 'rtl-1' },
    nwr:  { state: 'warn', device: 'rtl-1' },   // WEAK: running, holding the tuner
  });
  assert.strictEqual(await resolve('feed'), false);
  assert.deepStrictEqual(calls.stopped, [{ unit: 'oasis-nwr', action: 'stop' }]);
});

test('a watch on a DIFFERENT dongle is not a conflict', async () => {
  const { resolve, calls } = resolver({
    adsb: { state: 'down', device: 'rtl-1' },
    nwr:  { state: 'up',   device: 'rtl-2' },
  });
  assert.strictEqual(await resolve('adsb'), true);
  assert.strictEqual(calls.confirms.length, 0);
});

test('a stopped watch is not a conflict', async () => {
  const { resolve, calls } = resolver({
    adsb: { state: 'down', device: 'rtl-1' },
    nwr:  { state: 'down', device: 'rtl-1' },
  });
  assert.strictEqual(await resolve('adsb'), true);
  assert.strictEqual(calls.confirms.length, 0);
});

test('every RTL-SDR consumer in the list can be named and stopped', () => {
  // A card in the list with no label prints "undefined is currently using this
  // RTL-SDR"; one with no unit posts {unit: undefined} and stops nothing.
  const src = conflictSource();
  const decls = src.slice(0, src.indexOf('\n\n'));   // both consts, nothing else
  const cards = new Function(decls + '\nreturn { RTL_SDR_CARDS, RTL_SDR_LABEL };')();
  cards.RTL_SDR_CARDS.forEach((id) => {
    assert.ok(cards.RTL_SDR_LABEL[id], 'no operator-facing label for ' + id);
  });
  assert.ok(cards.RTL_SDR_CARDS.includes('nwr'),
    'the weather watch holds its dongle indefinitely — it is the one consumer ' +
    'that MUST be in this list');
});
