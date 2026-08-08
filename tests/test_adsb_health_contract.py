"""
/api/adsb/health — fourth ADS-B endpoint on docs/api-contract.md.

This is the textbook case for contract §2. `ok` meant "the adsb-api daemon
answered", so both consumers read `d.ok === false` as "ADS-B isn't installed" —
using the request-success flag to carry domain state. A caller could not tell
"the call failed" from "the call worked and ADS-B is off", which is precisely the
ambiguity a small model cannot resolve.

After: `ok` means the REQUEST succeeded, and it is true whenever we managed to
look. Whether ADS-B is running and whether data is flowing are typed fields:

    {"ok": true, "running": false, "flowing": null, "detail": "…"}

A health probe that reports "not running" has SUCCEEDED. Returning 503 for a
stopped optional service would be wrong — the service being off is the answer,
not a failure of the endpoint.
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

_FIELDS = ("running", "flowing", "aircraft_count", "positioned",
           "last_json_age_s", "messages_per_min", "samples_per_sec", "detail")


class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status = body, status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def get(self, payload):
        body = json.dumps(payload).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(body)):
            return self.c.get("/api/adsb/health")

    def get_raising(self, exc):
        def boom(req, timeout=None):
            raise exc
        with mock.patch.object(urllib.request, "urlopen", boom):
            return self.c.get("/api/adsb/health")


class HealthyTest(_Base):
    def test_running_and_flowing(self):
        r = self.get({"ok": True, "last_json_age_s": 0.4, "aircraft_count": 12,
                      "positioned": 9, "samples_per_sec": 2.4e6,
                      "messages_per_min": 900, "flowing": True})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], True)
        self.assertIs(d["flowing"], True)
        self.assertEqual(d["aircraft_count"], 12)
        self.assertEqual(d["positioned"], 9)

    def test_every_field_is_always_present(self):
        d = self.get({"ok": True, "aircraft_count": 0, "positioned": 0}).get_json()
        for key in _FIELDS:
            self.assertIn(key, d, f"§5: `{key}` must always be present")

    def test_stats_absent_means_flowing_unknown_not_false(self):
        """dump1090 hasn't written stats.json yet (just started). Unknown is null;
        reporting false would say 'no signal' when we simply don't know."""
        d = self.get({"ok": True, "aircraft_count": 3, "positioned": 2}).get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], True)
        self.assertIsNone(d["flowing"])
        self.assertIsNone(d["samples_per_sec"])


class NotRunningTest(_Base):
    """The §2 rule: a probe that reports bad news has still SUCCEEDED."""

    def test_daemon_unreachable_is_ok_true_running_false(self):
        r = self.get_raising(urllib.error.URLError("connection refused"))
        self.assertEqual(r.status_code, 200, "a stopped optional service is an "
                                             "answer, not a failed request")
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], False)
        self.assertIsNone(d["flowing"])
        self.assertIn("refused", d["detail"])

    def test_not_running_still_carries_every_field(self):
        d = self.get_raising(urllib.error.URLError("nope")).get_json()
        for key in _FIELDS:
            self.assertIn(key, d, f"§5: `{key}` must be present even when down")

    def test_timeout_is_also_not_running_not_an_error(self):
        d = self.get_raising(TimeoutError()).get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], False)

    def test_daemon_reporting_its_own_failure_is_not_running(self):
        d = self.get({"ok": False, "error": "decoder not running"}).get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], False)
        self.assertIn("decoder", d["detail"])

    def test_ok_is_never_false_with_http_200(self):
        for resp in (self.get_raising(urllib.error.URLError("x")),
                     self.get({"ok": False, "error": "y"}),
                     self.get({"ok": True, "aircraft_count": 1})):
            d = resp.get_json()
            if resp.status_code == 200:
                self.assertIsNot(d["ok"], False, "contract §2")


class MalformedTest(_Base):
    def test_non_json_body_is_not_running_rather_than_a_crash(self):
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(b"<html>")):
            r = self.c.get("/api/adsb/health")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], False)


if __name__ == "__main__":
    unittest.main()
