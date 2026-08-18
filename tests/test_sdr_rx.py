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

    def test_stderr_summary_names_a_busy_dongle(self):
        self.assertEqual(
            sdr_rx.stderr_summary("usb_claim_interface error -6\n"),
            "another service is already using the RTL-SDR dongle")

    def test_stderr_summary_falls_back_to_the_last_line(self):
        self.assertEqual(
            sdr_rx.stderr_summary("first\nsecond\nno device found\n"),
            "no device found")

    def test_stderr_summary_of_nothing_says_so(self):
        self.assertIn("no output", sdr_rx.stderr_summary(""))
        self.assertIn("no output", sdr_rx.stderr_summary(None))


class _FakeRtlTestResult:
    def __init__(self, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr


_ONE_DEVICE = ("Found 1 device(s):\n"
              "  0:  Realtek, RTL2838UHIDIR, SN: 00000001\n")
_NO_DEVICE = "No supported devices found.\n"


class DonglePresentCacheTest(unittest.TestCase):
    """Finding 6: /api/nwr/status polls every 5 s and both dashboards' health
    checks call this too -- each dongle_present() call shells out to
    `rtl_test -t` with a 6 s timeout, which can outlast index.html's own 4 s
    health-check budget and paint the chip TIMEOUT/DOWN even though the
    dongle is fine. Caching removes the repeated shell-outs; these tests
    exercise the cache through its `now` injection point so they don't
    depend on real wall-clock time."""

    def setUp(self):
        sdr_rx._reset_presence_cache()

    def tearDown(self):
        sdr_rx._reset_presence_cache()

    def test_a_second_call_inside_the_window_does_not_reshell_out(self):
        calls = []

        def run(*a, **k):
            calls.append(1)
            return _FakeRtlTestResult(stdout=_ONE_DEVICE)

        first = sdr_rx.dongle_present(run=run, now=1000.0)
        second = sdr_rx.dongle_present(run=run, now=1010.0)   # +10s, inside 30s
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(len(calls), 1,
                         "a cached answer must not re-invoke rtl_test")

    def test_the_cache_expires_after_the_window(self):
        calls = []

        def run(*a, **k):
            calls.append(1)
            return _FakeRtlTestResult(stdout=_ONE_DEVICE)

        sdr_rx.dongle_present(run=run, now=1000.0)
        sdr_rx.dongle_present(run=run, now=1031.0)   # +31s, past 30s
        self.assertEqual(len(calls), 2,
                         "a stale answer must not survive past the cache window")

    def test_an_unplug_is_seen_again_within_one_window(self):
        # A cache that never re-probed would mask a real unplug forever --
        # the whole point of a TTL is that "still cached" and "still true"
        # cannot silently become the same thing.
        answers = iter([_ONE_DEVICE, _NO_DEVICE])

        def run(*a, **k):
            return _FakeRtlTestResult(stdout=next(answers))

        first = sdr_rx.dongle_present(run=run, now=2000.0)
        second = sdr_rx.dongle_present(run=run, now=2031.0)
        self.assertTrue(first)
        self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
