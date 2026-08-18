import os
import sys
import unittest
from unittest import mock

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


class GainFlagTest(unittest.TestCase):
    """Finding: rtl_fm parses -g with atof(), which has no "auto" keyword --
    atof("auto") is 0.0, so `-g auto` silently ran the dongle at 0 dB tuner
    gain instead of rtl_fm's real automatic gain (which is what you get by
    omitting -g entirely). Shared by listener.rtl_command() and
    scan.scan_command(), which both hit this the same way."""

    def test_auto_omits_the_flag(self):
        self.assertEqual(sdr_rx.gain_flag("auto"), [])

    def test_blank_omits_the_flag(self):
        self.assertEqual(sdr_rx.gain_flag(""), [])

    def test_none_omits_the_flag(self):
        self.assertEqual(sdr_rx.gain_flag(None), [])

    def test_numeric_gain_emits_the_flag(self):
        self.assertEqual(sdr_rx.gain_flag("40"), ["-g", "40"])

    def test_numeric_gain_survives_non_string_input(self):
        self.assertEqual(sdr_rx.gain_flag(40), ["-g", "40"])


class DonglePresentCacheTest(unittest.TestCase):
    """Finding 6: /api/nwr/status polls every 5 s and both dashboards' health
    checks call this too -- each dongle_present() call shells out to
    `rtl_test -t` with a 6 s timeout, which can outlast index.html's own 4 s
    health-check budget and paint the chip TIMEOUT/DOWN even though the
    dongle is fine. Caching removes the repeated shell-outs for those pollers.

    These tests patch subprocess.run rather than passing `run=`, because the
    cache is deliberately never written for an injected runner (see the
    poisoning test below) -- so the polled path is the only one that can be
    tested at all, which is the point. The `now` injection keeps them off the
    wall clock."""

    def setUp(self):
        sdr_rx._reset_presence_cache()

    def tearDown(self):
        sdr_rx._reset_presence_cache()

    def _rtl_test(self, *outputs):
        """Patch subprocess.run to answer `rtl_test -t` with each output in
        turn (the last one repeats). Returns (patcher_ctx, calls)."""
        calls = []
        answers = list(outputs)

        def fake_run(*a, **k):
            calls.append(1)
            out = answers[min(len(calls) - 1, len(answers) - 1)]
            if isinstance(out, Exception):
                raise out
            return _FakeRtlTestResult(stdout=out)

        return mock.patch.object(sdr_rx.subprocess, "run", fake_run), calls

    def test_a_second_polled_call_inside_the_window_does_not_reshell_out(self):
        patch, calls = self._rtl_test(_ONE_DEVICE)
        with patch:
            first = sdr_rx.dongle_present(now=1000.0, cache=True)
            second = sdr_rx.dongle_present(now=1010.0, cache=True)  # +10s, inside 30s
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(len(calls), 1,
                         "a cached answer must not re-invoke rtl_test")

    def test_the_cache_expires_after_the_window(self):
        patch, calls = self._rtl_test(_ONE_DEVICE)
        with patch:
            sdr_rx.dongle_present(now=1000.0, cache=True)
            sdr_rx.dongle_present(now=1031.0, cache=True)   # +31s, past 30s
        self.assertEqual(len(calls), 2,
                         "a stale answer must not survive past the cache window")

    def test_an_unplug_is_seen_again_within_one_window(self):
        # A cache that never re-probed would mask a real unplug forever --
        # the whole point of a TTL is that "still cached" and "still true"
        # cannot silently become the same thing.
        patch, _calls = self._rtl_test(_ONE_DEVICE, _NO_DEVICE)
        with patch:
            first = sdr_rx.dongle_present(now=2000.0, cache=True)
            second = sdr_rx.dongle_present(now=2031.0, cache=True)
        self.assertTrue(first)
        self.assertFalse(second)

    def test_the_default_is_no_cache_at_all(self):
        # Satellites' Listen refuses outright on a False here
        # (services/satellites/routes.py NO_DONGLE), so plugging a dongle in
        # must work on the next click, not up to 30 s later. This function was
        # extracted from listen.py, which probed every time; the cache arrived
        # with the extraction and changed that contract silently.
        patch, calls = self._rtl_test(_NO_DEVICE, _ONE_DEVICE)
        with patch:
            self.assertFalse(sdr_rx.dongle_present(now=3000.0))
            self.assertTrue(sdr_rx.dongle_present(now=3001.0),
                            "an uncached probe must see the dongle that was "
                            "just plugged in, not a one-second-old 'no'")
        self.assertEqual(len(calls), 2)

    def test_a_polled_call_never_reads_an_uncached_call_s_answer(self):
        # The reverse of the above: an uncached probe must not WRITE the cache
        # either, or a satellites page refresh would silently set the answer
        # every other reader gets for the next 30 s.
        patch, calls = self._rtl_test(_NO_DEVICE, _ONE_DEVICE)
        with patch:
            self.assertFalse(sdr_rx.dongle_present(now=4000.0))
            self.assertTrue(sdr_rx.dongle_present(now=4001.0, cache=True))
        self.assertEqual(len(calls), 2)

    def test_a_failed_probe_is_never_cached(self):
        # One rtl_test timeout is a bad probe, not a measurement of the
        # hardware. Caching it turned a blip into a 30 s outage for every
        # reader in the process.
        patch, calls = self._rtl_test(OSError("rtl_test blew up"), _ONE_DEVICE)
        with patch:
            self.assertFalse(sdr_rx.dongle_present(now=5000.0, cache=True))
            self.assertTrue(sdr_rx.dongle_present(now=5001.0, cache=True),
                            "a transient probe failure must not be remembered")
        self.assertEqual(len(calls), 2)

    def test_an_injected_runner_never_poisons_the_shared_cache(self):
        # listen.preconditions(run=fake) must not decide the dongle question
        # for whatever runs next in the same process -- a latent test-order
        # flake, and a live one on any box where a fake runner ever reaches
        # this (the injection point exists for tests, but the global does not
        # know that).
        fake = lambda *a, **k: _FakeRtlTestResult(stdout=_NO_DEVICE)  # noqa: E731
        self.assertFalse(sdr_rx.dongle_present(run=fake, now=6000.0, cache=True))
        patch, calls = self._rtl_test(_ONE_DEVICE)
        with patch:
            self.assertTrue(sdr_rx.dongle_present(now=6001.0, cache=True),
                            "the fake runner's answer survived its own call")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
