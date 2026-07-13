import os, sys, tempfile, time, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))          # repo root
from services.adsb.common import history

class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "adsb-history.db")

    def test_record_then_read_back(self):
        w = history.open_writer(self.db)
        t = time.time()
        history.record(w, {"hex": "a1b2c3", "flight": "N123AB ",
                           "lat": 47.6, "lon": -122.3, "alt_baro": 3500,
                           "gs": 120.0, "track": 90.0, "squawk": "1200"}, t)
        w.close()
        r, err = history.open_reader(self.db)
        self.assertIsNone(err)
        rows = history.history(r, since_ts=t - 1, icao=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["icao"], "a1b2c3")
        self.assertEqual(rows[0]["callsign"], "N123AB")   # trimmed
        self.assertEqual(rows[0]["alt"], 3500)

    def test_reader_missing_db_returns_error(self):
        r, err = history.open_reader(os.path.join(self.dir, "nope.db"))
        self.assertIsNone(r)
        self.assertIn("not found", err.lower())

if __name__ == "__main__":
    unittest.main()
