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

test('cstate: per-device eligible list overrides the kind rule', () => {
  // A DRAWS channel-1 port is a valid 'draws' kind for winlink but an
  // impossible target (pat cannot use AGW port 1), so it must read 'na'.
  const svc = { id: 'winlink', kinds: ['draws'] };
  const right = { id: 'draws-right', kind: 'draws', eligible: ['aprs'] };
  const left = { id: 'draws-left', kind: 'draws', eligible: ['aprs', 'winlink'] };
  assert.equal(R.oasisCstate(right, svc), 'na');
  assert.equal(R.oasisCstate(left, svc), 'off');
});

test('cstate: falls back to the kind rule when eligible is absent', () => {
  const svc = { id: 'winlink', kinds: ['dra-pi'] };
  assert.equal(R.oasisCstate({ id: 'd', kind: 'dra-pi' }, svc), 'off');
  assert.equal(R.oasisCstate({ id: 'r', kind: 'rtl-sdr' }, svc), 'na');
});

test('cstate: eligible still yields on/stopped for an assigned device', () => {
  const svc = { id: 'winlink', kinds: ['draws'] };
  const dev = { id: 'draws-left', kind: 'draws', eligible: ['winlink'], assigned: 'winlink' };
  assert.equal(R.oasisCstate({ ...dev, running: true }, svc), 'on');
  assert.equal(R.oasisCstate({ ...dev, running: false }, svc), 'stopped');
});
