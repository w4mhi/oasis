import os, sys, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
import tle  # noqa: E402

FIXTURE = os.path.join(_HERE, "fixtures", "tle-sample.txt")

class TleTest(unittest.TestCase):
    def test_parse_returns_name_to_lines(self):
        with open(FIXTURE) as fh:
            sats = tle.parse_tle_text(fh.read())
        self.assertIn("ISS (ZARYA)", sats)
        l1, l2 = sats["ISS (ZARYA)"]
        self.assertTrue(l1.startswith("1 25544"))
        self.assertTrue(l2.startswith("2 25544"))
        self.assertEqual(len(sats), 2)

    def test_load_cache_merges_group_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(FIXTURE) as fh:
                open(os.path.join(d, "weather.txt"), "w").write(fh.read())
            sats = tle.load_cache(d)
            self.assertIn("NOAA 19", sats)

    def test_cache_age_none_when_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(tle.cache_age_days(d))

    def test_cache_mtime_stable_and_none_when_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(tle.cache_mtime(d))
            with open(FIXTURE) as fh:
                open(os.path.join(d, "weather.txt"), "w").write(fh.read())
            m1 = tle.cache_mtime(d)
            self.assertIsNotNone(m1)
            self.assertEqual(m1, tle.cache_mtime(d))   # stable across calls

if __name__ == "__main__":
    unittest.main()
