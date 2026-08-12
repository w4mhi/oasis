"""Unit tests for services/satellites/calibrate.py — the dongle frequency check
and the per-downlink RX verdict.

Unguarded: calibrate.py imports nothing optional (array is stdlib), so this runs
in the minimal CI job like doppler.py's tests.
"""
import os
import struct
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))

import calibrate  # noqa: E402


class MeasureArgvTest(unittest.TestCase):
    def test_correction_is_never_applied_while_measuring(self):
        """-p 0 is not an oversight. Applying a correction during the run would
        fold the answer into the question and we would read back zero forever."""
        argv = calibrate.measure_argv(162_550_000)
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "0")

    def test_it_is_time_bounded(self):
        argv = calibrate.measure_argv(162_550_000, seconds=8)
        self.assertEqual(argv[0], "timeout")
        self.assertGreater(int(argv[1]), 8)     # slack over the capture itself

    def test_the_dongle_is_pinned_by_serial(self):
        argv = calibrate.measure_argv(162_550_000, device_serial="00000042")
        self.assertIn("-d", argv)
        self.assertEqual(argv[argv.index("-d") + 1], "00000042")


class MeanSampleTest(unittest.TestCase):
    def test_mean_of_known_samples(self):
        raw = struct.pack("<4h", 100, -100, 300, -100)
        self.assertAlmostEqual(calibrate.mean_sample(raw), 50.0)

    def test_no_data_is_none_not_zero(self):
        """Zero is a legitimate reading — a perfectly on-frequency dongle. A
        failed capture must be distinguishable from a perfect one."""
        self.assertIsNone(calibrate.mean_sample(b""))
        self.assertIsNone(calibrate.mean_sample(b"\x01"))

    def test_an_odd_trailing_byte_is_ignored(self):
        raw = struct.pack("<2h", 10, 30) + b"\x7f"
        self.assertAlmostEqual(calibrate.mean_sample(raw), 20.0)


class SolveOffsetTest(unittest.TestCase):
    """The two-point solve. Assuming rtl_fm's internal scale instead measured
    ~4% wrong against hardware, and a scale error biases every ppm reading."""

    def test_it_recovers_a_known_scale_and_offset(self):
        scale, detune = 1.3653, 3000.0          # units per Hz
        true_offset = -500.0
        m_on = true_offset * scale
        m_det = (true_offset - detune) * scale  # tuning higher moves it lower
        got_scale, got_off = calibrate.solve_offset(m_on, m_det, detune)
        self.assertAlmostEqual(got_scale, scale, places=6)
        self.assertAlmostEqual(got_off, true_offset, places=6)

    def test_it_does_not_depend_on_the_scale_being_known(self):
        """Same offset, a wildly different scale — the answer must not move."""
        for scale in (0.5, 1.3653, 9.0):
            _, off = calibrate.solve_offset(-500 * scale, -3500 * scale, 3000)
            self.assertAlmostEqual(off, -500.0, places=6)

    def test_two_indistinguishable_runs_yield_nothing(self):
        """A detune that moved nothing means the measurement failed. Dividing by
        an almost-zero scale would turn noise into a confident wrong answer."""
        self.assertEqual(calibrate.solve_offset(100.0, 100.0, 3000), (None, None))

    def test_missing_runs_yield_nothing(self):
        self.assertEqual(calibrate.solve_offset(None, 100.0, 3000), (None, None))
        self.assertEqual(calibrate.solve_offset(100.0, None, 3000), (None, None))


class PpmTest(unittest.TestCase):
    def test_ppm_from_offset(self):
        # the NWR measurement from 2026-08-12
        self.assertAlmostEqual(
            calibrate.ppm_from_offset(-188.0, 162_550_000), -1.157, places=2)

    def test_offset_scales_linearly_with_carrier(self):
        """The whole reason a dongle can be fine on 2 m and useless on 70 cm."""
        self.assertAlmostEqual(calibrate.offset_at(10.0, 145_800_000), 1458.0)
        self.assertAlmostEqual(calibrate.offset_at(10.0, 435_575_000), 4355.75)

    def test_none_in_none_out(self):
        self.assertIsNone(calibrate.ppm_from_offset(None, 162e6))
        self.assertIsNone(calibrate.offset_at(None, 435e6))

    def test_leo_doppler_is_about_ten_kilohertz_at_70cm(self):
        self.assertAlmostEqual(calibrate.max_doppler_hz(435_575_000), 10_167, delta=200)
        self.assertAlmostEqual(calibrate.max_doppler_hz(145_800_000), 3_403, delta=100)


class VerdictTest(unittest.TestCase):
    def test_uncalibrated_is_grey_not_green(self):
        """Never say 'you are good' about something never measured."""
        v = calibrate.rx_verdict(None, 435_575_000, "usb")
        self.assertEqual(v["level"], "unknown")
        self.assertNotEqual(v["level"], "green")

    def test_unsupported_mode_is_not_a_warning(self):
        """The dropdown already disables modes we cannot demodulate — badging
        them too would complain about a choice never offered."""
        self.assertEqual(calibrate.rx_verdict(1.0, 435e6, None)["level"], "n/a")

    def test_fm_at_2m_shrugs_off_a_sloppy_dongle(self):
        """20 ppm at 145.8 MHz is 2.9 kHz — invisible in a 48 kHz window, which
        is why nobody noticed -p 0 for years."""
        v = calibrate.rx_verdict(20.0, 145_800_000, "fm")
        self.assertEqual(v["level"], "green")

    def test_the_same_dongle_fails_cw_at_70cm(self):
        """8.7 kHz of dongle error in a 6 kHz audio window. Judged with
        tracked=True so Doppler is out of the budget and the dongle term is the
        only thing on trial — otherwise Doppler dominates and this would pass
        for the wrong reason."""
        v = calibrate.rx_verdict(20.0, 435_575_000, "usb", tracked=True)
        self.assertEqual(v["level"], "amber")
        self.assertIn("dongle", v["reason"])

    def test_a_good_dongle_still_cannot_do_untracked_cw_at_70cm(self):
        """The finding that motivated the whole DSP project: even at 1 ppm,
        Doppler alone sweeps ~10 kHz through a 3.5 kHz window. Amber here is a
        true statement about the station, not a complaint about the dongle."""
        v = calibrate.rx_verdict(1.0, 435_575_000, "usb", tracked=False)
        self.assertEqual(v["level"], "amber")
        self.assertIn("Doppler", v["reason"])

    def test_tracked_capture_turns_that_green(self):
        """Same dongle, same bird, correction on — this is what phases 3-5 buy,
        expressed as something the operator can see."""
        v = calibrate.rx_verdict(1.0, 435_575_000, "usb", tracked=True)
        self.assertEqual(v["level"], "green")
        self.assertEqual(v["doppler_hz"], 0.0)

    def test_the_reason_names_the_dominant_term(self):
        """Dongle error and Doppler have completely different fixes — one is
        `-p`, the other is the tracked capture path. Saying which dominates is
        the difference between an actionable warning and a shrug."""
        dongle = calibrate.rx_verdict(60.0, 435_575_000, "usb", tracked=True)
        self.assertEqual(dongle["level"], "amber")
        self.assertIn("dongle", dongle["reason"])
        dop = calibrate.rx_verdict(0.5, 435_575_000, "usb", tracked=False)
        self.assertIn("Doppler", dop["reason"])

    def test_the_budget_is_reported_so_the_ui_can_explain_itself(self):
        v = calibrate.rx_verdict(2.0, 435_575_000, "usb")
        self.assertAlmostEqual(v["offset_hz"], 871.15, places=1)
        self.assertGreater(v["doppler_hz"], 9000)
        self.assertAlmostEqual(v["budget_hz"], v["offset_hz"] + v["doppler_hz"])
        self.assertEqual(v["tolerance_hz"], calibrate.TOLERANCE_HZ["usb"])

    def test_sign_of_the_error_does_not_matter(self):
        for ppm in (7.0, -7.0):
            self.assertEqual(calibrate.rx_verdict(ppm, 145_800_000, "fm")["level"],
                             "green")


if __name__ == "__main__":
    unittest.main()


class DongleVerdictTest(unittest.TestCase):
    """Separate from rx_verdict on purpose: "is my hardware sound" is a
    different question from "can I work this bird", and only the second one
    involves Doppler."""

    def test_unmeasured_is_unknown(self):
        self.assertEqual(calibrate.dongle_verdict(None)["level"], "unknown")

    def test_a_good_dongle_reports_its_deviation(self):
        v = calibrate.dongle_verdict(-2.98)
        self.assertEqual(v["level"], "green")
        self.assertIn("-2.98 ppm", v["text"])

    def test_judged_at_70cm_narrowband_the_hardest_case(self):
        """8 ppm is 3.5 kHz at 435 MHz — right at the SSB window. A dongle that
        passes at the tightest case passes everywhere OASIS captures."""
        self.assertEqual(calibrate.dongle_verdict(7.0)["level"], "green")
        self.assertEqual(calibrate.dongle_verdict(20.0)["level"], "amber")

    def test_the_amber_text_says_what_it_costs(self):
        v = calibrate.dongle_verdict(20.0)
        self.assertIn("kHz at 70 cm", v["text"])
        self.assertIn("narrowband", v["text"])

    def test_sign_does_not_change_the_verdict(self):
        self.assertEqual(calibrate.dongle_verdict(20.0)["level"],
                         calibrate.dongle_verdict(-20.0)["level"])
