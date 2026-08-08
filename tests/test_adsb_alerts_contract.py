"""
/api/adsb/alerts — first endpoint migrated to docs/api-contract.md.

Chosen as the pilot because it is the cheapest place to find out the contract is
wrong: it has ZERO front-end consumers, yet it exercises every rule at once —
envelope, list bounds, deterministic ordering, ISO timestamps, null-not-missing,
and the error shape.

The design decision this migration establishes for the whole ADS-B surface: the
**Flask proxy is the contract boundary**, not the adsb-api daemon. The daemon on
127.0.0.1:8086 is an internal implementation detail that ships as its own systemd
unit and is NOT restarted in lockstep with Flask (start-oasis only restarts the
web server). Normalising in the proxy means the contract holds even against a
daemon from an older bundle, and it puts the envelope where a reader can see it.
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


def _alert(ts, icao="a1b2c3", kind="emergency", detail="squawk 7700"):
    return {"ts": ts, "icao": icao, "kind": kind, "detail": detail}


class _Base(unittest.TestCase):
    NOW = 1_754_000_000.0          # fixed clock so age_s is deterministic

    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def get(self, daemon_payload, query="", now=None):
        body = json.dumps(daemon_payload).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(body)), \
             mock.patch("time.time", lambda: now if now is not None else self.NOW):
            return self.c.get("/api/adsb/alerts" + query)


class EnvelopeTest(_Base):
    def test_success_is_ok_true_with_a_named_list(self):
        r = self.get({"alerts": [_alert(self.NOW - 10)]})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIsInstance(d["alerts"], list)
        self.assertNotIn("error", d)          # §2: ok:true never carries `error`

    def test_empty_is_a_successful_answer_not_an_error(self):
        r = self.get({"alerts": []})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["alerts"], [])
        self.assertEqual(d["total"], 0)
        self.assertIs(d["truncated"], False)

    def test_list_bounds_are_always_present(self):
        d = self.get({"alerts": [_alert(self.NOW - 1)]}).get_json()
        for key in ("total", "truncated", "limit"):
            self.assertIn(key, d, f"contract §4 requires `{key}`")


class OrderingAndLimitTest(_Base):
    def test_newest_first_and_deterministic(self):
        # The daemon's ring is oldest-first; a model wants newest-first, and the
        # same input must always come back in the same order.
        payload = {"alerts": [_alert(self.NOW - 300, detail="oldest"),
                              _alert(self.NOW - 200, detail="middle"),
                              _alert(self.NOW - 100, detail="newest")]}
        first = self.get(payload).get_json()["alerts"]
        second = self.get(payload).get_json()["alerts"]
        self.assertEqual([a["detail"] for a in first], ["newest", "middle", "oldest"])
        self.assertEqual(first, second)

    def test_default_limit_bounds_the_response(self):
        d = self.get({"alerts": [_alert(self.NOW - i) for i in range(200)]}).get_json()
        self.assertEqual(len(d["alerts"]), d["limit"])
        self.assertEqual(d["total"], 200)
        self.assertIs(d["truncated"], True)

    def test_truncation_keeps_the_NEWEST(self):
        payload = {"alerts": [_alert(self.NOW - 999, detail="ancient")]
                   + [_alert(self.NOW - i, detail=f"n{i}") for i in range(100)]}
        d = self.get(payload, "?limit=3").get_json()
        self.assertEqual(len(d["alerts"]), 3)
        self.assertNotIn("ancient", [a["detail"] for a in d["alerts"]])

    def test_limit_is_honoured_and_clamped(self):
        many = {"alerts": [_alert(self.NOW - i) for i in range(200)]}
        self.assertEqual(len(self.get(many, "?limit=5").get_json()["alerts"]), 5)
        # Nonsense values must not 500 or return everything.
        for bad in ("?limit=0", "?limit=-3", "?limit=abc", "?limit=99999"):
            d = self.get(many, bad).get_json()
            self.assertIs(d["ok"], True, bad)
            self.assertGreaterEqual(d["limit"], 1, bad)
            self.assertLessEqual(len(d["alerts"]), 200, bad)

    def test_not_truncated_when_everything_fits(self):
        d = self.get({"alerts": [_alert(self.NOW - 1)]}, "?limit=50").get_json()
        self.assertIs(d["truncated"], False)
        self.assertEqual(d["total"], 1)


class FieldShapeTest(_Base):
    def test_timestamps_are_iso_utc_with_age(self):
        d = self.get({"alerts": [_alert(self.NOW - 42)]}).get_json()
        a = d["alerts"][0]
        self.assertRegex(a["time"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(a["age_s"], 42)
        self.assertNotIn("ts", a, "epoch floats are not allowed in a response (§6)")

    def test_every_field_is_present_even_when_unknown(self):
        # The daemon writes icao from ac.get("hex"), which can be None.
        d = self.get({"alerts": [_alert(self.NOW - 5, icao=None)]}).get_json()
        a = d["alerts"][0]
        for key in ("time", "age_s", "icao", "kind", "detail"):
            self.assertIn(key, a, f"§5: `{key}` must always be present")
        self.assertIsNone(a["icao"])

    def test_a_garbled_daemon_record_does_not_break_the_response(self):
        d = self.get({"alerts": [{"kind": "emergency"}, _alert(self.NOW - 1)]}).get_json()
        self.assertIs(d["ok"], True)
        for a in d["alerts"]:
            self.assertIn("icao", a)
            self.assertIn("time", a)


class ErrorShapeTest(_Base):
    def _err(self, exc):
        def boom(req, timeout=None):
            raise exc
        with mock.patch.object(urllib.request, "urlopen", boom):
            return self.c.get("/api/adsb/alerts")

    def test_daemon_down_is_503_with_a_stable_code(self):
        r = self._err(urllib.error.URLError("connection refused"))
        self.assertEqual(r.status_code, 503)
        d = r.get_json()
        self.assertIs(d["ok"], False)
        self.assertEqual(d["code"], "ADSB_API_UNAVAILABLE")
        self.assertIn("error", d)

    def test_never_ok_false_with_http_200(self):
        r = self._err(urllib.error.URLError("nope"))
        self.assertNotEqual(r.status_code, 200)

    def test_unparseable_daemon_body_is_502_not_a_crash(self):
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(b"<html>nope")):
            r = self.c.get("/api/adsb/alerts")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["code"], "ADSB_API_BAD_RESPONSE")


if __name__ == "__main__":
    unittest.main()
