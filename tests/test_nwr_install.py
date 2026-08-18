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


class MissingPackagesTest(unittest.TestCase):
    """multimon-ng was ALREADY installed on the box where the install failed.

    Step 1 shelled out to apt unconditionally, so an install with nothing to do
    still took the dpkg lock -- and lost it to apt-daily.timer. Presence at any
    version is the test: this feature has no version floor.
    """

    def test_nothing_missing_when_dpkg_has_them_all(self):
        with mock.patch.object(nwr_install, "dpkg_installed_version",
                               return_value="1.3.1+dfsg-1+b1"):
            self.assertEqual(nwr_install.missing_packages(["multimon-ng"]), [])

    def test_any_version_counts_as_installed(self):
        # No floor: an ancient multimon-ng decodes SAME the same way.
        with mock.patch.object(nwr_install, "dpkg_installed_version",
                               return_value="0.9"):
            self.assertEqual(nwr_install.missing_packages(["multimon-ng"]), [])

    def test_reports_only_the_absent_ones(self):
        have = {"multimon-ng": "1.3.1", "sox": None}
        with mock.patch.object(nwr_install, "dpkg_installed_version",
                               side_effect=lambda p: have[p]):
            self.assertEqual(nwr_install.missing_packages(["multimon-ng", "sox"]),
                             ["sox"])


class AptFailureHintTest(unittest.TestCase):
    """A held lock, a 404 and an unknown package name are three problems.

    The old message asserted "Offline? The package ships in the bundle group
    'nwr'." for all three. On raspad it was the lock, the box was online and
    the bundle group existed -- every word of it false.
    """

    LOCK = ("E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by "
            "process 6960 (apt-get)\nE: Unable to acquire the dpkg frontend lock")

    def test_lock_says_lock_and_never_offline(self):
        hint = nwr_install.apt_failure_hint(self.LOCK)
        self.assertIn("lock", hint.lower())
        self.assertNotIn("offline", hint.lower())
        self.assertNotIn("bundle group", hint.lower())

    def test_unknown_package_points_at_the_index(self):
        hint = nwr_install.apt_failure_hint("E: Unable to locate package multimon-ng")
        self.assertIn("apt-get update", hint)

    def test_fetch_failure_is_the_only_one_that_says_offline(self):
        hint = nwr_install.apt_failure_hint(
            "E: Failed to fetch http://deb.debian.org/... 404  Not Found")
        self.assertIn("Offline", hint)
        self.assertIn("nwr", hint)

    def test_no_words_means_no_guess(self):
        self.assertIsNone(nwr_install.apt_failure_hint(""))
        self.assertIsNone(nwr_install.apt_failure_hint(None))
        self.assertIsNone(nwr_install.apt_failure_hint("E: Something new and unseen"))


class RunStepOneTest(unittest.TestCase):
    """Step 1: skip apt when there is nothing to install, and when apt does run
    and fails, print what apt said rather than a guess."""

    def _run_step_one(self, missing, apt_result=None):
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            return apt_result

        printed = []
        with mock.patch.object(nwr_install.M, "apt_packages",
                               return_value=["multimon-ng"]), \
             mock.patch.object(nwr_install, "missing_packages", return_value=missing), \
             mock.patch.object(nwr_install, "dpkg_installed_version",
                               return_value="1.3.1+dfsg-1+b1"), \
             mock.patch.object(nwr_install, "_run", side_effect=fake_run), \
             mock.patch.object(nwr_install, "write_unit"), \
             mock.patch.object(nwr_install, "_ok", side_effect=lambda m: printed.append(m)), \
             mock.patch.object(nwr_install, "_warn", side_effect=lambda m: printed.append(m)), \
             mock.patch.object(nwr_install, "_info", side_effect=lambda m: printed.append(m)):
            result = nwr_install.run(repo_root="/repo")
        return result, calls, printed

    def test_already_installed_never_shells_out_to_apt(self):
        result, calls, printed = self._run_step_one(missing=[])
        for argv, _kw in calls:
            self.assertNotIn("apt-get", argv,
                             f"apt must not run when nothing is missing: {argv}")
        self.assertTrue(result.get("ok"))
        self.assertTrue(any("already installed" in m for m in printed),
                        f"the skip must be reported, not silent: {printed}")

    def test_a_failure_prints_apts_own_words(self):
        result, _calls, printed = self._run_step_one(
            missing=["multimon-ng"],
            apt_result=mock.Mock(returncode=100, stderr=AptFailureHintTest.LOCK))
        joined = "\n".join(printed)
        self.assertIn("Unable to acquire the dpkg frontend lock", joined)
        self.assertNotIn("Offline?", joined)
        self.assertFalse(result.get("ok"))
        self.assertIn("lock", result["error"].lower())

    def test_the_apt_call_captures_stderr(self):
        # _run() captures nothing by default, so without this the failure
        # branch would have no stderr to print.
        _result, calls, _printed = self._run_step_one(
            missing=["multimon-ng"],
            apt_result=mock.Mock(returncode=100, stderr=""))
        apt_calls = [(a, k) for a, k in calls if "apt-get" in a]
        self.assertEqual(len(apt_calls), 1)
        self.assertEqual(apt_calls[0][1].get("stderr"), nwr_install.subprocess.PIPE)
        self.assertTrue(apt_calls[0][1].get("text"))

    def test_only_the_missing_packages_are_installed(self):
        _result, calls, _printed = self._run_step_one(
            missing=["multimon-ng"],
            apt_result=mock.Mock(returncode=0, stderr=""))
        apt_argv = [a for a, _k in calls if "apt-get" in a][0]
        self.assertEqual(apt_argv[-1], "multimon-ng")


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
