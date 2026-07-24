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

    def test_load_cache_tolerates_non_utf8_byte(self):
        # A USB copy can flip a byte in a cache file (0xb0 = latin-1 degree sign,
        # invalid as UTF-8). load_cache must not 500 the whole endpoint over it —
        # the corrupt entry degrades, valid ones still parse.
        with tempfile.TemporaryDirectory() as d:
            with open(FIXTURE, "rb") as fh:
                raw = fh.read()
            corrupt = raw.replace(b"NOAA 19", b"NOAA 1\xb09", 1)
            self.assertNotEqual(corrupt, raw)      # fixture actually contains it
            with open(os.path.join(d, "weather.txt"), "wb") as fh:
                fh.write(corrupt)
            sats = tle.load_cache(d)               # must not raise
            self.assertIn("ISS (ZARYA)", sats)     # the clean entry survives

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

    def test_norad_of_parses_line1(self):
        self.assertEqual(
            tle.norad_of("1 25544U 98067A   24010.51782528 -.00002182  00000-0 -24984-4 0  9990"),
            25544)
        self.assertIsNone(tle.norad_of("garbage"))
        self.assertIsNone(tle.norad_of(""))

    def test_index_by_norad_keys_on_catalog_number(self):
        with open(FIXTURE) as fh:
            idx = tle.index_by_norad(tle.parse_tle_text(fh.read()))
        # ISS (25544) and NOAA 19 (33591) indexed by NORAD id, not name.
        self.assertIn(25544, idx)
        self.assertIn(33591, idx)
        name, l1, l2 = idx[25544]
        self.assertEqual(name, "ISS (ZARYA)")
        self.assertTrue(l1.startswith("1 25544"))

if __name__ == "__main__":
    unittest.main()
