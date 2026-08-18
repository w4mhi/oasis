import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import listener, scan  # noqa: E402

# rtl_power CSV: date, time, low, high, step, samples, then one dBm per bin.
# 162.390 -> 162.560 in 5 kHz steps = 34 bins. WX7 (162.550) is the strongest.
_BINS = ["-40.0"] * 34
_BINS[32] = "-12.5"          # 162390000 + 5000*32.5 = 162552500 -> nearest WX7
CSV = ("2026-08-17, 12:00:00, 162390000, 162560000, 5000.00, 100, "
       + ", ".join(_BINS) + "\n")


class ScanCommandTest(unittest.TestCase):
    def test_is_argv_with_the_nwr_band(self):
        argv = scan.scan_command("auto", 0, seconds=10)
        self.assertEqual(argv[0], "rtl_power")
        self.assertIn("162390000:162560000:5000", " ".join(argv))
        self.assertIn("-e", argv)

    def test_pins_the_dongle_by_serial(self):
        argv = scan.scan_command("auto", 0, device_serial="00000002")
        i = argv.index("-d")
        self.assertEqual(argv[i + 1], "00000002")

    def test_gain_and_ppm_flags_are_wired(self):
        argv = scan.scan_command("49.6", 3, seconds=10)
        self.assertEqual(argv[argv.index("-g") + 1], "49.6")
        self.assertEqual(argv[argv.index("-p") + 1], "3")

    def test_integration_flag_is_wired(self):
        argv = scan.scan_command("auto", 0, seconds=10)
        self.assertEqual(argv[argv.index("-i") + 1], "1")

    def test_step_hz_keyword_changes_the_band_string(self):
        argv = scan.scan_command("auto", 0, seconds=10, step_hz=2500)
        self.assertIn("162390000:162560000:2500", " ".join(argv))

    def test_auto_gain_omits_dash_g(self):
        # Same rtl_power/rtl_fm atof("-g") trap as listener.rtl_command(): a
        # sweep run with "-g auto" was taken at 0 dB tuner gain, not AGC.
        argv = scan.scan_command("auto", 0, seconds=10)
        self.assertNotIn("-g", argv)

    def test_blank_gain_omits_dash_g(self):
        argv = scan.scan_command("", 0, seconds=10)
        self.assertNotIn("-g", argv)

    def test_none_gain_omits_dash_g(self):
        argv = scan.scan_command(None, 0, seconds=10)
        self.assertNotIn("-g", argv)

    def test_numeric_gain_emits_dash_g(self):
        argv = scan.scan_command("40", 0, seconds=10)
        self.assertEqual(argv[argv.index("-g") + 1], "40")


class ChannelPowersTest(unittest.TestCase):
    def test_every_channel_gets_a_reading(self):
        powers = scan.channel_powers(CSV)
        self.assertEqual(len(powers), 7)
        self.assertIn(162550000, powers)

    def test_the_strongest_channel_wins(self):
        hz, dbm = scan.best_channel(scan.channel_powers(CSV))
        self.assertEqual(hz, 162550000)
        self.assertAlmostEqual(dbm, -12.5, places=1)

    def test_empty_input_is_not_a_crash(self):
        self.assertEqual(scan.channel_powers(""), {})
        self.assertEqual(scan.best_channel({}), (None, None))

    def test_garbage_rows_are_skipped(self):
        powers = scan.channel_powers("nonsense\n" + CSV + "also nonsense\n")
        self.assertEqual(len(powers), 7)

    def test_equal_power_ties_go_to_the_lowest_frequency_channel(self):
        # Bin 2 (centre 162402500) is WX1's strongest bin; bin 7 (centre
        # 162427500) is WX2's. channel_powers inserts WX1 into `best` first
        # because it scans bins low-to-high, so an exact tie must resolve
        # to WX1 -- exercising that insertion-order dependency directly,
        # not just asserting it from a hand-built dict.
        bins = ["-40.0"] * 34
        bins[2] = "-20.0"
        bins[7] = "-20.0"
        tie_csv = ("2026-08-17, 12:00:00, 162390000, 162560000, 5000.00, 100, "
                   + ", ".join(bins) + "\n")
        hz, _ = scan.best_channel(scan.channel_powers(tie_csv))
        self.assertEqual(hz, 162400000)


class _FakeResult:
    """Stand-in for subprocess.CompletedProcess, injected via `runner`."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunTest(unittest.TestCase):
    """`run()`'s error paths are exactly what an operator hits on a Pi with
    no rtl_power, or a dongle another service already holds -- so they are
    exercised here via the `runner` injection point rather than a real
    subprocess.
    """

    def _keys(self, result):
        return set(result.keys())

    def test_missing_rtl_power_is_reported_not_raised(self):
        def runner(*a, **k):
            raise FileNotFoundError("no rtl_power")

        result = scan.run(runner=runner)
        self.assertEqual(
            result,
            {"ok": False, "error": "rtl_power is not installed",
             "code": "NWR_NO_RTL_POWER", "powers": {},
             "best_hz": None, "best_dbm": None},
        )

    def test_nonzero_exit_is_a_failure_not_a_clean_band(self):
        def runner(*a, **k):
            return _FakeResult(returncode=1, stdout="",
                                stderr="usb_claim_interface error -6\n")

        result = scan.run(runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NWR_SCAN_FAILED")
        self.assertEqual(result["powers"], {})
        self.assertIsNone(result["best_hz"])
        self.assertIsNone(result["best_dbm"])
        self.assertIn("dongle", result["error"])
        self.assertEqual(self._keys(result),
                          {"ok", "error", "code", "powers", "best_hz", "best_dbm"})

    def test_timeout_is_reported_not_raised(self):
        def runner(*a, **k):
            raise subprocess.TimeoutExpired(cmd="rtl_power", timeout=40)

        result = scan.run(runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NWR_SCAN_FAILED")
        self.assertEqual(result["powers"], {})
        self.assertIsNone(result["best_hz"])
        self.assertIsNone(result["best_dbm"])

    def test_successful_sweep_reports_powers_and_best_channel(self):
        def runner(*a, **k):
            return _FakeResult(returncode=0, stdout=CSV, stderr="")

        result = scan.run(runner=runner)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["best_hz"], 162550000)
        self.assertAlmostEqual(result["best_dbm"], -12.5, places=1)
        self.assertEqual(len(result["powers"]), 7)
        self.assertEqual(self._keys(result),
                          {"ok", "error", "code", "powers", "best_hz", "best_dbm"})

    def test_clean_exit_with_empty_stdout_is_a_failure(self):
        # A genuine sweep emits dozens of CSV bins per second of -e; a
        # zero-exit with nothing parseable means the sweep never actually
        # ran, not that the band was measured and found silent.
        def runner(*a, **k):
            return _FakeResult(returncode=0, stdout="", stderr="")

        result = scan.run(runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NWR_SCAN_FAILED")
        self.assertEqual(result["powers"], {})
        self.assertIsNone(result["best_hz"])
        self.assertIsNone(result["best_dbm"])


class ScanArbitrationTest(unittest.TestCase):
    """Finding 4: run() blocks its Flask worker for up to `seconds + 30` while
    rtl_power owns the dongle, but nothing reported NWR as a claimant during
    that window -- a concurrent /api/nwr/listen would pass every gate and
    spawn an rtl_fm that instantly lost the tuner it never actually held."""

    def setUp(self):
        self._saved = dict(listener._state)

    def tearDown(self):
        listener._state.clear()
        listener._state.update(self._saved)

    def test_the_dongle_reads_claimed_for_the_duration_of_the_sweep(self):
        seen = {}

        def runner(*a, **k):
            seen["during"] = listener.is_claimed()
            return _FakeResult(returncode=0, stdout=CSV, stderr="")

        self.assertFalse(listener.is_claimed())
        scan.run(runner=runner)
        self.assertTrue(seen.get("during"), "the sweep must be visible to "
                        "is_claimed() while it is actually running")
        self.assertFalse(listener.is_claimed(), "the claim must be released "
                         "once the sweep ends")

    def test_the_claim_clears_even_when_the_runner_raises(self):
        def runner(*a, **k):
            raise subprocess.TimeoutExpired(cmd="rtl_power", timeout=40)

        scan.run(runner=runner)
        self.assertFalse(listener.is_claimed())

    def test_the_claim_clears_even_when_rtl_power_is_missing(self):
        def runner(*a, **k):
            raise FileNotFoundError("no rtl_power")

        scan.run(runner=runner)
        self.assertFalse(listener.is_claimed())


if __name__ == "__main__":
    unittest.main()
