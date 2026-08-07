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

    def test_all_kinds_whitelisted(self):
        self.assertEqual(
            files_mod.FORM_KINDS,
            {"ics-205", "ics-213", "ics-214", "ics-309", "net-log"})


if __name__ == "__main__":
    unittest.main()
