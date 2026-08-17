import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from services.aprs.common import warning_catalog as wc

class CatalogTest(unittest.TestCase):
    def test_real_catalog_maps_known_types(self):
        m = wc.load_symbol_map(_ROOT)
        self.assertEqual(m["flood"], ("\\", "w"))
        self.assertEqual(m["eoc"], ("/", "o"))
        self.assertEqual(len(m), 16)

    def test_missing_root_returns_empty(self):
        self.assertEqual(wc.load_symbol_map("/no/such/root"), {})

    def test_abbr_map(self):
        m = wc.load_abbr_map(_ROOT)
        self.assertEqual(m["first_aid"], "AID")
        self.assertEqual(m["flood"], "FLOOD")
        self.assertEqual(len(m), 16)

    def test_abbr_map_missing_root_returns_empty(self):
        self.assertEqual(wc.load_abbr_map("/no/such/root"), {})


class StationCallsignTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self._tmp, "configuration"), exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_station(self, data):
        import json
        with open(os.path.join(self._tmp, "configuration", "station.json"), "w") as fh:
            json.dump(data, fh)

    def test_reads_callsign(self):
        self._write_station({"callsign": "W4MHI"})
        self.assertEqual(wc.station_callsign(self._tmp), "W4MHI")

    def test_missing_file_returns_none(self):
        self.assertIsNone(wc.station_callsign(self._tmp))

    def test_missing_callsign_key_returns_none(self):
        self._write_station({"grid": "CN87XN"})
        self.assertIsNone(wc.station_callsign(self._tmp))

    def test_blank_callsign_returns_none(self):
        self._write_station({"callsign": ""})
        self.assertIsNone(wc.station_callsign(self._tmp))


if __name__ == "__main__":
    unittest.main()
