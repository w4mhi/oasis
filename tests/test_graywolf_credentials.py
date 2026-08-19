"""GrayWolf Management API credentials: surfaced in setup, erased on reset.

The bug behind these tests: the 2026-08-16 bundle redeploy created a fresh
configuration/, so `_provision_api_config` wrote its empty stub and the operator's
hand-made credentials were gone. `broadcast_available` went false on both Pis and
nothing anywhere said so, because graywolf_client swallows every failure by design
("broadcasting must never break local warning CRUD").

So the properties worth pinning are the ones that keep that silent:
  * the reset DELETES the credential file rather than blanking it
  * an empty password from the form means UNCHANGED, never "clear it"
  * the probe reports booleans, never the credential itself
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from common import config_paths  # noqa: E402
from services.graywolf.common import graywolf  # noqa: E402


class RemovalRecordTest(unittest.TestCase):
    """A credential is not 'kept data' — same rule as Pat's config.json."""

    def test_credential_file_is_listed_for_deletion(self):
        rec = graywolf.removal_record("/srv/oasis")
        self.assertIn(config_paths.graywolf_api_json("/srv/oasis"), rec["files"])

    def test_services_are_still_torn_down(self):
        rec = graywolf.removal_record("/srv/oasis")
        self.assertEqual(rec["services"], [graywolf.SERVICE, graywolf.API_SERVICE])

    def test_no_repo_root_yields_no_file_entry(self):
        # The signature keeps repo_root optional for uniformity; without it there
        # is no configuration/ to point at, and a bogus relative path would be
        # worse than omitting the key.
        self.assertNotIn("files", graywolf.removal_record())

    def test_deleted_not_blanked_so_the_stub_can_be_rewritten(self):
        """The whole point: _provision_api_config returns early when the file
        exists, so blanking would leave a file every future install skips."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = config_paths.graywolf_api_json(tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"username": "op", "password": "s3cret"}, fh)

        # Simulate what common/removal.py does with record["files"].
        for p in graywolf.removal_record(tmp).get("files", []):
            if os.path.isfile(p):
                os.remove(p)
        self.assertFalse(os.path.exists(path), "credential file must be gone")

        # A reinstall now re-stubs it, which a blanked file would have prevented.
        graywolf._provision_api_config(tmp)
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["username"], "")
        self.assertEqual(cfg["password"], "")
        self.assertTrue(cfg["base_url"], "the stub must still carry a base_url")

    def test_provision_never_clobbers_a_real_credential(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = config_paths.graywolf_api_json(tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"base_url": "http://x", "username": "op",
                       "password": "s3cret", "send_path": "both"}, fh)
        graywolf._provision_api_config(tmp)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["password"], "s3cret")


class SetupWriterTest(unittest.TestCase):
    """_setup_write_graywolf owns two fields and must not disturb the rest."""

    def _writer(self, tmp):
        # Import lazily and repoint SUITE_ROOT: the module reaches Flask app
        # globals at import time, so this mirrors what the other setup tests do.
        sys.path.insert(0, os.path.join(_ROOT, "server"))
        import routes.setup as setup_mod
        setup_mod.SUITE_ROOT = tmp
        return setup_mod

    def _seed(self, tmp, **over):
        path = config_paths.graywolf_api_json(tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cfg = {"base_url": "http://127.0.0.1:8080", "username": "",
               "password": "", "send_path": "both"}
        cfg.update(over)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        return path

    def test_writes_both_fields_and_preserves_siblings(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = self._seed(tmp)
        m = self._writer(tmp)
        m._setup_write_graywolf({"graywolf": {"username": "op", "password": "s3cret"}})
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["username"], "op")
        self.assertEqual(cfg["password"], "s3cret")
        # send_path and base_url are owned elsewhere and must survive.
        self.assertEqual(cfg["send_path"], "both")
        self.assertEqual(cfg["base_url"], "http://127.0.0.1:8080")

    def test_empty_password_means_unchanged_not_cleared(self):
        """The form sends '' whenever Change password was not pressed. Treating
        that as a clear would wipe the credential on any unrelated save."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = self._seed(tmp, username="op", password="s3cret")
        m = self._writer(tmp)
        m._setup_write_graywolf({"graywolf": {"username": "op2", "password": ""}})
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["password"], "s3cret", "password must be untouched")
        self.assertEqual(cfg["username"], "op2")

    def test_absent_graywolf_block_is_a_noop(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = self._seed(tmp, username="op", password="s3cret")
        m = self._writer(tmp)
        m._setup_write_graywolf({"station": {"callsign": "W4MHI"}})
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["password"], "s3cret")

    def test_no_file_yet_is_a_noop(self):
        """Writing one here would pre-empt the installer's stub with a file that
        carries no base_url."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(config_paths.config_dir(tmp), exist_ok=True)
        m = self._writer(tmp)
        m._setup_write_graywolf({"graywolf": {"username": "op", "password": "p"}})
        self.assertFalse(os.path.exists(config_paths.graywolf_api_json(tmp)))

    def test_corrupt_file_raises_rather_than_replacing_it(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = config_paths.graywolf_api_json(tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        m = self._writer(tmp)
        with self.assertRaises(ValueError):
            m._setup_write_graywolf({"graywolf": {"username": "op", "password": "p"}})


if __name__ == "__main__":
    unittest.main()
