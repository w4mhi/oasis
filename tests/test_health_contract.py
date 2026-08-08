"""
/api/health/* — the cluster docs/api-contract.md §2 was written for.

Four of the seven routes used `ok` to mean "the news is good":

    /api/health/probe      ok:false at HTTP 200 when the service wasn't listening
    /api/health/binary     ok: bool(path)
    /api/health/service    ok: active == "active"
    /api/health/file       ok: exists          (inside an opaque jsonify(info))
    /api/health/feed-flow  ok:false at HTTP 200, four times, four key sets

So "the call failed" and "the call worked; the service is stopped" were the same
value — the single ambiguity a small model cannot resolve, on the endpoints most
likely to be asked "is X running?".

The evidence that `ok` was never load-bearing here: EVERY front-end consumer
already branched on `active` / `installed` / `enabled` / `supported`, and not one
read `ok`. It was noise that only a machine reading the contract would trust.

`ok` is now a literal True on every success path, domain state lives in typed
fields, and tests/test_api_contract.py's new computed-`ok` check makes the whole
class unwritable rather than merely fixed here.
"""

import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

import app as oasis_app                      # noqa: E402


def _completed(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc,
                                       stdout=stdout, stderr=stderr)


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()


class ProbeTest(_Base):
    _KEYS = {"ok", "service", "port", "reachable", "status", "detail"}

    def _probe(self, opener):
        with mock.patch.object(urllib.request, "urlopen", opener):
            return self.c.get("/api/health/probe?service=kiwix&port=8081")

    def test_reachable_service(self):
        r = self._probe(lambda *a, **k: mock.MagicMock(
            __enter__=lambda s: mock.Mock(status=200), __exit__=lambda *x: False))
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(set(d), self._KEYS)
        self.assertIs(d["ok"], True)
        self.assertIs(d["reachable"], True)
        self.assertEqual(d["status"], 200)

    def test_nothing_listening_is_a_successful_probe(self):
        """§2: 'nothing is on port 8081' is the ANSWER, not a failure to answer."""
        def boom(*a, **k):
            raise ConnectionRefusedError("connection refused")
        r = self._probe(boom)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True, "the probe ran; the service is simply down")
        self.assertIs(d["reachable"], False)
        self.assertIsNone(d["status"])
        self.assertIn("refused", d["detail"])

    def test_every_outcome_has_the_same_keys(self):
        def boom(*a, **k):
            raise ConnectionRefusedError("nope")
        up = self._probe(lambda *a, **k: mock.MagicMock(
            __enter__=lambda s: mock.Mock(status=200), __exit__=lambda *x: False))
        self.assertEqual(set(up.get_json()), set(self._probe(boom).get_json()))

    def test_http_error_still_counts_as_reachable(self):
        def http_err(*a, **k):
            raise urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
        d = self._probe(http_err).get_json()
        self.assertIs(d["reachable"], True, "404 on / still proves it's listening")
        self.assertEqual(d["status"], 404)

    def test_bad_input_is_a_real_4xx_with_a_code(self):
        for query, code in (("service=kiwix&port=abc", "INVALID_PORT"),
                            ("service=nope&port=80", "UNKNOWN_SERVICE"),
                            ("service=kiwix&port=99999", "PORT_OUT_OF_RANGE")):
            r = self.c.get("/api/health/probe?" + query)
            self.assertEqual(r.status_code, 400, query)
            self.assertIs(r.get_json()["ok"], False, query)
            self.assertEqual(r.get_json()["code"], code)

    def test_no_ok_true_response_carries_an_error_key(self):
        """§2 keeps `error` for failed requests; a successful probe reporting an
        unreachable service uses `detail` — the word /api/adsb/health settled on."""
        def boom(*a, **k):
            raise ConnectionRefusedError("nope")
        self.assertNotIn("error", self._probe(boom).get_json())


class BinaryTest(_Base):
    def test_present_binary(self):
        with mock.patch("shutil.which", return_value="/usr/bin/rtl_test"):
            d = self.c.get("/api/health/binary?name=rtl_test").get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["present"], True)
        self.assertEqual(d["path"], "/usr/bin/rtl_test")

    def test_absent_binary_is_ok_true(self):
        """§2: `ok` was `bool(path)`. Looking and not finding it is a successful
        lookup — this is a question with a `no` answer, not a broken request."""
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("os.path.isfile", return_value=False):
            r = self.c.get("/api/health/binary?name=definitely_absent")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["present"], False)
        self.assertIsNone(d["path"], "§5: null, not the empty string")

    def test_invalid_name_is_400_with_a_code(self):
        r = self.c.get("/api/health/binary?name=../../etc/passwd")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "INVALID_BINARY_NAME")


class ServiceTest(_Base):
    _KEYS = {"ok", "service", "supported", "running", "active", "enabled",
             "installed"}

    def _svc(self, active="active", enabled="enabled"):
        # api_health_service does `import sys as _sys` INSIDE the function, so
        # patching the module attribute on health_routes does nothing — the name
        # is rebound on every call. Patch the real sys module object, which both
        # that local alias and health.py's module-level `sys` point at.
        def fake_run(cmd, **kw):
            return _completed(stdout=(active if cmd[1] == "is-active" else enabled))
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch("subprocess.run", fake_run):
            return self.c.get("/api/health/service?name=kiwix")

    def test_stopped_service_is_a_successful_request(self):
        """The headline §2 case. `ok` was `active == "active"`, so a health probe
        that worked perfectly and found the service stopped reported failure."""
        r = self._svc(active="inactive", enabled="enabled")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True, "the probe SUCCEEDED — it found it stopped")
        self.assertIs(d["running"], False)
        self.assertEqual(d["active"], "inactive")
        self.assertIs(d["installed"], True)

    def test_running_service(self):
        d = self._svc(active="active").get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["running"], True)

    def test_the_fields_every_consumer_reads_are_unchanged(self):
        """index.html, dashboard.html and setup.html branch on these — and on
        none of them is `ok` consulted, which is why this migration is safe."""
        d = self._svc(active="inactive", enabled="disabled").get_json()
        self.assertEqual(set(d), self._KEYS)
        self.assertEqual(d["active"], "inactive")
        self.assertEqual(d["enabled"], "disabled")
        self.assertIs(d["installed"], True)

    def test_absent_unit_is_not_installed(self):
        d = self._svc(active="", enabled="not-found").get_json()
        self.assertIs(d["installed"], False)
        self.assertEqual(d["active"], "unknown")

    def test_non_linux_is_ok_true_with_the_same_key_set(self):
        with mock.patch.object(sys, "platform", "darwin"):
            r = self.c.get("/api/health/service?name=kiwix")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True, "'this host has no systemd' is an answer")
        self.assertIs(d["supported"], False)
        self.assertEqual(set(d), self._KEYS, "§5: not a shorter dict off-Linux")
        self.assertIsNone(d["active"])

    def test_unknown_service_is_400(self):
        r = self.c.get("/api/health/service?name=not-a-real-unit")
        self.assertEqual(r.status_code, 400)
        self.assertIs(r.get_json()["ok"], False)
        self.assertEqual(r.get_json()["code"], "UNKNOWN_SERVICE")


class FileTest(_Base):
    _KEYS = {"ok", "key", "exists", "callsign_set", "password_set"}

    def test_missing_config_is_ok_true(self):
        with mock.patch("os.path.isfile", return_value=False):
            r = self.c.get("/api/health/file?key=pat_config")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True, "'not configured yet' is a fine answer")
        self.assertIs(d["exists"], False)

    def test_the_two_pat_booleans_are_always_present(self):
        """§5: they used to appear only for an existing, PARSEABLE pat_config, so
        'no password', 'not a pat_config' and 'the file is corrupt' were all the
        same absent key. null = we don't know; false = we looked."""
        with mock.patch("os.path.isfile", return_value=False):
            d = self.c.get("/api/health/file?key=pat_config").get_json()
        self.assertEqual(set(d), self._KEYS)
        self.assertIsNone(d["callsign_set"])
        self.assertIsNone(d["password_set"])

    def test_configured_pat_reports_booleans_never_values(self):
        cfg = json.dumps({"mycall": "W4MHI", "secure_login_password": "hunter2"})
        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=cfg)):
            d = self.c.get("/api/health/file?key=pat_config").get_json()
        self.assertIs(d["callsign_set"], True)
        self.assertIs(d["password_set"], True)
        self.assertNotIn("hunter2", json.dumps(d), "secrets never leave as values")
        self.assertNotIn("W4MHI", json.dumps(d))

    def test_corrupt_pat_config_leaves_the_booleans_unknown(self):
        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data="{not json")):
            d = self.c.get("/api/health/file?key=pat_config").get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["exists"], True)
        self.assertIsNone(d["password_set"], "unreadable is unknown, not false")

    def test_non_pat_key_still_carries_every_field(self):
        with mock.patch("os.path.isfile", return_value=True):
            d = self.c.get("/api/health/file?key=rtl_blacklist").get_json()
        self.assertEqual(set(d), self._KEYS)
        self.assertIs(d["exists"], True)
        self.assertIsNone(d["callsign_set"])

    def test_unknown_key_is_400_with_a_code(self):
        r = self.c.get("/api/health/file?key=/etc/shadow")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "UNKNOWN_KEY")


class RtcTest(_Base):
    _KEYS = {"ok", "present", "name", "hctosys", "drift_s"}

    def test_absent_rtc_has_the_same_keys_as_a_present_one(self):
        """§5: this returned a two-key dict when absent and a five-key dict when
        present, so a caller had to know which world it was in before reading."""
        with mock.patch("os.path.isdir", return_value=False):
            d = self.c.get("/api/health/rtc").get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["present"], False)
        self.assertEqual(set(d), self._KEYS)
        self.assertIsNone(d["name"])
        self.assertIsNone(d["drift_s"])


class ZimTest(_Base):
    def test_no_zim_content_is_still_ok_true(self):
        with mock.patch("os.listdir", side_effect=OSError):
            d = self.c.get("/api/health/zim").get_json()
        self.assertIs(d["ok"], True, "an empty library is not a failed request")
        self.assertEqual(d["count"], 0)
        self.assertEqual(d["names"], [])


if __name__ == "__main__":
    unittest.main()
