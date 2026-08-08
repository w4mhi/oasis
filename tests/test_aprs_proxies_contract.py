"""
/api/aprs/{health,track,system} — the remaining GrayWolf proxies, on the contract.

health is the §2 case again: `ok` meant "graywolf-api answered", and BOTH service
pills inferred up/down from the HTTP status. A stopped optional service is an
answer, so this is now always 200 with `running` carrying the state — which is
exactly why the two consumers had to change with it.

track is the one list in OASIS that is deliberately ASCENDING: the points are
drawn as a polyline, so chronological order is the useful one.
"""
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
import app as oasis_app  # noqa: E402


class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status = body, status
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def get(self, path, payload):
        body = json.dumps(payload).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(body)):
            return self.c.get(path)

    def get_down(self, path):
        def boom(req, timeout=None):
            raise urllib.error.URLError("refused")
        with mock.patch.object(urllib.request, "urlopen", boom):
            return self.c.get(path)


class HealthTest(_Base):
    def test_running_reports_the_db(self):
        d = self.get("/api/aprs/health",
                     {"ok": True, "db": "/var/lib/graywolf/h.db", "db_exists": True}).get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], True)
        self.assertIs(d["db_exists"], True)
        self.assertIsNone(d["detail"])

    def test_daemon_down_is_200_with_running_false(self):
        r = self.get_down("/api/aprs/health")
        self.assertEqual(r.status_code, 200, "a stopped service is an answer (§2)")
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], False)
        self.assertIn("refused", d["detail"])

    def test_every_field_present_when_down(self):
        d = self.get_down("/api/aprs/health").get_json()
        for k in ("running", "db_exists", "db", "detail"):
            self.assertIn(k, d, k)

    def test_missing_db_is_running_but_not_ready(self):
        d = self.get("/api/aprs/health", {"ok": True, "db": None, "db_exists": False}).get_json()
        self.assertIs(d["running"], True)
        self.assertIs(d["db_exists"], False)


class TrackTest(_Base):
    P = [{"timestamp": "2026-08-08 01:53:14.019847214+00:00", "lat": 50.1234567,
          "lon": -120.7491234, "alt_m": 646.0, "speed_mph": 66.7, "course": 171},
         {"timestamp": "2026-08-08 01:51:00.5+00:00", "lat": 50.0, "lon": -120.7,
          "alt_m": None, "speed_mph": 0, "course": None}]

    def test_envelope_and_bounds(self):
        d = self.get("/api/aprs/track?callsign=W4MHI",
                     {"ok": True, "points": self.P}).get_json()
        for k in ("points", "total", "count", "truncated", "limit", "minutes", "callsign"):
            self.assertIn(k, d, k)
        self.assertIs(d["ok"], True)

    def test_points_are_chronological_not_newest_first(self):
        d = self.get("/api/aprs/track?callsign=W4MHI",
                     {"ok": True, "points": self.P}).get_json()
        times = [p["time"] for p in d["points"]]
        self.assertEqual(times, sorted(times), "a polyline needs time order")
        self.assertEqual(times[0], "2026-08-08T01:51:00Z")

    def test_timestamps_normalised_and_coords_rounded(self):
        p = self.get("/api/aprs/track?callsign=W4MHI",
                     {"ok": True, "points": self.P}).get_json()["points"][1]
        self.assertEqual(p["time"], "2026-08-08T01:53:14Z")
        self.assertEqual(p["lat"], 50.12346)
        self.assertNotIn("timestamp", p)

    def test_missing_callsign_is_400(self):
        r = self.c.get("/api/aprs/track")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "MISSING_CALLSIGN")

    def test_bad_minutes_degrades_and_is_clamped(self):
        for q, expect in (("minutes=abc", 60.0), ("minutes=-5", 60.0), ("minutes=999999", 43200.0)):
            d = self.get(f"/api/aprs/track?callsign=W4MHI&{q}",
                         {"ok": True, "points": []}).get_json()
            self.assertEqual(d["minutes"], expect, q)

    def test_empty_track_is_success(self):
        d = self.get("/api/aprs/track?callsign=W4MHI", {"ok": True, "points": []}).get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["points"], [])
        self.assertEqual(d["count"], 0)


class SystemTest(_Base):
    S = {"ok": True, "cpu_pct": 40.3, "cpu_temp_c": 56.5, "cpu_count": 4,
         "ram": {"pct": 50}, "disk": {"pct": 52.2}, "load": {"avg1": 4.5},
         "uptime_sec": 3600, "boot_str": "Fri Aug 07 19:29", "fcc_db_date": None}

    def test_fields_consumers_read_survive(self):
        d = self.get("/api/aprs/system", self.S).get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["cpu_pct"], 40.3)
        self.assertEqual(d["cpu_temp_c"], 56.5)

    def test_boot_time_is_iso_not_a_year_less_string(self):
        d = self.get("/api/aprs/system", self.S).get_json()
        self.assertNotIn("boot_str", d, "'Fri Aug 07 19:29' has no year or zone (§6)")
        self.assertRegex(d["boot_time"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_uptime_carries_its_unit(self):
        d = self.get("/api/aprs/system", self.S).get_json()
        self.assertEqual(d["uptime_s"], 3600)

    def test_daemon_down_is_503(self):
        r = self.get_down("/api/aprs/system")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["code"], "APRS_API_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
