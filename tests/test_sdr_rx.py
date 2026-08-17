import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import sdr_rx  # noqa: E402


class _Inv:
    """Minimal stand-in for common.hardware inventory."""
    def __init__(self, assignments, devices=None):
        self.assignments = assignments
        self.devices = devices or {}


class SdrRxTest(unittest.TestCase):
    def test_mhz_to_hz(self):
        self.assertEqual(sdr_rx.mhz_to_hz(162.550), 162550000)
        self.assertEqual(sdr_rx.mhz_to_hz("137.100"), 137100000)

    def test_missing_deps_reports_in_order(self):
        self.assertEqual(
            sdr_rx.missing_deps(("rtl_fm", "multimon-ng"), which=lambda b: None),
            ["rtl_fm", "multimon-ng"])
        self.assertEqual(
            sdr_rx.missing_deps(("rtl_fm", "multimon-ng"),
                                which=lambda b: "/usr/bin/" + b),
            [])

    def test_stream_encoder_prefers_ffmpeg(self):
        cmd, mime = sdr_rx.stream_encoder(22050, which=lambda b: "/usr/bin/" + b)
        self.assertIn("ffmpeg", cmd)
        self.assertIn("-ar 22050", cmd)
        self.assertEqual(mime, "audio/mpeg")

    def test_stream_encoder_falls_back_to_sox(self):
        which = lambda b: "/usr/bin/sox" if b == "sox" else None  # noqa: E731
        cmd, mime = sdr_rx.stream_encoder(22050, which=which)
        self.assertIn("sox", cmd)
        self.assertEqual(mime, "audio/mpeg")

    def test_stream_encoder_none_when_neither(self):
        self.assertEqual(sdr_rx.stream_encoder(22050, which=lambda b: None),
                         (None, None))

    def test_dongle_busy_ignores_our_own_service(self):
        # nwr is assigned rtl-1 and is the ONLY assignee -> never busy.
        inv = _Inv({"nwr": "rtl-1"})
        busy, holder = sdr_rx.dongle_busy(inv, lambda u: True, "nwr")
        self.assertFalse(busy)
        self.assertIsNone(holder)

    def test_dongle_busy_reports_the_other_holder(self):
        # adsb co-assigned to the same dongle, and its unit is active.
        inv = _Inv({"nwr": "rtl-1", "adsb": "rtl-1"})
        busy, holder = sdr_rx.dongle_busy(
            inv, lambda u: u == "dump1090-fa", "nwr")
        self.assertTrue(busy)
        self.assertEqual(holder, "adsb")

    def test_dongle_busy_falls_back_when_unassigned(self):
        # No assignment -> global SDR-consumer check.
        inv = _Inv({})
        busy, holder = sdr_rx.dongle_busy(
            inv, lambda u: u == "dump1090-fa", "nwr")
        self.assertTrue(busy)
        self.assertEqual(holder, "dump1090-fa")


if __name__ == "__main__":
    unittest.main()
