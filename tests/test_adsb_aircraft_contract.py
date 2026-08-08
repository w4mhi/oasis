"""
/api/adsb/aircraft — second endpoint on docs/api-contract.md.

Unlike /api/adsb/alerts this one has three front-end consumers (index.html,
maps/traffic/map.html, oasis-dashboard/dashboard.html), so it is the first real
test of the per-endpoint discipline: server + every consumer + tests in one
revertable commit.

The interesting part is the `now`/`seen` coupling. The daemon returned a top-level
`now` (dump1090's epoch clock) and a per-aircraft `seen` (seconds since the last
message); all three consumers then computed `now - seen` client-side, via
common/js/traffic-list.js, to get an absolute time. That is an epoch float on the
wire (§6) AND it makes every client depend on a decoder clock it cannot verify.
Doing the subtraction server-side fixes both.

NOT done here: renaming alt_baro -> altitude_ft, gs -> ground_speed_kt etc.
(contract §7). That is a rename across the whole ADS-B surface touching adsb.js,
traffic-list.js and three pages; mixing it into a shape change would make this
commit un-revertable. Tracked in the contract's migration status.
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


NOW = 1_754_000_000.0


def _ac(hex_="a1b2c3", seen=1.0, **extra):
    rec = {"hex": hex_, "seen": seen, "flight": "SWA123 ", "lat": 47.5, "lon": -122.0,
           "alt_baro": 31000, "gs": 430.2, "track": 271, "category": "A3",
           "squawk": "1200"}
    rec.update(extra)
    return rec


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def get(self, aircraft, query="", now=NOW):
        body = json.dumps({"now": now, "aircraft": aircraft}).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(body)):
            return self.c.get("/api/adsb/aircraft" + query)


class EnvelopeTest(_Base):
    def test_envelope_and_list_bounds(self):
        r = self.get([_ac()])
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        for key in ("aircraft", "total", "truncated", "limit", "time"):
            self.assertIn(key, d, f"contract requires `{key}`")
        self.assertNotIn("error", d)

    def test_empty_is_success(self):
        d = self.get([]).get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["aircraft"], [])
        self.assertEqual(d["total"], 0)
        self.assertIs(d["truncated"], False)

    def test_no_epoch_now_on_the_wire(self):
        d = self.get([_ac()]).get_json()
        self.assertNotIn("now", d, "epoch float `now` replaced by ISO `time` (§6)")
        self.assertRegex(d["time"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class TimestampTest(_Base):
    def test_seen_is_resolved_to_an_absolute_iso_time(self):
        d = self.get([_ac(seen=42.0)]).get_json()
        a = d["aircraft"][0]
        self.assertRegex(a["last_seen"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(a["age_s"], 42)
        # NOW - 42 == 1753999958 -> 2025-07-31T22:12:38Z
        self.assertEqual(a["last_seen"], "2025-07-31T22:12:38Z")

    def test_clients_no_longer_need_the_decoder_clock(self):
        """Two polls with different dump1090 clocks but the same `seen` must give
        the same age — the client should never have to reconcile clocks."""
        a1 = self.get([_ac(seen=5.0)], now=NOW).get_json()["aircraft"][0]
        a2 = self.get([_ac(seen=5.0)], now=NOW + 10_000).get_json()["aircraft"][0]
        self.assertEqual(a1["age_s"], a2["age_s"])

    def test_missing_seen_does_not_break_the_record(self):
        d = self.get([_ac(seen=None)]).get_json()
        a = d["aircraft"][0]
        self.assertIn("last_seen", a)
        self.assertIn("age_s", a)


class OrderingAndLimitTest(_Base):
    def test_most_recently_heard_first_and_stable(self):
        payload = [_ac("ccc", seen=30.0), _ac("aaa", seen=1.0), _ac("bbb", seen=10.0)]
        first = [a["hex"] for a in self.get(payload).get_json()["aircraft"]]
        second = [a["hex"] for a in self.get(payload).get_json()["aircraft"]]
        self.assertEqual(first, ["aaa", "bbb", "ccc"])
        self.assertEqual(first, second)

    def test_ties_break_on_hex_so_order_is_total(self):
        payload = [_ac("ddd", seen=5.0), _ac("bbb", seen=5.0), _ac("ccc", seen=5.0)]
        order = [a["hex"] for a in self.get(payload).get_json()["aircraft"]]
        self.assertEqual(order, ["bbb", "ccc", "ddd"])

    def test_limit_is_honoured_and_clamped(self):
        many = [_ac(f"h{i:04d}", seen=float(i)) for i in range(600)]
        d = self.get(many, "?limit=5").get_json()
        self.assertEqual(len(d["aircraft"]), 5)
        self.assertEqual(d["total"], 600)
        self.assertIs(d["truncated"], True)
        for bad in ("?limit=0", "?limit=-1", "?limit=abc"):
            self.assertIs(self.get(many, bad).get_json()["ok"], True, bad)

    def test_default_limit_does_not_truncate_a_realistic_station(self):
        """The map needs every aircraft; the bound exists to stop an unbounded
        response, not to cut normal operation."""
        d = self.get([_ac(f"h{i:04d}", seen=float(i)) for i in range(300)]).get_json()
        self.assertIs(d["truncated"], False)
        self.assertEqual(len(d["aircraft"]), 300)


class FieldPreservationTest(_Base):
    def test_the_fields_the_ui_renders_survive(self):
        d = self.get([_ac()]).get_json()
        a = d["aircraft"][0]
        for key in ("hex", "flight", "lat", "lon", "alt_baro", "gs", "track",
                    "category", "squawk"):
            self.assertIn(key, a, f"front-end reads `{key}` — it must survive")

    def test_unknown_values_are_null_not_missing(self):
        d = self.get([{"hex": "bare", "seen": 1.0}]).get_json()
        a = d["aircraft"][0]
        for key in ("flight", "lat", "lon", "alt_baro", "gs", "track", "category",
                    "squawk"):
            self.assertIn(key, a, f"§5: `{key}` must always be present")
            self.assertIsNone(a[key], f"§5: unknown `{key}` must be null")

    def test_ground_altitude_is_preserved_not_coerced(self):
        # dump1090 reports the string "ground"; the UI relies on it.
        d = self.get([_ac(alt_baro="ground")]).get_json()
        self.assertEqual(d["aircraft"][0]["alt_baro"], "ground")

    def test_flight_callsign_is_trimmed(self):
        # dump1090 pads the callsign to 8 chars; every consumer trimmed it itself.
        self.assertEqual(self.get([_ac()]).get_json()["aircraft"][0]["flight"], "SWA123")

    def test_a_record_without_hex_is_dropped_not_rendered_broken(self):
        d = self.get([{"seen": 1.0}, _ac("good")]).get_json()
        self.assertEqual([a["hex"] for a in d["aircraft"]], ["good"])


class ErrorShapeTest(_Base):
    def test_daemon_down_is_503_with_a_code(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("refused")
        with mock.patch.object(urllib.request, "urlopen", boom):
            r = self.c.get("/api/adsb/aircraft")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["code"], "ADSB_API_UNAVAILABLE")
        self.assertIs(r.get_json()["ok"], False)

    def test_non_json_body_is_502_not_a_200_of_html(self):
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(b"<html>")):
            r = self.c.get("/api/adsb/aircraft")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["code"], "ADSB_API_BAD_RESPONSE")


if __name__ == "__main__":
    unittest.main()
