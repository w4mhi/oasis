"""The NWR watch's systemd unit and removal record.

v1 shipped removal_record() empty because the capture was an ad-hoc Flask
subprocess with nothing to tear down. Task 4 adds the always-on daemon's
unit; a removal_record() that still said nothing would strand a running
daemon holding a dongle after the feature is uninstalled.

write_unit() deliberately does NOT enable the unit -- see WriteUnitTest.
Nothing starts the daemon at boot until a later task wires nwr into the
conflict engine's boot_start_plan().
"""
import os
import sys
import unittest
from unittest import mock

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


class WriteUnitTest(unittest.TestCase):
    """common/hardware.py's boot_start_plan() decides what starts at boot from
    the persisted device assignments and skips services with no dongle
    assigned -- enabling the unit here would bypass that and start the watch
    on every boot regardless. write_unit() must write the unit and reload
    systemd, and stop there: no `systemctl enable`, matching adsb's
    _write_api_unit (services/adsb/common/adsb.py:359-388), which also stops
    after daemon-reload."""

    def test_writes_the_unit_and_reloads_but_never_enables(self):
        calls = []

        class _FakeProc:
            returncode = 0

            def communicate(self, data):
                calls.append(("tee", data))

        def fake_popen(argv, **kw):
            self.assertEqual(argv, ["sudo", "tee", nwr_install.UNIT_PATH])
            return _FakeProc()

        def fake_run(argv, **kw):
            calls.append(("run", argv))
            return mock.Mock(returncode=0)

        with mock.patch.object(nwr_install.subprocess, "Popen", side_effect=fake_popen), \
             mock.patch.object(nwr_install, "_run", side_effect=fake_run):
            nwr_install.write_unit("/repo/.venv/bin/python3",
                                   "/repo/services/nwr/install.py")

        tee_calls = [c for c in calls if c[0] == "tee"]
        run_calls = [c[1] for c in calls if c[0] == "run"]
        self.assertEqual(len(tee_calls), 1, "the unit must be written exactly once")
        self.assertIn(["sudo", "systemctl", "daemon-reload"], run_calls)
        for argv in run_calls:
            self.assertNotIn("enable", argv,
                             f"write_unit() must not enable the unit: {argv}")


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
