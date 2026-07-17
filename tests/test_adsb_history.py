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

    def test_recent_one_row_per_aircraft_latest_obs(self):
        w = history.open_writer(self.db)
        t = time.time()
        # Two observations for the same aircraft — recent() keeps only the latest.
        history.record(w, {"hex": "aaa111", "flight": "OLD1 ", "lat": 1.0,
                           "lon": 1.0, "alt_baro": 1000, "gs": 100.0,
                           "track": 10.0, "squawk": "1200"}, t - 300)
        history.record(w, {"hex": "aaa111", "flight": "OLD1 ", "lat": 2.0,
                           "lon": 2.0, "alt_baro": 2000, "gs": 110.0,
                           "track": 20.0, "squawk": "1200"}, t - 60)
        # A second aircraft, and one outside the window (excluded).
        history.record(w, {"hex": "bbb222", "flight": "NEW2 ", "lat": 3.0,
                           "lon": 3.0, "alt_baro": 9000}, t - 30)
        history.record(w, {"hex": "ccc333", "flight": "OLDX ", "lat": 4.0,
                           "lon": 4.0, "alt_baro": 500}, t - 100000)
        w.close()
        r, err = history.open_reader(self.db)
        self.assertIsNone(err)
        rows = history.recent(r, since_ts=t - 3600)   # last hour
        by_hex = {a["hex"]: a for a in rows}
        self.assertEqual(set(by_hex), {"aaa111", "bbb222"})   # ccc333 out of window
        # Latest observation wins for the repeated aircraft, live-shaped fields.
        self.assertEqual(by_hex["aaa111"]["alt_baro"], 2000)
        self.assertEqual(by_hex["aaa111"]["lat"], 2.0)
        self.assertEqual(by_hex["aaa111"]["ts"], t - 60)
        self.assertEqual(by_hex["aaa111"]["flight"], "OLD1")

    def test_reader_missing_db_returns_error(self):
        r, err = history.open_reader(os.path.join(self.dir, "nope.db"))
        self.assertIsNone(r)
        self.assertIn("not found", err.lower())

if __name__ == "__main__":
    unittest.main()
