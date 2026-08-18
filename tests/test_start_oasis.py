import importlib.util
import io
import os
import sys
import types
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_spec = importlib.util.spec_from_file_location(
    "start_oasis", os.path.join(_ROOT, "start-oasis.py"))
start_oasis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(start_oasis)


class TailTest(unittest.TestCase):
    def test_returns_last_nonempty_lines(self):
        self.assertEqual(start_oasis._tail("a\n\nb\nc\n\n", 2), "b\nc")

    def test_empty_input(self):
        self.assertEqual(start_oasis._tail("", 3), "")


class WheelFailureMessageTest(unittest.TestCase):
    def test_names_packages_and_stderr_tail(self):
        msg = start_oasis._wheel_failure_message(
            ["psutil"], "ERROR: No matching distribution found for psutil")
        self.assertIn("psutil", msg)
        self.assertIn("No matching distribution", msg)

    def test_includes_platform_and_online_fix_hint(self):
        msg = start_oasis._wheel_failure_message(["psutil"], "")
        self.assertIn(start_oasis._platform_tag(), msg)
        self.assertIn("pip install", msg)


class EnsureVenvSurfacesFailureTest(unittest.TestCase):
    def test_exits_and_reports_when_wheel_install_fails(self):
        # A fresh venv where the offline wheel install fails (e.g. no armv7l
        # psutil wheel in the bundle). ensure_venv must NOT silently return —
        # it must surface the real pip error and exit non-zero.
        fake = types.SimpleNamespace(
            returncode=1, stdout="",
            stderr="ERROR: No matching distribution found for psutil (from versions: none)")
        stderr = io.StringIO()
        with mock.patch.object(start_oasis, "_venv_python", return_value="/x/.venv/bin/python"), \
             mock.patch.object(start_oasis, "_venv_pip", return_value="/x/.venv/bin/pip"), \
             mock.patch.object(start_oasis, "_pkg_importable", return_value=False), \
             mock.patch("subprocess.run", return_value=fake), \
             mock.patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as cm:
                start_oasis.ensure_venv()
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("psutil", stderr.getvalue())


class ServiceControlsGateTest(unittest.TestCase):
    """The gate decides whether start-oasis re-runs enable-service-controls.py,
    and that script does TWO things: it writes the sudoers rule AND it adds the
    user to systemd-journal for the Winlink session console (/api/winlink/log).

    A gate that answers only for the sudoers half skips the journal grant on
    every start, forever. Stock Raspberry Pi OS makes that the DEFAULT case,
    not an edge one: /etc/sudoers.d/010_<user>-nopasswd is NOPASSWD: ALL, so
    the sudoers probe says "granted" on a box that has never run the script."""

    # A `sudo -n -l` listing: the sudo-group blanket entry, which authorises
    # every command on the box and grants passwordless execution of none, plus
    # whatever NOPASSWD entries are passed in.
    def _listing(self, *nopasswd_entries):
        entries = ["    (ALL : ALL) ALL"] + ["    " + e for e in nopasswd_entries]
        return ("User pi may run the following commands on pi5draws:\n"
                + "\n".join(entries) + "\n")

    def _gate(self, listing, journal_members, sudo_rc=0):
        """_service_controls_current() with sudo's policy listing and the
        journal group's membership faked. Patches are on the shared stdlib
        modules, not on the module object, because the gate re-loads
        enable-service-controls.py from source on every call."""
        import grp

        def fake_run(argv, **kwargs):
            return types.SimpleNamespace(returncode=sudo_rc, stdout=listing, stderr="")

        group = types.SimpleNamespace(gr_mem=list(journal_members))
        with mock.patch("sys.platform", "linux"), \
             mock.patch("os.geteuid", return_value=1000), \
             mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("shutil.which", side_effect=lambda b: "/usr/bin/" + b), \
             mock.patch("getpass.getuser", return_value="pi"), \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(grp, "getgrnam", return_value=group):
            os.environ.pop("SUDO_USER", None)
            return start_oasis._service_controls_current()

    def _granted(self):
        return "(root) NOPASSWD: /usr/bin/systemctl restart oasis-nwr.service"

    def test_blanket_nopasswd_sudo_is_not_a_journal_grant(self):
        # The regression: stock Pi OS grants NOPASSWD: ALL, so the sudoers
        # probe alone reports "nothing to do" while the Winlink log console
        # stays permanently empty.
        self.assertFalse(
            self._gate(self._listing("(ALL : ALL) NOPASSWD: ALL"), journal_members=[]),
            "a box with blanket NOPASSWD sudo and no systemd-journal "
            "membership still needs the grant run — Winlink's session log "
            "depends on the SECOND thing that script does")

    def test_both_grants_in_place_skips_the_re_run(self):
        self.assertTrue(self._gate(self._listing(self._granted()),
                                   journal_members=["pi"]))

    def test_a_stale_sudoers_rule_still_reads_stale(self):
        # The live station on 2026-08-17: sudo-group authorisation for
        # everything, a NOPASSWD rule that never heard of oasis-nwr, and a
        # weather watch that does not come back after a reboot.
        stale = "(root) NOPASSWD: /usr/bin/systemctl restart graywolf.service"
        self.assertFalse(self._gate(self._listing(stale), journal_members=["pi"]))

    def test_sudo_refusing_to_list_reads_as_stale(self):
        self.assertFalse(self._gate(self._listing(self._granted()),
                                    journal_members=["pi"], sudo_rc=1))


if __name__ == "__main__":
    unittest.main()
