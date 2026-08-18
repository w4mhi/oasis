"""The NWR watch's systemd unit, boot enable, and removal record.

v1 shipped removal_record() empty because the capture was an ad-hoc Flask
subprocess with nothing to tear down. Task 4 adds the always-on daemon's
unit; a removal_record() that still said nothing would strand a running
daemon holding a dongle after the feature is uninstalled.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import daemon  # noqa: E402
from services.nwr.common import nwr_install  # noqa: E402


class UnitTextTest(unittest.TestCase):
    def setUp(self):
        self.text = nwr_install.unit_text("/repo/.venv/bin/python3",
                                          "/repo/services/nwr/install.py")

    def test_execstart_serves(self):
        self.assertIn(
            "ExecStart=/repo/.venv/bin/python3 /repo/services/nwr/install.py --serve",
            self.text)

    def test_restarts_on_failure(self):
        self.assertIn("Restart=on-failure", self.text)

    def test_starts_after_the_network(self):
        self.assertIn("After=network.target", self.text)

    def test_wanted_by_multi_user(self):
        self.assertIn("WantedBy=multi-user.target", self.text)

    def test_no_partof(self):
        # Unlike adsb-api, which is bound to dump1090-fa's decoder unit, the
        # watch has no separate decoder to follow.
        self.assertNotIn("PartOf=", self.text)


class ServiceNameTest(unittest.TestCase):
    def test_service_matches_the_daemon(self):
        # SERVICE is canonical in daemon.py; nwr_install must not redefine it.
        self.assertEqual(nwr_install.SERVICE, daemon.SERVICE)
        self.assertEqual(nwr_install.SERVICE, "oasis-nwr")

    def test_unit_path_uses_the_service_name(self):
        self.assertEqual(nwr_install.UNIT_PATH,
                         f"/etc/systemd/system/{daemon.SERVICE}.service")


class RemovalRecordTest(unittest.TestCase):
    def test_names_the_watch_service(self):
        rec = nwr_install.removal_record()
        self.assertIn("oasis-nwr", rec.get("services", []))

    def test_names_the_unit_file(self):
        rec = nwr_install.removal_record()
        self.assertIn(nwr_install.UNIT_PATH, rec.get("files", []))

    def test_accepts_a_repo_root_argument(self):
        # setup_registry's _removal_record() always calls removal_record(repo_root).
        rec = nwr_install.removal_record("/repo")
        self.assertIn("oasis-nwr", rec.get("services", []))


if __name__ == "__main__":
    unittest.main()
