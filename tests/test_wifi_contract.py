"""
/api/wifi/* and /api/config on the contract.

Wi-Fi splits cleanly into the two halves docs/api-contract.md §2 distinguishes,
and getting that split right is the whole point of this migration:

  status, scan     PROBES. "Could not scan" is an answer, not a failed request,
                   so they are ok:true with `supported`/`scanned` carrying it.
  connect, forget  ACTIONS. "Join this network" either happened or it did not,
                   so ok:false IS right — what was wrong was serving it with
                   HTTP 200, which made a refused join look like a successful
                   call to anything that checks the status first.

The `scanned` flag matters more than it looks. A host with no sudo grant used to
return an empty `networks` list, which is indistinguishable from "scanned, found
nothing" — one says fix your install, the other says move the antenna.
"""

import json
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

import app as oasis_app                  # noqa: E402
from routes import wifi as wifi_routes   # noqa: E402

_HDR = {"X-OASIS-Request": "1"}

_SCAN_OUT = "*:MH-500:85:WPA2\n :MH-070:60:WPA2\n :Open:20:--\n"


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def call(self, path, method="get", netctl_ok=True, out="", err="",
             platform="linux", helper=True):
        with mock.patch.object(sys, "platform", platform), \
             mock.patch.object(os.path, "exists", return_value=helper), \
             mock.patch.object(wifi_routes, "_netctl",
                               return_value=(netctl_ok, out, err)):
            fn = getattr(self.c, method)
            kw = {"headers": _HDR, "json": {"ssid": "MH-500",
                                            "password": "correcthorse"}} \
                if method == "post" else {}
            return fn(path, **kw)


class StatusTest(_Base):
    _KEYS = {"ok", "supported", "mode", "ssid", "ap_ip", "reason"}

    def test_client_mode(self):
        with mock.patch.object(wifi_routes, "_current_ssid", return_value="MH-500"):
            r = self.call("/api/wifi/status", out="netplan-wlan-MH-500:802-11-wireless\n")
        d = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertIs(d["ok"], True)
        self.assertEqual(d["mode"], "client")
        self.assertEqual(d["ssid"], "MH-500")
        self.assertIsNone(d["reason"])

    def test_ap_mode(self):
        d = self.call("/api/wifi/status",
                      out=f"{wifi_routes.AP_CON_NAME}:802-11-wireless\n").get_json()
        self.assertEqual(d["mode"], "ap")
        self.assertEqual(d["ap_ip"], "10.42.0.1")

    def test_every_outcome_has_one_key_set(self):
        """§5: this returned two keys off-Linux, three with no helper, and five
        when it worked — three shapes for one question."""
        outcomes = [
            self.call("/api/wifi/status", platform="darwin"),
            self.call("/api/wifi/status", helper=False),
            self.call("/api/wifi/status", out="OASIS-AP:802-11-wireless\n"),
        ]
        for r in outcomes:
            self.assertEqual(r.status_code, 200)
            d = r.get_json()
            self.assertIs(d["ok"], True)
            self.assertEqual(set(d), self._KEYS)

    def test_unsupported_hosts_say_why(self):
        self.assertEqual(self.call("/api/wifi/status", platform="darwin")
                         .get_json()["reason"], "not-linux")
        self.assertEqual(self.call("/api/wifi/status", helper=False)
                         .get_json()["reason"], "controls-not-installed")


class ScanTest(_Base):
    _KEYS = {"ok", "supported", "scanned", "networks", "total", "count",
             "truncated", "limit", "reason", "detail"}

    def test_successful_scan(self):
        r = self.call("/api/wifi/scan", out=_SCAN_OUT)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["scanned"], True)
        self.assertEqual(d["count"], 3)
        self.assertEqual([n["ssid"] for n in d["networks"]],
                         ["MH-500", "MH-070", "Open"], "sorted by signal desc")
        self.assertIs(d["networks"][0]["in_use"], True)
        self.assertIs(d["networks"][2]["secure"], False)
        self.assertIsNone(d["reason"])

    def test_no_privilege_is_not_an_empty_network_list(self):
        """The distinction this migration exists for: an empty `networks` used to
        mean BOTH "no sudo grant" and "nothing in range". One says fix your
        install; the other says move the antenna."""
        r = self.call("/api/wifi/scan", netctl_ok=False,
                      err="sudo: a password is required")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True, "§2: the request was handled")
        self.assertIs(d["scanned"], False, "the radio never looked")
        self.assertEqual(d["networks"], [])
        self.assertEqual(d["reason"], "no-privilege")

    def test_scanned_true_with_nothing_in_range(self):
        d = self.call("/api/wifi/scan", out="").get_json()
        self.assertIs(d["scanned"], True, "we DID look — there is just nothing")
        self.assertEqual(d["networks"], [])
        self.assertIsNone(d["reason"])

    def test_every_outcome_has_one_key_set(self):
        for r in (self.call("/api/wifi/scan", platform="darwin"),
                  self.call("/api/wifi/scan", helper=False),
                  self.call("/api/wifi/scan", netctl_ok=False, err="boom"),
                  self.call("/api/wifi/scan", out=_SCAN_OUT)):
            self.assertEqual(r.status_code, 200)
            self.assertEqual(set(r.get_json()), self._KEYS)

    def test_a_failed_scan_names_its_reason(self):
        d = self.call("/api/wifi/scan", netctl_ok=False, err="nmcli exploded").get_json()
        self.assertEqual(d["reason"], "scan-failed")
        self.assertIn("exploded", d["detail"])

    def test_hidden_and_duplicate_ssids_are_dropped(self):
        d = self.call("/api/wifi/scan",
                      out=" ::40:WPA2\n *:Dup:80:WPA2\n :Dup:30:WPA2\n").get_json()
        self.assertEqual([n["ssid"] for n in d["networks"]], ["Dup"])


class ConnectTest(_Base):
    def test_success(self):
        r = self.call("/api/wifi/connect", method="post", out="ok")
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.get_json()["ok"], True)

    def test_a_refused_join_is_not_http_200(self):
        """§2: ok:false is CORRECT here — an action either happened or it did
        not. Serving it with 200 was the defect."""
        r = self.call("/api/wifi/connect", method="post",
                      netctl_ok=False, err="Secrets were required")
        self.assertEqual(r.status_code, 502)
        d = r.get_json()
        self.assertIs(d["ok"], False)
        self.assertEqual(d["code"], "WIFI_CONNECT_FAILED")
        self.assertIn("Secrets", d["error"])

    def test_missing_sudo_grant_is_503_and_says_how_to_fix_it(self):
        r = self.call("/api/wifi/connect", method="post",
                      netctl_ok=False, err="sudo: a terminal is required")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["code"], "WIFI_NO_PRIVILEGE")
        self.assertIn("enable-ap-fallback.py", r.get_json()["error"])

    def test_controls_absent_is_503(self):
        for kw in ({"platform": "darwin"}, {"helper": False}):
            r = self.call("/api/wifi/connect", method="post", **kw)
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.get_json()["code"], "WIFI_CONTROLS_UNAVAILABLE")

    def test_bad_input_is_400_with_a_code(self):
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.object(os.path, "exists", return_value=True):
            r = self.c.post("/api/wifi/connect", headers=_HDR,
                            json={"ssid": "", "password": "correcthorse"})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.get_json()["code"], "SSID_REQUIRED")
            r = self.c.post("/api/wifi/connect", headers=_HDR,
                            json={"ssid": "MH-500", "password": "short"})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.get_json()["code"], "INVALID_PASSWORD")

    def test_csrf_header_still_required(self):
        r = self.c.post("/api/wifi/connect", json={"ssid": "X", "password": "12345678"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "FORBIDDEN")

    def test_the_password_never_comes_back(self):
        r = self.call("/api/wifi/connect", method="post",
                      netctl_ok=False, err="Secrets were required")
        self.assertNotIn("correcthorse", json.dumps(r.get_json()))


class ForgetTest(_Base):
    def test_success(self):
        r = self.call("/api/wifi/forget", method="post", out="ok")
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.get_json()["ok"], True)

    def test_failure_is_502_with_a_code(self):
        r = self.call("/api/wifi/forget", method="post",
                      netctl_ok=False, err="no such connection")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["code"], "WIFI_FORGET_FAILED")

    def test_csrf_header_still_required(self):
        self.assertEqual(self.c.post("/api/wifi/forget").status_code, 403)


class ConfigTest(_Base):
    """/api/config was `jsonify(payload)` — verifiable now, and its alias must
    not be able to drift from it."""

    def test_envelope_and_ports(self):
        d = self.c.get("/api/config").get_json()
        self.assertIs(d["ok"], True)
        self.assertIn("port", d)
        for name in ("flask", "graywolf", "kiwix", "aprs_api", "webssh",
                     "winlink", "openwebrx"):
            self.assertIn(name, d["ports"], "front-end pages read this by name")

    def test_the_alias_serves_exactly_the_same_document(self):
        self.assertEqual(self.c.get("/server-ports.json").get_json(),
                         self.c.get("/api/config").get_json())


if __name__ == "__main__":
    unittest.main()
