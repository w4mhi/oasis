"""
/api/adsb/recent — third endpoint on docs/api-contract.md.

The 24 h history feed. It is merged with /api/adsb/aircraft into one aged list by
common/js/traffic-list.js, which is why the shape matters more here than the
endpoint's own consumers suggest: live rows and history rows describing the SAME
kind of thing arrived in different shapes, so the merge had two code paths and a
model reading either got a different schema for "an aircraft".

Unified here:
  ts (epoch)         -> last_seen (ISO) + age_s, matching /api/adsb/aircraft
  flight/squawk ""   -> null (SQL returns '' for unknown; '' is not "unknown")
  category absent    -> null (live rows have it, history rows never did)

Doing this is what lets the `now - seen` fallback leave traffic-list.js.
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


def _row(hex_="a1b2c3", ts=NOW - 60, **extra):
    rec = {"hex": hex_, "flight": "SWA123", "ts": ts, "lat": 47.5, "lon": -122.0,
           "alt_baro": 31000, "gs": 430.0, "track": 271, "squawk": "1200"}
    rec.update(extra)
    return rec


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()
        self.captured = {}

    def get(self, rows, query="?hours=24"):
        body = json.dumps({"now": NOW, "aircraft": rows}).encode()

        def fake(req, timeout=None):
            self.captured["url"] = req if isinstance(req, str) else getattr(req, "full_url", req)
            return _Resp(body)

        with mock.patch.object(urllib.request, "urlopen", fake):
            return self.c.get("/api/adsb/recent" + query)


class EnvelopeTest(_Base):
    def test_envelope_and_bounds(self):
        d = self.get([_row()]).get_json()
        self.assertIs(d["ok"], True)
        for key in ("aircraft", "total", "truncated", "limit", "hours"):
            self.assertIn(key, d, f"contract requires `{key}`")

    def test_empty_history_is_success(self):
        d = self.get([]).get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["aircraft"], [])
        self.assertEqual(d["total"], 0)

    def test_no_epoch_on_the_wire(self):
        d = self.get([_row()]).get_json()
        self.assertNotIn("now", d)
        self.assertNotIn("ts", d["aircraft"][0], "epoch `ts` replaced by ISO (§6)")


class SharedShapeTest(_Base):
    """A history row and a live row describe the same thing; one schema."""

    def test_history_rows_carry_the_same_keys_as_live_rows(self):
        live_keys = {"hex", "flight", "lat", "lon", "alt_baro", "gs", "track",
                     "category", "squawk", "emergency", "baro_rate",
                     "last_seen", "age_s"}
        got = set(self.get([_row()]).get_json()["aircraft"][0])
        self.assertEqual(got, live_keys,
                         "history and live rows must be the same schema — the "
                         "front-end merges them and a model reads them as one type")

    def test_timestamp_is_iso_with_age(self):
        a = self.get([_row(ts=NOW - 3600)]).get_json()["aircraft"][0]
        self.assertRegex(a["last_seen"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(a["last_seen"], "2025-07-31T21:13:20Z")
        self.assertGreaterEqual(a["age_s"], 3600)

    def test_empty_strings_become_null(self):
        # SQL returns '' for an unknown callsign/squawk; '' is not "unknown".
        a = self.get([_row(flight="", squawk="")]).get_json()["aircraft"][0]
        self.assertIsNone(a["flight"])
        self.assertIsNone(a["squawk"])

    def test_category_is_present_as_null(self):
        # Live rows carry it, history rows never did — the key must still exist.
        a = self.get([_row()]).get_json()["aircraft"][0]
        self.assertIn("category", a)
        self.assertIsNone(a["category"])

    def test_row_without_hex_is_dropped(self):
        d = self.get([{"ts": NOW}, _row("good")]).get_json()
        self.assertEqual([a["hex"] for a in d["aircraft"]], ["good"])


class OrderingAndBoundsTest(_Base):
    def test_most_recent_first_with_a_total_order(self):
        rows = [_row("ccc", ts=NOW - 300), _row("aaa", ts=NOW - 100),
                _row("bbb", ts=NOW - 200)]
        first = [a["hex"] for a in self.get(rows).get_json()["aircraft"]]
        self.assertEqual(first, ["aaa", "bbb", "ccc"])
        self.assertEqual(first, [a["hex"] for a in self.get(rows).get_json()["aircraft"]])

    def test_ties_break_on_hex(self):
        rows = [_row("ddd", ts=NOW), _row("bbb", ts=NOW), _row("ccc", ts=NOW)]
        self.assertEqual([a["hex"] for a in self.get(rows).get_json()["aircraft"]],
                         ["bbb", "ccc", "ddd"])

    def test_limit_bounds_the_history(self):
        rows = [_row(f"h{i:04d}", ts=NOW - i) for i in range(900)]
        d = self.get(rows, "?hours=24&limit=10").get_json()
        self.assertEqual(len(d["aircraft"]), 10)
        self.assertEqual(d["total"], 900)
        self.assertIs(d["truncated"], True)


class HoursParamTest(_Base):
    def test_hours_is_echoed_back(self):
        self.assertEqual(self.get([_row()], "?hours=6").get_json()["hours"], 6.0)

    def test_bad_hours_degrades_to_the_default(self):
        for bad in ("?hours=abc", "?hours=-5", "?hours=0", ""):
            d = self.get([_row()], bad).get_json()
            self.assertIs(d["ok"], True, bad)
            self.assertGreater(d["hours"], 0, bad)

    def test_absurd_hours_is_clamped(self):
        # An unbounded window is an unbounded DB scan on a Pi.
        d = self.get([_row()], "?hours=999999").get_json()
        self.assertLessEqual(d["hours"], 24 * 30)


class ErrorShapeTest(_Base):
    def test_daemon_down_is_503(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("refused")
        with mock.patch.object(urllib.request, "urlopen", boom):
            r = self.c.get("/api/adsb/recent?hours=24")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["code"], "ADSB_API_UNAVAILABLE")

    def test_daemon_error_body_is_not_dressed_as_success(self):
        # The daemon answers {"ok": false, "error": …} with 503 when the DB is
        # unreadable; that must not become ok:true with an empty list.
        body = json.dumps({"ok": False, "error": "no such table"}).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(body)):
            r = self.c.get("/api/adsb/recent?hours=24")
        self.assertNotEqual(r.status_code, 200)
        self.assertIs(r.get_json()["ok"], False)
        self.assertEqual(r.get_json()["code"], "ADSB_HISTORY_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
