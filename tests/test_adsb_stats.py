import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))          # repo root
from services.adsb.common import adsb

# dump1090-fa stats.json layout (per README-json.md): `messages` is a
# PERIOD-level key (sibling of `local`/`remote`), while `samples_processed`,
# `signal`, `noise` live inside `local`. A working receiver seeing little air
# traffic (messages: 1 in the whole minute) is exactly the case that makes
# samples_processed — not messages — the right health signal.
REAL_STATS_FIXTURE = {
    "latest": {
        "start": 1784154896.4, "end": 1784154896.4,
        "messages": 0,
        "local": {"samples_processed": 0},
    },
    "last1min": {
        "start": 1784154836.4, "end": 1784154896.4,
        "messages": 1,
        "local": {"samples_processed": 144048128, "signal": -33.6, "noise": -33.5},
    },
    "last5min": {
        "start": 1784154596.3, "end": 1784154896.4,
        "messages": 10,
        "local": {"samples_processed": 720240640, "signal": -32.9, "noise": -31.3},
    },
}


class ParseAdsbStatsTest(unittest.TestCase):
    def test_derives_rates_from_real_fixture(self):
        derived = adsb.parse_adsb_stats(REAL_STATS_FIXTURE)
        self.assertAlmostEqual(derived["samples_per_sec"], 144048128 / 60.0)
        self.assertAlmostEqual(derived["messages_per_min"], 1.0)
        self.assertEqual(derived["signal_dbfs"], -33.6)
        self.assertEqual(derived["noise_dbfs"], -33.5)
        self.assertTrue(derived["flowing"])

    def test_flowing_false_when_samples_processed_zero(self):
        stats = {"last1min": {"start": 0, "end": 60,
                              "local": {"samples_processed": 0}}}
        derived = adsb.parse_adsb_stats(stats)
        self.assertEqual(derived["samples_per_sec"], 0)
        self.assertFalse(derived["flowing"])

    def test_none_when_last1min_missing(self):
        self.assertIsNone(adsb.parse_adsb_stats({}))

    def test_none_when_last1min_not_a_dict(self):
        self.assertIsNone(adsb.parse_adsb_stats({"last1min": "bogus"}))

    def test_none_when_window_duration_is_zero(self):
        # Matches the real "latest" bucket right after a window rollover:
        # start == end, an empty in-progress accumulator — must not raise
        # ZeroDivisionError.
        stats = {"last1min": {"start": 100.0, "end": 100.0,
                              "local": {"samples_processed": 0}}}
        self.assertIsNone(adsb.parse_adsb_stats(stats))

    def test_none_when_stats_not_a_dict(self):
        self.assertIsNone(adsb.parse_adsb_stats(None))
        self.assertIsNone(adsb.parse_adsb_stats("bogus"))

    def test_scales_messages_when_window_shorter_than_a_minute(self):
        # Right after dump1090-fa (re)starts, last1min hasn't accumulated a
        # full 60s yet — messages_per_min should still scale to a per-minute
        # rate, not report the raw window count unscaled.
        stats = {"last1min": {"start": 100.0, "end": 130.0, "messages": 5,
                              "local": {"samples_processed": 30000}}}
        derived = adsb.parse_adsb_stats(stats)
        self.assertAlmostEqual(derived["messages_per_min"], 10.0)
        self.assertAlmostEqual(derived["samples_per_sec"], 1000.0)

if __name__ == "__main__":
    unittest.main()
