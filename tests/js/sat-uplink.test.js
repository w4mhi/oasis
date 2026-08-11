'use strict';
// Unit tests for common/js/sat-uplink.js — the uplink display lines on the
// satellite detail popup.
//
// The rule under test is attribution: an uplink belongs to a transmitter, and a
// grouped entry can hold three modes with only one of them transmittable. A line
// that says "437.800 FM · SSTV · FSK ↑ 145.990" without naming FM tells the
// operator they can transmit to the SSTV service, which they cannot.
const test = require('node:test');
const assert = require('node:assert');
const U = require('../../common/js/sat-uplink.js');

test('an entry with no uplink produces no lines', () => {
  assert.deepStrictEqual(U.uplinkLines({ freq_mhz: 137.1, modes: ['LRPT'], uplinks: [] }), []);
});

test('a missing uplinks key is treated as none, not as a throw', () => {
  assert.deepStrictEqual(U.uplinkLines({ freq_mhz: 137.1, modes: ['LRPT'] }), []);
});

test('a single-mode entry does not name its own mode twice', () => {
  const lines = U.uplinkLines({
    freq_mhz: 145.8, modes: ['FM'],
    uplinks: [{ freq_mhz: 144.49, freq_high_mhz: null, invert: false,
                ctcss_hz: null, simplex: false, mode: 'FM' }] });
  assert.deepStrictEqual(lines, ['↑ 144.49']);
});

test('a multi-mode entry names the mode the uplink belongs to', () => {
  const lines = U.uplinkLines({
    freq_mhz: 437.8, modes: ['FM', 'FSK', 'SSTV'],
    uplinks: [{ freq_mhz: 145.99, freq_high_mhz: null, invert: false,
                ctcss_hz: 67.0, simplex: false, mode: 'FM' }] });
  assert.deepStrictEqual(lines, ['↑ 145.99 FM · CTCSS 67.0']);
});

test('a linear passband renders as a range and says it inverts', () => {
  const lines = U.uplinkLines({
    freq_mhz: 145.925, modes: ['USB'],
    uplinks: [{ freq_mhz: 432.125, freq_high_mhz: 432.175, invert: true,
                ctcss_hz: null, simplex: false, mode: 'USB' }] });
  assert.deepStrictEqual(lines, ['↑ 432.125–432.175 inverting']);
});

test('uplink equal to downlink reads as simplex, not as two legs', () => {
  const lines = U.uplinkLines({
    freq_mhz: 145.825, modes: ['APRS'],
    uplinks: [{ freq_mhz: 145.825, freq_high_mhz: null, invert: false,
                ctcss_hz: null, simplex: true, mode: 'APRS' }] });
  assert.deepStrictEqual(lines, ['↑↓ 145.825 simplex']);
});

test('several uplink-bearing members get one line each', () => {
  const lines = U.uplinkLines({
    freq_mhz: 145.8, modes: ['FM', 'SSTV'],
    uplinks: [
      { freq_mhz: 144.49, freq_high_mhz: null, invert: false, ctcss_hz: null, simplex: false, mode: 'FM' },
      { freq_mhz: 145.20, freq_high_mhz: null, invert: false, ctcss_hz: null, simplex: false, mode: 'FM' }] });
  assert.strictEqual(lines.length, 2);
});

test('no line uses a codepoint above U+FFFF', () => {
  // The Pi ships no emoji font. Arrows must stay BMP or they render as tofu.
  const lines = U.uplinkLines({
    freq_mhz: 145.825, modes: ['APRS'],
    uplinks: [{ freq_mhz: 145.825, freq_high_mhz: null, invert: false,
                ctcss_hz: null, simplex: true, mode: 'APRS' }] });
  for (const ch of lines.join('')) assert.ok(ch.codePointAt(0) <= 0xFFFF, ch);
});
