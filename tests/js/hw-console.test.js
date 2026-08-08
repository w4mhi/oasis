'use strict';
// Unit tests for the shared HW/SRV console logic (cell state, flip decision,
// rail health) used by both index.html and the kiosk dashboard overlay.
const test = require('node:test');
const assert = require('node:assert');
const R = require('../../common/js/hw-console.js');

const rtl = { id: 'rtl-1', kind: 'rtl-sdr', assigned: 'aprs', running: true, locked: false };
const aprs = { id: 'aprs', kinds: ['rtl-sdr'] };
const adsb = { id: 'adsb', kinds: ['rtl-sdr'] };
const winlink = { id: 'winlink', kinds: ['dra-pi', 'digirig'] };

test('cstate: na when the device kind cannot serve the service', () => {
  assert.strictEqual(R.oasisCstate(rtl, winlink), 'na');   // rtl-sdr not in winlink.kinds
});

test('cstate: on when assigned here and running, off elsewhere', () => {
  assert.strictEqual(R.oasisCstate(rtl, aprs), 'on');       // assigned aprs + running
  assert.strictEqual(R.oasisCstate(rtl, adsb), 'off');      // eligible but not assigned
});

test('cstate: stopped when assigned here but not running', () => {
  const idle = { ...rtl, running: false };
  assert.strictEqual(R.oasisCstate(idle, aprs), 'stopped');
});

test('flipPlan: na cell is a no-op', () => {
  assert.deepStrictEqual(R.oasisFlipPlan(rtl, winlink), { action: 'none' });
});

test('flipPlan: a locked device refuses any move', () => {
  const locked = { ...rtl, locked: true };
  assert.deepStrictEqual(R.oasisFlipPlan(locked, adsb), { action: 'locked' });
  // even the cell it is running on refuses (must unlock first)
  assert.deepStrictEqual(R.oasisFlipPlan(locked, aprs), { action: 'locked' });
});

test('flipPlan: running cell → stop; off/stopped cell → route', () => {
  assert.deepStrictEqual(R.oasisFlipPlan(rtl, aprs), { action: 'stop' });   // on → stop
  assert.deepStrictEqual(R.oasisFlipPlan(rtl, adsb), { action: 'route' });  // off → route
  const idle = { ...rtl, running: false };
  assert.deepStrictEqual(R.oasisFlipPlan(idle, aprs), { action: 'route' }); // stopped → route
});

test('railHealth: crit outranks warn; empty is ok', () => {
  assert.deepStrictEqual(R.oasisRailHealth([]), { lvl: 'ok', glyph: '✓', count: 0 });
  assert.deepStrictEqual(R.oasisRailHealth([{ severity: 'warn' }]), { lvl: 'warn', glyph: '!', count: 1 });
  const mixed = [{ severity: 'warn' }, { severity: 'crit' }];
  assert.deepStrictEqual(R.oasisRailHealth(mixed), { lvl: 'crit', glyph: '⚠', count: 2 });
});

test('hwConsole: exposes the console action contract', () => {
  ['fetchState', 'route', 'serviceStop', 'lock', 'stopAll', 'guardianState', 'guardianCancel']
    .forEach(k => assert.strictEqual(typeof R.oasisHwConsole[k], 'function', k + ' is a function'));
});

// ── Advisory services: a claim the console cannot start ──────────────────────
// satellites' recorder is an ad-hoc rtl_fm subprocess launched from the
// satellites page, not a systemd unit. The console had nothing to start, so the
// cell sat on 'stopped' (amber, "this failed to start") forever — and tapping it
// planned a route that ran `systemctl start satellites-listen`, a unit that does
// not exist. The server now flags such services `startable: false`.
const sat = { id: 'satellites', kinds: ['rtl-sdr'], startable: false };
const satDev = { id: 'rtl-1', kind: 'rtl-sdr', assigned: 'satellites',
                 running: false, locked: false };

test('cstate: an assigned advisory service is READY, not stopped', () => {
  assert.strictEqual(R.oasisCstate(satDev, sat), 'ready');
});

test('cstate: an advisory service still shows ON while it is actually running', () => {
  assert.strictEqual(R.oasisCstate({ ...satDev, running: true }, sat), 'on');
});

test('cstate: startable services are unaffected by the new state', () => {
  const startable = { id: 'satellites', kinds: ['rtl-sdr'], startable: true };
  assert.strictEqual(R.oasisCstate(satDev, startable), 'stopped');
});

test('cstate: an older server that omits `startable` keeps the old behaviour', () => {
  // Only an explicit false selects 'ready' — a cached page against a new server,
  // or a new page against an old one, must not invent a state.
  assert.strictEqual(R.oasisCstate(satDev, { id: 'satellites', kinds: ['rtl-sdr'] }),
                     'stopped');
});

test('flipPlan: tapping a READY cell releases the device', () => {
  // Tap still toggles the claim — the habit every other cell teaches — there is
  // simply no unit to stop on the way.
  assert.deepStrictEqual(R.oasisFlipPlan(satDev, sat), { action: 'release' });
});

test('flipPlan: a running advisory service still stops', () => {
  assert.deepStrictEqual(R.oasisFlipPlan({ ...satDev, running: true }, sat),
                         { action: 'stop' });
});

test('flipPlan: a locked device still refuses, advisory or not', () => {
  assert.deepStrictEqual(R.oasisFlipPlan({ ...satDev, locked: true }, sat),
                         { action: 'locked' });
});

test('console contract exposes release()', () => {
  assert.strictEqual(typeof R.oasisHwConsole.release, 'function');
});
