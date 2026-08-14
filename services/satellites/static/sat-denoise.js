// Slider position -> audio filter corners, for the live listen path only.
// UMD: browser (window.satdenoise) and node (module.exports) for tests.
//
// WHERE THE NUMBERS COME FROM
// ---------------------------
// The PCSAT APRS capture of 2026-08-13 (144.390 FM, tracked/csdr, 142 s), not a
// textbook. Measured per-band, burst frames against dead air:
//
//     0- 300 Hz   dead air 9.0 dB LOUDER than the signal
//   300- 900 Hz   dead air 6.3 dB louder
//   900-1400 Hz   signal 6.4 dB louder   <- Bell 202 mark, 1200 Hz
//  1400-2000 Hz   signal 6.9 dB louder   <- Bell 202 space, 2200 Hz
//  2600-3400 Hz   dead air louder again
//  3400 Hz +      0.1% of total power; nothing is up there
//
// The surprise, and the reason this is a high-pass rather than the low-pass
// everyone reaches for: THE NOISE IS BELOW THE SIGNAL, NOT ABOVE IT. The chain
// already falls off a cliff past 2.6 kHz (-40 dB/octave measured on dead air),
// so there is no high hiss left to remove. What you actually hear between
// packets is low-frequency rumble that the AGC lifts while nothing is being
// received. Sweeping a high-pass up toward the mark tone is what removes it.
//
// Measured net gain -- signal power kept against dead-air power kept. Two
// columns, because the first one flatters the filter and it is the second that
// the operator actually hears:
//
//   corners        brick-wall   real biquad (Q=1, what runs here)
//   300-3400 Hz      +1.0 dB      -
//   825-2875 Hz      +2.9 dB      +2.4 dB
//   1000-2500 Hz     +4.0 dB      +2.9 dB      <- full deflection
//
// Selecting a band in an FFT is not the same as running a second-order biquad
// over it: the biquad's skirts let noise through either side of the corner and
// shave signal just inside it. Expect ~2-3 dB, not ~4.
//
// Confirmed against a second capture (LilacSat-2 APRS, 2026-08-14) which agrees
// within half a dB throughout, so these corners are not tuned to one pass.
//
// THE RANGE STOPS WHERE IT DOES BECAUSE THE CURVE IS FLAT THERE, not out of
// caution. Modelled past full deflection on both captures: 1100-2400 buys
// +0.05 dB, 1000-2300 buys +0.14, and 1200-2400 is WORSE (-0.09) as the
// high-pass starts eating the 1200 Hz mark tone. There is nothing left to win by
// widening the sweep, so it is not widened.
//
// WHY THIS IS THE LISTEN PATH AND NOTHING ELSE
// --------------------------------------------
// It runs in the BROWSER, on the <audio> element, downstream of everything.
// capture.py's sinks are not equal: the WAV must never lose a sample, a listener
// may. Filtering here inherits that for free — the recording on disk stays the
// raw article, which matters most for packet, where a decoder wants the tones
// as they arrived and not as they sounded nice. It also costs the Pi nothing,
// which a chain-side filter would not.
//
// THE TWO CORNERS ARE NOT SYMMETRIC IN RISK. Losing the 2200 Hz space tone
// destroys mark/space discrimination outright, so the low-pass stops well clear
// of it; the high-pass may crowd 1200 Hz more closely because everything under
// it is measurably noise.
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.satdenoise = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const HP_MIN = 300, HP_MAX = 1000;      // sweep toward, never onto, the mark tone
  const LP_MAX = 4000, LP_MIN = 2500;     // never near the space tone at 2200 Hz

  function _amount(a) {
    const n = typeof a === 'string' ? parseFloat(a) : a;
    if (typeof n !== 'number' || !isFinite(n)) return 0;
    return Math.max(0, Math.min(100, n));
  }

  // Corners for a slider position 0-100. 0 is a real bypass: the operator has to
  // be able to hear exactly what was recorded, or there is no way to tell
  // whether the filter is helping.
  function filterFor(amount) {
    const a = _amount(amount);
    if (a <= 0) return { bypass: true, highpassHz: 0, lowpassHz: 0, amount: 0 };
    const t = a / 100;
    return {
      bypass: false,
      highpassHz: Math.round(HP_MIN + (HP_MAX - HP_MIN) * t),
      lowpassHz: Math.round(LP_MAX - (LP_MAX - LP_MIN) * t),
      amount: a,
    };
  }

  // Hz, not a number on a scale of five. The operator is judging a weak signal
  // by ear and needs to know whether what just went quiet was the space tone.
  function labelFor(amount) {
    const f = filterFor(amount);
    return f.bypass ? 'off (raw audio)' : `${f.highpassHz}-${f.lowpassHz} Hz`;
  }

  return { filterFor, labelFor, HP_MIN, HP_MAX, LP_MIN, LP_MAX };
});
