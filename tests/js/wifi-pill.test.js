'use strict';
// Unit tests for the shared Wi-Fi pill logic used by index.html + the kiosk.
const test = require('node:test');
const assert = require('node:assert');
const W = require('../../common/js/wifi-pill.js');

test('tone: signal thresholds degrade green → amber → red', () => {
  assert.strictEqual(W.oasisWifiTone(100), 'strong');
  assert.strictEqual(W.oasisWifiTone(66), 'strong');
  assert.strictEqual(W.oasisWifiTone(65), 'med');
  assert.strictEqual(W.oasisWifiTone(33), 'med');
  assert.strictEqual(W.oasisWifiTone(32), 'weak');
  assert.strictEqual(W.oasisWifiTone(0), 'weak');
  assert.strictEqual(W.oasisWifiTone(null), 'unknown');
  assert.strictEqual(W.oasisWifiTone(undefined), 'unknown');
});

test('pill: AP mode → OASIS in blue', () => {
  assert.deepStrictEqual(W.oasisWifiPill({ supported: true, mode: 'ap' }, null),
                         { text: 'OASIS', tone: 'ap' });
});

test('pill: client mode → SSID coloured by signal (no number in text)', () => {
  assert.deepStrictEqual(W.oasisWifiPill({ supported: true, mode: 'client', ssid: 'MH-500' }, 80),
                         { text: 'MH-500', tone: 'strong' });
  assert.deepStrictEqual(W.oasisWifiPill({ supported: true, mode: 'client', ssid: 'MH-500' }, 45),
                         { text: 'MH-500', tone: 'med' });
  assert.deepStrictEqual(W.oasisWifiPill({ supported: true, mode: 'client', ssid: 'MH-500' }, 12),
                         { text: 'MH-500', tone: 'weak' });
  // signal unavailable → still show the SSID, neutral tone
  assert.deepStrictEqual(W.oasisWifiPill({ supported: true, mode: 'client', ssid: 'MH-500' }, null),
                         { text: 'MH-500', tone: 'unknown' });
});

test('signal: prefers the in_use row', () => {
  const scan = { ok: true, networks: [
    { ssid: 'A', signal: 50, in_use: false },
    { ssid: 'MH-500', signal: 85, in_use: true },
  ] };
  assert.strictEqual(W.oasisWifiSignal(scan, 'MH-500'), 85);
});

test('signal: falls back to SSID match when nmcli tags no in_use row', () => {
  // The real bug: connected to MH-500 but every row is in_use:false.
  const scan = { ok: true, networks: [
    { ssid: 'MH-070', signal: 90, in_use: false },
    { ssid: 'MH-500', signal: 85, in_use: false },
  ] };
  assert.strictEqual(W.oasisWifiSignal(scan, 'MH-500'), 85, 'matches by SSID → no gray flicker');
});

test('signal: null when scan failed or SSID absent', () => {
  assert.strictEqual(W.oasisWifiSignal({ ok: false }, 'MH-500'), null);
  assert.strictEqual(W.oasisWifiSignal(null, 'MH-500'), null);
  assert.strictEqual(W.oasisWifiSignal({ ok: true, networks: [{ ssid: 'X', signal: 40, in_use: false }] }, 'MH-500'), null);
});

test('pill: not connected → Wi-Fi / none', () => {
  assert.deepStrictEqual(W.oasisWifiPill({ supported: true, mode: 'none' }, null),
                         { text: 'Wi-Fi', tone: 'none' });
});

test('pill: unsupported (non-Linux / helper absent) → null (hide)', () => {
  assert.strictEqual(W.oasisWifiPill({ supported: false }, null), null);
  assert.strictEqual(W.oasisWifiPill(null, null), null);
});
