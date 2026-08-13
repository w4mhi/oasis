"""Tests for the client-data backup store (server/routes/files.py):
/api/forms/save + /api/forms/list, plus the ICS-205 back-compat aliases that now
delegate to the same shared helper. SUITE_ROOT is patched to a temp dir so the
tests never write into the repo's static/ tree."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import app as oasis_app          # noqa: E402
from routes import files as files_mod  # noqa: E402


class FormsBackupTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        from oasis_testclient import csrf_client
        self.c = csrf_client(oasis_app.app)
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(files_mod, "SUITE_ROOT", self._tmp.name)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _saved(self, kind, name):
        return os.path.join(self._tmp.name, "static", kind, "saved", name)

    def test_save_and_list_roundtrip(self):
        body = {"kind": "net-log", "filename": "net-log-20260722-1200.json",
                "content": json.dumps({"rows": [{"seq": 1}]})}
        r = self.c.post("/api/forms/save", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertTrue(os.path.isfile(self._saved("net-log", body["filename"])))
        r2 = self.c.get("/api/forms/list?kind=net-log")
        self.assertEqual(r2.status_code, 200)
        names = [f["name"] for f in r2.get_json()["files"]]
        self.assertIn(body["filename"], names)

    def test_unknown_kind_rejected(self):
        r = self.c.post("/api/forms/save", json={
            "kind": "passwords", "filename": "x.json", "content": "{}"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])
        self.assertEqual(self.c.get("/api/forms/list?kind=passwords").status_code, 400)

    def test_bad_filename_rejected(self):
        for fn in ("../evil.json", "sub/dir.json", "notjson.txt", ""):
            r = self.c.post("/api/forms/save", json={
                "kind": "ics-213", "filename": fn, "content": "{}"})
            self.assertEqual(r.status_code, 400, fn)
            self.assertFalse(r.get_json()["ok"], fn)

    def test_ics205_alias_still_works(self):
        name = "ics-205-plan-20260722-1200.json"
        r = self.c.post("/api/save-ics205", json={"filename": name, "content": "{}"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(os.path.isfile(self._saved("ics-205", name)))
        r2 = self.c.get("/api/list-ics205")
        self.assertEqual(r2.status_code, 200)
        self.assertIn(name, [f["name"] for f in r2.get_json()["files"]])

    def test_csv_export_lands_in_the_same_designated_folder(self):
        """The CSV export writes beside the JSON snapshots, not to the operator's
        own machine — same saved/ dir, same filename rules."""
        name = "ics-205-plan-20260813-0930.csv"
        r = self.c.post("/api/forms/save", json={
            "kind": "ics-205", "filename": name, "content": "a,b\n1,2\n"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertTrue(os.path.isfile(self._saved("ics-205", name)))

    def test_ext_filter_separates_the_two_pickers(self):
        """One folder backs both pickers: Restore sees .json, Import CSV sees
        .csv, and neither sees the other's files."""
        js = "ics-309-log-20260813-0930.json"
        csv = "ics-309-log-20260813-0930.csv"
        for fn, body in ((js, "{}"), (csv, "a,b\n")):
            self.assertEqual(self.c.post("/api/forms/save", json={
                "kind": "ics-309", "filename": fn, "content": body}).status_code, 200)

        def names(qs):
            r = self.c.get("/api/forms/list?kind=ics-309" + qs)
            self.assertEqual(r.status_code, 200)
            return [f["name"] for f in r.get_json()["files"]]

        self.assertEqual(names("&ext=csv"), [csv])
        self.assertEqual(names("&ext=json"), [js])
        # Default stays .json so every existing Restore caller is unaffected.
        self.assertEqual(names(""), [js])

    def test_unknown_ext_rejected(self):
        r = self.c.get("/api/forms/list?kind=ics-213&ext=exe")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])
        self.assertEqual(r.get_json()["code"], "UNKNOWN_FORM_EXT")

    def test_still_only_our_extensions_are_writable(self):
        for fn in ("evil.exe", "shell.sh", "page.html", "../escape.csv", "sub/dir.csv"):
            r = self.c.post("/api/forms/save", json={
                "kind": "ics-214", "filename": fn, "content": "x"})
            self.assertEqual(r.status_code, 400, fn)
            self.assertFalse(r.get_json()["ok"], fn)

    def test_all_kinds_whitelisted(self):
        self.assertEqual(
            files_mod.FORM_KINDS,
            {"ics-205", "ics-213", "ics-214", "ics-309", "net-log"})


if __name__ == "__main__":
    unittest.main()


class RefusalStatusTest(unittest.TestCase):
    """Runtime cover for tests/test_api_contract.py's _DYNAMIC_ERROR_STATUS.

    The four form routes forward `(payload, status)` from the shared store, so
    the AST scan cannot read the status literal and its ok:false-with-200 rule
    skips them. These assertions are what that skip is traded for: no refusal
    from any of the four is ever served with HTTP 200.
    """

    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_no_refusal_is_served_with_http_200(self):
        cases = [
            ("post", "/api/forms/save", {"kind": "passwords", "filename": "a.json",
                                         "content": "{}"}),
            ("post", "/api/forms/save", {"kind": "ics-205", "filename": "../esc.json",
                                         "content": "{}"}),
            ("post", "/api/save-ics205", {"filename": "../esc.json", "content": "{}"}),
        ]
        for method, url, body in cases:
            r = getattr(self.c, method)(url, json=body,
                                        headers={"X-OASIS-Request": "1"})
            self.assertNotEqual(r.status_code, 200, url)
            self.assertIs(r.get_json()["ok"], False, url)
            self.assertTrue(r.get_json()["code"], f"{url} needs a stable §3 code")

        for url in ("/api/forms/list?kind=passwords",):
            r = self.c.get(url)
            self.assertNotEqual(r.status_code, 200, url)
            self.assertIs(r.get_json()["ok"], False, url)
            self.assertTrue(r.get_json()["code"], url)

    def test_a_successful_list_carries_the_list_envelope(self):
        d = self.c.get("/api/list-ics205").get_json()
        self.assertIs(d["ok"], True)
        for key in ("files", "total", "count", "truncated"):
            self.assertIn(key, d)
