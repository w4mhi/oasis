import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import scan  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
