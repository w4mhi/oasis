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

    def test_recent_reads_snapshot_not_observations_scan(self):
        # recent() must serve the latest-per-aircraft feed from the aircraft
        # snapshot columns, NOT by scanning observations. Proof: wipe every
        # observation row, and recent() still returns the aircraft with its
        # last position — because record() maintained the snapshot.
        w = history.open_writer(self.db)
        t = time.time()
        history.record(w, {"hex": "aaa111", "flight": "SNAP1 ", "lat": 5.0,
                           "lon": 6.0, "alt_baro": 4200, "gs": 130.0,
                           "track": 45.0, "squawk": "1234"}, t - 10)
        w.execute("DELETE FROM observations")     # no history rows left at all
        w.close()
        r, err = history.open_reader(self.db)
        self.assertIsNone(err)
        rows = history.recent(r, since_ts=t - 3600)
        by_hex = {a["hex"]: a for a in rows}
        self.assertEqual(set(by_hex), {"aaa111"})
        self.assertEqual(by_hex["aaa111"]["alt_baro"], 4200)
        self.assertEqual(by_hex["aaa111"]["lat"], 5.0)
        self.assertEqual(by_hex["aaa111"]["flight"], "SNAP1")
        self.assertEqual(by_hex["aaa111"]["ts"], t - 10)

    def test_record_snapshot_keeps_latest_not_stale(self):
        # An out-of-order (older) observation must not clobber a newer snapshot.
        w = history.open_writer(self.db)
        t = time.time()
        history.record(w, {"hex": "bbb222", "flight": "NEW ", "lat": 2.0,
                           "lon": 2.0, "alt_baro": 2000}, t)
        history.record(w, {"hex": "bbb222", "flight": "OLD ", "lat": 1.0,
                           "lon": 1.0, "alt_baro": 1000}, t - 500)   # older, ignored
        w.close()
        r, _ = history.open_reader(self.db)
        rows = history.recent(r, since_ts=t - 3600)
        self.assertEqual(rows[0]["alt_baro"], 2000)
        self.assertEqual(rows[0]["ts"], t)

    def _obs_count(self, conn):
        return conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    def test_record_skips_unchanged_position(self):
        # Same lat/lon/alt on consecutive frames → one breadcrumb, not one per
        # frame. The snapshot's last_heard still advances to the newest frame.
        w = history.open_writer(self.db)
        t = time.time()
        ac = {"hex": "sta111", "lat": 1.5, "lon": 2.5, "alt_baro": 3000}
        history.record(w, ac, t)
        history.record(w, ac, t + 1)
        history.record(w, ac, t + 2)
        self.assertEqual(self._obs_count(w), 1)             # deduped
        last_heard = w.execute("SELECT last_heard FROM aircraft WHERE icao='sta111'").fetchone()[0]
        self.assertEqual(last_heard, t + 2)                 # snapshot still fresh
        w.close()

    def test_record_inserts_on_position_change(self):
        w = history.open_writer(self.db)
        t = time.time()
        history.record(w, {"hex": "mov111", "lat": 1.0, "lon": 1.0, "alt_baro": 3000}, t)
        history.record(w, {"hex": "mov111", "lat": 1.0, "lon": 1.0, "alt_baro": 3000}, t + 1)  # same
        history.record(w, {"hex": "mov111", "lat": 1.2, "lon": 1.3, "alt_baro": 3200}, t + 2)  # moved
        self.assertEqual(self._obs_count(w), 2)             # first sighting + the move
        w.close()

    def test_record_skips_positionless_frame(self):
        # Mode-S-only frame (no lat/lon) leaves no breadcrumb, but the aircraft is
        # still tracked (snapshot row exists so it shows in recent(), position-less).
        w = history.open_writer(self.db)
        t = time.time()
        history.record(w, {"hex": "nop111", "flight": "MODES ", "alt_baro": 3000}, t)
        self.assertEqual(self._obs_count(w), 0)
        r, _ = history.open_reader(self.db)
        rows = history.recent(r, since_ts=t - 3600)
        self.assertEqual(rows[0]["hex"], "nop111")
        self.assertIsNone(rows[0]["lat"])

    def test_prune_deletes_old_observations_keeps_recent(self):
        w = history.open_writer(self.db)
        t = time.time()
        history.record(w, {"hex": "old000", "lat": 1.0, "lon": 1.0}, t - 100000)
        history.record(w, {"hex": "new000", "lat": 2.0, "lon": 2.0}, t - 10)
        deleted = history.prune(w, older_than_ts=t - 3600)
        self.assertEqual(deleted, 1)
        remaining = [r[0] for r in
                     w.execute("SELECT icao FROM observations").fetchall()]
        self.assertEqual(remaining, ["new000"])
        w.close()

    def test_open_writer_migrates_legacy_schema(self):
        # A pre-snapshot DB: aircraft table with no position columns. open_writer
        # must add them idempotently so record()/recent() work after upgrade.
        legacy = os.path.join(self.dir, "legacy.db")
        import sqlite3
        c = sqlite3.connect(legacy)
        c.execute("CREATE TABLE aircraft (icao TEXT PRIMARY KEY, callsign TEXT, "
                  "first_heard REAL, last_heard REAL)")
        c.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "icao TEXT NOT NULL, ts REAL NOT NULL, lat REAL, lon REAL, "
                  "alt INTEGER, speed REAL, track REAL, squawk TEXT)")
        c.execute("INSERT INTO aircraft(icao, last_heard) VALUES('leg111', ?)",
                  (time.time(),))
        c.commit(); c.close()
        w = history.open_writer(legacy)          # must not raise; adds columns
        t = time.time()
        history.record(w, {"hex": "leg111", "lat": 9.0, "lon": 9.0,
                           "alt_baro": 7000}, t)
        w.close()
        r, _ = history.open_reader(legacy)
        rows = history.recent(r, since_ts=t - 3600)
        self.assertEqual(rows[0]["lat"], 9.0)

    def test_reader_missing_db_returns_error(self):
        r, err = history.open_reader(os.path.join(self.dir, "nope.db"))
        self.assertIsNone(r)
        self.assertIn("not found", err.lower())

if __name__ == "__main__":
    unittest.main()
