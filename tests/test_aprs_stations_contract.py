"""
/api/aprs/stations — fifth endpoint on docs/api-contract.md, and the heaviest.

306 KB per response, ~1100 stations, fetched every 15 s by three pages. It was a
byte pass-through of the graywolf-api daemon, so its shape was whatever GrayWolf
emitted:

  {"ok": true, "count": 1108, "stations": [...]}        no total/truncated/limit
  "last_heard": "2026-08-07 20:08:04.45893926-07:00"    space-separated, ns
                                                        precision, -07:00 offset
  "lat": 47.99666666666667                              17 significant digits

The timestamp is the interesting one. It is not ISO-8601 UTC, which is why
common/js/traffic-list.js's lastHeardEpoch() does three string .replace()s plus a
new Date() FOR EVERY ROW — and that runs over ~1600 rows on every list render.
Normalising server-side deletes that work from every consumer, and gives a model
one timestamp format across the whole API instead of two.
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

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stn(callsign="W4MHI", last_heard="2026-08-07 20:08:04.45893926-07:00", **extra):
    rec = {"callsign": callsign, "last_heard": last_heard,
           "lat": 47.99666666666667, "lon": -119.66116666666667,
           "sym_table": "/", "sym_code": "_", "path": ["TCPIP"], "via": "is",
           "comment": "AmbientCWOP.com", "speed_mph": 0, "course": None,
           "alt_m": None, "is_object": False}
    rec.update(extra)
    return rec


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def get(self, stations, query=""):
        body = json.dumps({"ok": True, "count": len(stations),
                           "stations": stations}).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(body)):
            return self.c.get("/api/aprs/stations" + query)


class EnvelopeTest(_Base):
    def test_envelope_and_bounds(self):
        d = self.get([_stn()]).get_json()
        self.assertIs(d["ok"], True)
        for key in ("stations", "total", "truncated", "limit"):
            self.assertIn(key, d, f"contract §4 requires `{key}`")

    def test_empty_is_success(self):
        d = self.get([]).get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["stations"], [])
        self.assertEqual(d["total"], 0)
        self.assertIs(d["truncated"], False)

    def test_count_is_still_emitted_for_existing_consumers(self):
        # Three pages read `count`; keep it as an alias of `total` rather than
        # breaking them for a rename that buys nothing.
        d = self.get([_stn(), _stn("K7ABC")]).get_json()
        self.assertEqual(d["count"], d["total"])


class TimestampTest(_Base):
    def test_last_heard_is_normalised_to_iso_utc(self):
        d = self.get([_stn(last_heard="2026-08-07 20:08:04.45893926-07:00")]).get_json()
        # -07:00 20:08:04 == 03:08:04Z the next day
        self.assertEqual(d["stations"][0]["last_heard"], "2026-08-08T03:08:04Z")

    def test_already_utc_input_survives(self):
        d = self.get([_stn(last_heard="2026-08-07T20:08:04Z")]).get_json()
        self.assertEqual(d["stations"][0]["last_heard"], "2026-08-07T20:08:04Z")

    def test_unparseable_timestamp_becomes_null_not_a_crash(self):
        for bad in ("", None, "not a date", "0000-00-00"):
            d = self.get([_stn(last_heard=bad)]).get_json()
            self.assertIs(d["ok"], True, bad)
            self.assertIsNone(d["stations"][0]["last_heard"], bad)

    def test_no_offset_or_space_separated_forms_leak_through(self):
        d = self.get([_stn()]).get_json()
        lh = d["stations"][0]["last_heard"]
        self.assertNotIn(" ", lh)
        self.assertTrue(lh.endswith("Z"), lh)
        self.assertNotIn(".", lh, "sub-second precision is noise for APRS")


class FieldShapeTest(_Base):
    def test_every_rendered_field_is_present(self):
        d = self.get([{"callsign": "BARE", "last_heard": "2026-08-07T20:08:04Z"}]).get_json()
        s = d["stations"][0]
        for key in ("callsign", "last_heard", "lat", "lon", "sym_table", "sym_code",
                    "path", "via", "comment", "speed_mph", "course", "alt_m",
                    "is_object"):
            self.assertIn(key, s, f"§5: `{key}` must always be present")

    def test_path_is_always_a_list(self):
        d = self.get([_stn(path=None)]).get_json()
        self.assertEqual(d["stations"][0]["path"], [])

    def test_coordinates_are_rounded_to_metre_precision(self):
        # 47.99666666666667 is 17 significant digits for a position APRS reports
        # to ~10 m. 5 dp is ~1 m and saves ~18 bytes per station over 1100 of them.
        s = self.get([_stn()]).get_json()["stations"][0]
        self.assertEqual(s["lat"], 47.99667)
        self.assertEqual(s["lon"], -119.66117)

    def test_missing_coordinates_stay_null(self):
        s = self.get([_stn(lat=None, lon=None)]).get_json()["stations"][0]
        self.assertIsNone(s["lat"])
        self.assertIsNone(s["lon"])

    def test_station_without_callsign_is_dropped(self):
        d = self.get([{"last_heard": "2026-08-07T20:08:04Z"}, _stn("GOOD")]).get_json()
        self.assertEqual([s["callsign"] for s in d["stations"]], ["GOOD"])


class OrderingAndLimitTest(_Base):
    def test_most_recently_heard_first_with_a_total_order(self):
        rows = [_stn("CCC", "2026-08-07T20:00:00Z"),
                _stn("AAA", "2026-08-07T20:08:00Z"),
                _stn("BBB", "2026-08-07T20:04:00Z")]
        first = [s["callsign"] for s in self.get(rows).get_json()["stations"]]
        self.assertEqual(first, ["AAA", "BBB", "CCC"])
        self.assertEqual(first, [s["callsign"] for s in self.get(rows).get_json()["stations"]])

    def test_ties_break_on_callsign(self):
        rows = [_stn("DDD", "2026-08-07T20:00:00Z"), _stn("BBB", "2026-08-07T20:00:00Z"),
                _stn("CCC", "2026-08-07T20:00:00Z")]
        self.assertEqual([s["callsign"] for s in self.get(rows).get_json()["stations"]],
                         ["BBB", "CCC", "DDD"])

    def test_limit_bounds_the_response(self):
        rows = [_stn(f"K{i:04d}", "2026-08-07T20:00:00Z") for i in range(1500)]
        d = self.get(rows, "?limit=10").get_json()
        self.assertEqual(len(d["stations"]), 10)
        self.assertEqual(d["total"], 1500)
        self.assertIs(d["truncated"], True)

    def test_a_real_station_count_is_not_truncated_by_default(self):
        # ~1100 stations is normal; the map plots all of them.
        rows = [_stn(f"K{i:04d}", "2026-08-07T20:00:00Z") for i in range(1200)]
        d = self.get(rows).get_json()
        self.assertIs(d["truncated"], False)
        self.assertEqual(len(d["stations"]), 1200)


class ErrorShapeTest(_Base):
    def test_daemon_down_is_503_with_a_code(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("refused")
        with mock.patch.object(urllib.request, "urlopen", boom):
            r = self.c.get("/api/aprs/stations")
        self.assertEqual(r.status_code, 503)
        self.assertIs(r.get_json()["ok"], False)
        self.assertEqual(r.get_json()["code"], "APRS_API_UNAVAILABLE")

    def test_daemon_error_body_is_not_dressed_as_success(self):
        body = json.dumps({"ok": False, "error": "history DB missing"}).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(body)):
            r = self.c.get("/api/aprs/stations")
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["code"], "APRS_STATIONS_UNAVAILABLE")

    def test_non_json_body_is_502(self):
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(b"<html>")):
            r = self.c.get("/api/aprs/stations")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["code"], "APRS_API_BAD_RESPONSE")


if __name__ == "__main__":
    unittest.main()
