'use strict';
// Unit tests for satdenoise.filterFor — slider position to filter corners.
//
// The corners are not taste. They come from measuring the PCSAT APRS capture of
// 2026-08-13 (144.390 FM, tracked/csdr, 142 s), and the measurement says
// something other than the obvious:
//
//   band        burst    dead air     the useful fact
//   0- 300 Hz    7.9 dB    16.9 dB    dead air is 9.0 dB LOUDER than signal
//   300- 900     17.0      23.3       dead air 6.3 dB louder
//   900-1400     28.7      22.3       signal 6.4 dB louder  <- the mark tone
//   1400-2000    27.3      20.4       signal 6.9 dB louder  <- the space tone
//   2600-3400     7.9      13.4       dead air louder again
//   3400+        ~0.1% of all power   nothing up here at all
//
// So the noise is BELOW the packet band, not above it: the chain's own rolloff
// (-40 dB/octave past 2.6 kHz) already removed the high hiss, and the AGC lifts
// low-frequency rumble between packets. The filter that helps is therefore a
// HIGH-pass sweeping up toward the tones, and the low-pass is nearly free.
//
// Measured net gain, signal power kept vs dead-air power kept:
//   300-3400 Hz -> +1.0 dB    900-2600 Hz -> +3.5 dB    1000-2500 Hz -> +4.0 dB
//
// Those are BRICK-WALL figures. Re-measured through the real second-order
// biquad (Q=1) on this capture and on LilacSat-2 APRS of 2026-08-14, full
// deflection is worth +2.9 dB and +2.3 dB respectively -- the skirts pass noise
// outside the corner and shave signal inside it. The corners are unchanged
// because the biquad model puts the optimum in the same place; only the size of
// the prize was overstated.
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const d = require(path.join(__dirname, '..', '..', 'services', 'satellites',
                            'static', 'sat-denoise.js'));

test('zero is a true bypass, not a very gentle filter', () => {
  // It must be possible to hear exactly what was recorded. A filter that is
  // always in circuit cannot be A/B'd against the raw audio, and then nobody
  // can tell whether it is helping.
  const f = d.filterFor(0);
  assert.strictEqual(f.bypass, true);
});

test('any positive amount engages the filter', () => {
  for (const a of [1, 25, 50, 100]) assert.strictEqual(d.filterFor(a).bypass, false, String(a));
});

test('the high-pass climbs toward the mark tone but never reaches it', () => {
  // 1200 Hz is the mark tone. A high-pass at or above it eats the signal it is
  // meant to reveal, so the sweep has to stop short with margin.
  const lo = d.filterFor(1).highpassHz, hi = d.filterFor(100).highpassHz;
  assert.ok(lo < hi, 'the sweep must climb');
  assert.ok(hi < 1100, `high-pass tops out below the mark tone, got ${hi}`);
  // The faintest engaged setting sits essentially at HP_MIN -- one step of a
  // 100-point sweep above it, not a different regime.
  assert.ok(lo <= d.HP_MIN + 10, `gentlest setting starts at ~${d.HP_MIN} Hz, got ${lo}`);
});

test('the low-pass keeps the space tone at every setting', () => {
  // 2200 Hz is the space tone. Losing it destroys mark/space discrimination,
  // which is the one thing a packet decoder cannot do without.
  for (const a of [1, 20, 50, 80, 100]) {
    assert.ok(d.filterFor(a).lowpassHz > 2300,
              `low-pass must stay clear of 2200 Hz at ${a}, got ${d.filterFor(a).lowpassHz}`);
  }
});

test('the passband never inverts', () => {
  for (let a = 0; a <= 100; a += 5) {
    const f = d.filterFor(a);
    if (f.bypass) continue;
    assert.ok(f.highpassHz < f.lowpassHz, `inverted passband at ${a}`);
  }
});

test('both corners move monotonically', () => {
  let prevHp = -1, prevLp = Infinity;
  for (let a = 1; a <= 100; a += 1) {
    const f = d.filterFor(a);
    assert.ok(f.highpassHz >= prevHp, `high-pass went backwards at ${a}`);
    assert.ok(f.lowpassHz <= prevLp, `low-pass went backwards at ${a}`);
    prevHp = f.highpassHz; prevLp = f.lowpassHz;
  }
});

test('full deflection lands on the measured best corners', () => {
  // 1000-2500 Hz measured +4.0 dB net on the PCSAT capture -- the best of the
  // three candidates while still keeping 95.5% of burst power.
  const f = d.filterFor(100);
  assert.ok(Math.abs(f.highpassHz - 1000) <= 60, `got ${f.highpassHz}`);
  assert.ok(Math.abs(f.lowpassHz - 2500) <= 150, `got ${f.lowpassHz}`);
});

test('out-of-range and junk amounts are clamped, never thrown', () => {
  assert.strictEqual(d.filterFor(-10).bypass, true);
  assert.strictEqual(d.filterFor(999).highpassHz, d.filterFor(100).highpassHz);
  assert.strictEqual(d.filterFor(NaN).bypass, true);
  assert.strictEqual(d.filterFor(null).bypass, true);
  assert.strictEqual(d.filterFor(undefined).bypass, true);
  assert.strictEqual(d.filterFor('50').highpassHz, d.filterFor(50).highpassHz);
});

test('the label says what the filter is doing, in Hz', () => {
  // The operator is judging a weak signal by ear. "3" tells them nothing; the
  // corners tell them whether the thing they stopped hearing was the space tone.
  assert.match(d.labelFor(0), /off/i);
  assert.match(d.labelFor(100), /1000/);
  assert.match(d.labelFor(100), /2500/);
});
