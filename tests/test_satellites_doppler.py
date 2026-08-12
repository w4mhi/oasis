"""Unit tests for services/satellites/doppler.py — the pure capture arithmetic.

Deliberately UNGUARDED: doppler.py imports nothing optional, so unlike the
predict tests these must run everywhere, including the minimal CI server-setup
job that installs only flask/gunicorn/psutil. If an import guard ever becomes
necessary here, something has leaked into a module whose whole point is that
nothing has.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))

import doppler  # noqa: E402  — pure module, no optional deps


class ConstantsTest(unittest.TestCase):
    """The two numbers the whole chain is built on. A test rather than a comment
    because both were chosen for reasons that are invisible at the call site."""

    def test_input_rate_divides_the_rtl_clock_exactly(self):
        """28.8 MHz / 240000 = 120. A non-integer ratio engages the RTL2832U's
        fractional divider, which is where 'some sample rates are unstable' comes
        from — 250000 would give 115.2."""
        self.assertEqual(28_800_000 % doppler.INPUT_RATE_HZ, 0)

    def test_input_rate_divides_into_both_output_rates(self):
        """48 kHz for FM and 12 kHz for SSB/CW, the rates listen.demod_params
        already uses. Both integer, so the chain decimates by FirDecimate alone
        and never needs a FractionalDecimator."""
        self.assertEqual(doppler.INPUT_RATE_HZ % 48_000, 0)
        self.assertEqual(doppler.INPUT_RATE_HZ % 12_000, 0)
        self.assertEqual(doppler.INPUT_RATE_HZ // 48_000, 5)
        self.assertEqual(doppler.INPUT_RATE_HZ // 12_000, 20)

    def test_the_dc_spike_falls_outside_the_first_decimated_passband(self):
        """The reason the dongle parks off-target at all: after the first /5 the
        passband is +/-24 kHz around the shifted carrier, so an LFO offset larger
        than that puts the RTL's DC artefact where the decimation FIR removes
        it."""
        self.assertGreater(doppler.LFO_OFFSET_HZ, doppler.INPUT_RATE_HZ / 5 / 2)


class GeometryTest(unittest.TestCase):
    def test_the_dongle_parks_above_the_downlink(self):
        self.assertEqual(doppler.centre_hz(437_800_000), 437_848_000)

    def test_on_frequency_the_signal_sits_at_minus_the_offset(self):
        """Zero Doppler (closest approach) is not zero baseband — the whole point
        of the LFO is that the signal is never at the centre."""
        self.assertEqual(doppler.baseband_offset_hz(0), -doppler.LFO_OFFSET_HZ)

    def test_nominal_shift_rate_is_exactly_a_fifth(self):
        """48000 / 240000. Chosen to be inspectable: a rate that is not ~0.2 at
        closest approach means the offset and the input rate disagree."""
        self.assertAlmostEqual(doppler.shift_rate(0), 0.2, places=12)

    def test_the_shift_rate_swings_the_expected_way_across_a_pass(self):
        """~+/-10 kHz at 70 cm, against a 48 kHz offset in a 240 kHz window."""
        approaching = doppler.shift_rate(+10_200)     # bird inbound, shifted up
        receding = doppler.shift_rate(-10_200)        # outbound, shifted down
        self.assertAlmostEqual(approaching, (48_000 - 10_200) / 240_000, places=12)
        self.assertAlmostEqual(receding, (48_000 + 10_200) / 240_000, places=12)
        self.assertLess(approaching, 0.2)
        self.assertGreater(receding, 0.2)

    def test_a_whole_leo_pass_stays_far_inside_nyquist(self):
        for dop in range(-11_000, 11_001, 500):
            rate = doppler.shift_rate(dop)
            self.assertTrue(doppler.shift_rate_in_range(rate), f"{dop} Hz -> {rate}")
            self.assertLess(abs(rate), 0.25, "should sit near 0.2, nowhere near 0.5")

    def test_an_absurd_offset_is_reported_out_of_range_not_clamped(self):
        """A rate past Nyquist means the configuration is wrong, and clamping it
        would produce audio that demodulates but is quietly off-frequency."""
        self.assertFalse(doppler.shift_rate_in_range(
            doppler.shift_rate(0, lfo_offset_hz=200_000)))

    def test_shift_hz_is_the_factor_applied_to_the_armed_carrier(self):
        factor = 1e-5
        self.assertAlmostEqual(doppler.shift_hz(factor, 145.8e6), 1458.0, places=9)
        self.assertAlmostEqual(doppler.shift_hz(factor, 437.8e6), 4378.0, places=9)


class CurveAtTest(unittest.TestCase):
    # 1 s steps, factor falling linearly through zero — a stand-in for a pass.
    CURVE = [(0, 2e-5), (1, 1e-5), (2, 0.0), (3, -1e-5), (4, -2e-5)]

    def test_no_curve_is_none_not_zero(self):
        """Zero Doppler is a real reading that happens at every closest approach.
        A caller with no curve must fall back to the uncorrected path, and a
        plausible 0.0 would hide that it never had a choice."""
        self.assertIsNone(doppler.curve_at([], 1.0))
        self.assertIsNone(doppler.curve_at(None, 1.0))

    def test_samples_are_returned_exactly(self):
        for t, f in self.CURVE:
            self.assertAlmostEqual(doppler.curve_at(self.CURVE, t), f, places=12)

    def test_between_samples_it_interpolates(self):
        self.assertAlmostEqual(doppler.curve_at(self.CURVE, 0.5), 1.5e-5, places=12)
        self.assertAlmostEqual(doppler.curve_at(self.CURVE, 2.25), -0.25e-5, places=12)

    def test_a_capture_that_outlives_its_curve_holds_the_last_value(self):
        """Clamping beats failing mid-pass: the alternative to a slightly stale
        correction is no correction at all."""
        self.assertEqual(doppler.curve_at(self.CURVE, 99.0), -2e-5)
        self.assertEqual(doppler.curve_at(self.CURVE, -99.0), 2e-5)

    def test_a_single_sample_curve_is_a_constant(self):
        self.assertEqual(doppler.curve_at([(0, 3e-5)], 7.5), 3e-5)

    def test_interpolation_residual_at_the_worst_real_doppler_rate(self):
        """The reason the curve is sampled at 1 s and the tracker ticks at 10 Hz.

        Doppler rate peaks near ~215 Hz/s at 437 MHz. Model a pass whose shift
        sweeps at that rate and check that reading the 1 s curve at 10 Hz stays
        within the ~21 Hz step that makes the correction inaudible."""
        carrier = 437.8e6
        rate_hz_per_s = 215.0
        # A curve whose factor changes at exactly that rate, in 1 s steps.
        curve = [(i, (i * rate_hz_per_s) / carrier) for i in range(21)]
        worst = 0.0
        t = 0.0
        while t <= 20.0:
            got = doppler.shift_hz(doppler.curve_at(curve, t), carrier)
            worst = max(worst, abs(got - t * rate_hz_per_s))
            t += 1.0 / doppler.TICK_HZ
        # Linear ramp against linear interpolation: the residual is the tick
        # quantisation alone, which is what the 10 Hz choice was sized for.
        self.assertLess(worst, 25.0, f"worst residual {worst:.1f} Hz")


class TickTest(unittest.TestCase):
    CURVE = [(0, 1e-5), (10, -1e-5)]

    def test_a_tick_is_the_whole_chain_from_clock_to_nco(self):
        rate = doppler.tick_shift_rate(self.CURVE, 0.0, 437.8e6)
        expected = (doppler.LFO_OFFSET_HZ - 1e-5 * 437.8e6) / doppler.INPUT_RATE_HZ
        self.assertAlmostEqual(rate, expected, places=12)

    def test_no_curve_yields_no_rate(self):
        self.assertIsNone(doppler.tick_shift_rate([], 0.0, 437.8e6))

    def test_the_carrier_is_applied_at_the_tick_not_baked_into_the_curve(self):
        """One curve, two bands: the same orbital sample must serve whichever
        downlink was armed. This is the bug that made /track's doppler_hz wrong,
        expressed on the capture side."""
        vhf = doppler.tick_shift_rate(self.CURVE, 0.0, 145.8e6)
        uhf = doppler.tick_shift_rate(self.CURVE, 0.0, 437.8e6)
        self.assertNotAlmostEqual(vhf, uhf, places=6)
        # Both are 0.2 minus their own doppler contribution.
        self.assertAlmostEqual((0.2 - uhf) / (0.2 - vhf), 437.8 / 145.8, places=6)

    def test_every_tick_of_a_realistic_pass_is_within_nyquist(self):
        curve = [(i, (7.0 / doppler.C_KM_S) * (1 - i / 300.0)) for i in range(601)]
        t = 0.0
        while t <= 600.0:
            rate = doppler.tick_shift_rate(curve, t, 437.8e6)
            self.assertTrue(doppler.shift_rate_in_range(rate), f"t={t} rate={rate}")
            t += 1.0 / doppler.TICK_HZ


if __name__ == "__main__":
    unittest.main()
