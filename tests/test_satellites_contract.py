"""
/api/satellites, /passes, /track, /refresh, /select on the contract.

The roster and prediction surface — what the satellites page, the kiosk and a
model all read. Five defects, one of them a real crash:

  /track    request.args["from"] was read unguarded and parsed unguarded, so a
            missing param raised KeyError and a malformed one ValueError. Both
            surfaced as a bare HTTP 500 on what is plainly a bad request.
  /passes   `jsonify(cached)` — the response WAS whatever happened to be on
            disk, including `errors`, an internal per-satellite retry counter.
  /passes   an unconfigured station returned `{passes:{}, error:…}` at 200, so
            "no location set" and "nothing overhead for 48 h" looked alike.
  /select   echoed the ENTIRE roster (~150 birds with TLE lines) for one
            checkbox, to a client that discards the response.
  /refresh  ok:false at 200 for offline / timeout / failure alike.

/refresh is an ACTION, so ok:false is right — it just needed to stop being 200,
and to say WHICH failure it was. Offline is 503 (host cannot right now, try
later), a broken rebuild is 502, a slow one 504.
"""

import datetime
import json
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

import app as oasis_app                        # noqa: E402
from services.satellites import routes as sat_routes  # noqa: E402

# Probe the optional pass-prediction dep, same as tests/test_satellites_routes.py.
# Almost every contract test here runs WITHOUT it — that is the point, and it is
# what routes.py's deferred `import predict` now guarantees: a box that has not
# run install-predict.py must still honour the contract on every path that does
# not actually propagate (bad date range -> 400, no station -> 200, unknown
# satellite -> 404). Only a test that patches predict itself needs the real
# module, and only that one skips.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
    import predict as _predict_probe  # noqa: F401,E402
    _HAS_PREDICT = True
except Exception:  # noqa: BLE001
    _HAS_PREDICT = False

_HDR = {"X-OASIS-Request": "1"}
_ISO = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def _station(self, lat=47.6, lon=-122.3):
        return mock.patch.object(sat_routes, "_station",
                                 return_value={"lat": lat, "lon": lon})


class RosterTest(_Base):
    def test_envelope_and_bounds(self):
        d = self.c.get("/api/satellites").get_json()
        self.assertIs(d["ok"], True)
        for key in ("satellites", "total", "count", "truncated", "limit",
                    "tle_age_days", "station"):
            self.assertIn(key, d, f"contract requires `{key}`")

    def test_station_block_is_always_all_four_keys(self):
        d = self.c.get("/api/satellites").get_json()
        self.assertEqual(set(d["station"]), {"lat", "lon", "horizon", "min_elev"})

    def test_limit_bounds_the_roster(self):
        d = self.c.get("/api/satellites?limit=1").get_json()
        self.assertLessEqual(len(d["satellites"]), 1)
        self.assertEqual(d["limit"], 1)

    def test_a_nonsense_limit_degrades_to_the_default(self):
        d = self.c.get("/api/satellites?limit=abc").get_json()
        self.assertEqual(d["limit"], sat_routes._ROSTER_DEFAULT_LIMIT)
        self.assertIs(d["truncated"], False, "a real roster is ~150 birds")


class PassesTest(_Base):
    _KEYS = {"ok", "passes", "count", "computed", "complete", "computed_at",
             "window_h", "min_elev", "reason"}

    def test_unconfigured_station_is_a_state_not_an_error(self):
        """§2: nothing about the request was wrong. `computed:false` is what
        stops an empty `passes` reading as 'nothing overhead for 48 hours'."""
        with self._station(lat=None, lon=None):
            r = self.c.get("/api/satellites/passes")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["computed"], False)
        self.assertEqual(d["passes"], {})
        self.assertEqual(d["reason"], "no-station-location")
        self.assertEqual(set(d), self._KEYS)

    def test_computed_response_shape(self):
        with self._station(), \
             mock.patch.object(sat_routes, "_sats_by_norad", return_value={}):
            r = self.c.get("/api/satellites/passes?window=24")
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["computed"], True)
        self.assertEqual(d["window_h"], 24)
        self.assertEqual(set(d), self._KEYS)

    def test_internal_retry_bookkeeping_never_reaches_the_wire(self):
        """`errors` is a per-satellite consecutive-failure counter this endpoint
        keeps so it can stop retrying a decayed TLE. It is meaningless to a
        caller and used to ship in every response because the cache file WAS
        the response."""
        with self._station(), \
             mock.patch.object(sat_routes, "_sats_by_norad", return_value={}):
            d = self.c.get("/api/satellites/passes").get_json()
        self.assertNotIn("errors", d)

    def test_computed_at_is_iso_not_epoch(self):
        with self._station(), \
             mock.patch.object(sat_routes, "_sats_by_norad", return_value={}):
            d = self.c.get("/api/satellites/passes?window=3").get_json()
        self.assertRegex(d["computed_at"], _ISO)

    def test_the_served_cache_has_the_same_shape_as_a_fresh_compute(self):
        """The serve and compute paths converge on one return — they cannot
        drift apart, which is why the helper became a single exit."""
        with self._station(), \
             mock.patch.object(sat_routes, "_sats_by_norad", return_value={}):
            first = self.c.get("/api/satellites/passes?window=7").get_json()
            second = self.c.get("/api/satellites/passes?window=7").get_json()
        self.assertEqual(set(first), set(second))
        self.assertEqual(first["computed_at"], second["computed_at"],
                         "the second read is served from cache")


class TrackTest(_Base):
    def test_missing_time_range_is_400_not_a_500(self):
        """The crash: request.args["from"] raised KeyError straight out of the
        view, so omitting a query param returned a bare Internal Server Error."""
        with self._station():
            r = self.c.get("/api/satellites/track?sat=25544")
        self.assertEqual(r.status_code, 400)
        self.assertIs(r.get_json()["ok"], False)
        self.assertEqual(r.get_json()["code"], "MISSING_TIME_RANGE")

    def test_malformed_time_range_is_400(self):
        with self._station():
            r = self.c.get("/api/satellites/track?sat=25544&from=yesterday&to=now")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "INVALID_TIME_RANGE")

    def test_backwards_time_range_is_400(self):
        with self._station():
            r = self.c.get("/api/satellites/track?sat=25544"
                           "&from=2026-08-08T12:00:00&to=2026-08-08T11:00:00")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "INVALID_TIME_RANGE")

    def test_unknown_satellite_is_404_distinct_from_no_station(self):
        """Two causes were merged into one `{track: [], error: …}` at 200. One is
        fixed in Setup, the other by asking for a different bird."""
        with self._station(), \
             mock.patch.object(sat_routes, "_sats_by_norad", return_value={}):
            r = self.c.get("/api/satellites/track?sat=99999"
                           "&from=2026-08-08T11:00:00&to=2026-08-08T12:00:00")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["code"], "UNKNOWN_SATELLITE")

    def test_no_station_is_ok_true_with_an_empty_track(self):
        with self._station(lat=None, lon=None):
            r = self.c.get("/api/satellites/track?sat=25544"
                           "&from=2026-08-08T11:00:00&to=2026-08-08T12:00:00")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["track"], [])
        self.assertEqual(d["reason"], "no-station-location")

    @unittest.skipUnless(_HAS_PREDICT, "skyfield/predict not installed")
    def test_a_real_track_carries_the_envelope(self):
        # The only test in this file that needs the real module: it patches
        # predict.compute_track, and you cannot patch an attribute on a module
        # that will not import.
        fake = object()
        with self._station(), \
             mock.patch.object(sat_routes, "_sats_by_norad", return_value={25544: fake}), \
             mock.patch("predict.compute_track", return_value=[{"lat": 1, "lon": 2}]):
            d = self.c.get("/api/satellites/track?sat=25544"
                           "&from=2026-08-08T11:00:00&to=2026-08-08T12:00:00").get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["norad"], 25544)
        self.assertIn("l1", d)
        self.assertIn("l2", d)


class RefreshTest(_Base):
    def test_offline_is_503_with_a_code(self):
        """§2: an ACTION that did not happen is ok:false — correctly — but 200
        made 'no internet' look like a successful rebuild to anything checking
        the status first."""
        with mock.patch("common.oasis_lib.has_internet", return_value=False):
            r = self.c.post("/api/satellites/refresh", headers=_HDR)
        self.assertEqual(r.status_code, 503)
        d = r.get_json()
        self.assertIs(d["ok"], False)
        self.assertEqual(d["code"], "OFFLINE")
        self.assertIn("tle_age_days", d, "the pill still needs the age")

    def test_a_failed_rebuild_is_502(self):
        done = mock.Mock(returncode=1, stdout="", stderr="celestrak unreachable")
        with mock.patch("common.oasis_lib.has_internet", return_value=True), \
             mock.patch("subprocess.run", return_value=done):
            r = self.c.post("/api/satellites/refresh", headers=_HDR)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["code"], "REFRESH_FAILED")

    def test_a_timeout_is_504(self):
        import subprocess
        with mock.patch("common.oasis_lib.has_internet", return_value=True), \
             mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("x", 180)):
            r = self.c.post("/api/satellites/refresh", headers=_HDR)
        self.assertEqual(r.status_code, 504)
        self.assertEqual(r.get_json()["code"], "REFRESH_TIMED_OUT")

    def test_the_build_summary_is_nested_not_splatted(self):
        """§5: splatting a subprocess's stdout into the envelope made the
        response shape depend on another program's output."""
        out = json.dumps({"satellites": 141, "source": "satnogs"})
        done = mock.Mock(returncode=0, stdout=out, stderr="")
        with mock.patch("common.oasis_lib.has_internet", return_value=True), \
             mock.patch("subprocess.run", return_value=done):
            d = self.c.post("/api/satellites/refresh", headers=_HDR).get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["summary"]["satellites"], 141)
        self.assertNotIn("satellites", d, "summary keys stay inside `summary`")

    def test_unparseable_summary_still_succeeds_with_an_empty_object(self):
        done = mock.Mock(returncode=0, stdout="not json at all", stderr="")
        with mock.patch("common.oasis_lib.has_internet", return_value=True), \
             mock.patch("subprocess.run", return_value=done):
            d = self.c.post("/api/satellites/refresh", headers=_HDR).get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["summary"], {})

    def test_csrf_header_required(self):
        self.assertEqual(self.c.post("/api/satellites/refresh").status_code, 403)


class SelectTest(_Base):
    def test_malformed_bodies_are_400_with_a_code(self):
        for body in ({}, {"norad": "nope", "selected": True}, {"selections": [1, 2]}):
            r = self.c.post("/api/satellites/select", headers=_HDR, json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.get_json()["code"], "INVALID_SELECTIONS", body)

    def test_an_unwritable_roster_reports_why(self):
        with mock.patch("roster.set_selected_many",
                        side_effect=OSError("Permission denied")):
            r = self.c.post("/api/satellites/select", headers=_HDR,
                            json={"norad": 25544, "selected": True})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["code"], "ROSTER_NOT_WRITABLE")
        self.assertIn("writable", r.get_json()["error"])

    def test_csrf_header_required(self):
        self.assertEqual(self.c.post("/api/satellites/select",
                                     json={"norad": 1, "selected": True}).status_code, 403)


class NoEpochOnTheWireTest(_Base):
    def test_passes_reports_no_raw_epoch(self):
        with self._station(), \
             mock.patch.object(sat_routes, "_sats_by_norad", return_value={}):
            d = self.c.get("/api/satellites/passes").get_json()
        # The guard is "an unexplained float on the wire is probably an epoch".
        # These are the explained ones: counts and a window in hours, plus
        # min_elev, which is DEGREES and legitimately fractional — a horizon mask
        # of 12.5 is a normal thing for an operator to want.
        for key, value in d.items():
            if key in ("count", "window_h", "min_elev"):
                continue
            self.assertNotIsInstance(value, float,
                                     f"`{key}` looks like a raw epoch (§6)")

    def test_iso_helper_agrees_with_datetime(self):
        # Guards the conversion itself: computed_at is stored as an epoch and
        # rendered as ISO, and those two must describe the same instant.
        from common.api_shape import iso_utc
        now = datetime.datetime(2026, 8, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(iso_utc(now.timestamp()), "2026-08-08T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
