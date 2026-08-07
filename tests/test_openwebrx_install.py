"""Regression tests for the OpenWebRX+ installer's apt handling.

Both cases here come from one real failure on the bench Pi (2026-08-07): the
OpenWebRX+ repo's dump1090-fa-minimal recommends collides with FlightAware's
dump1090-fa over /usr/bin/dump1090-fa, apt exited 1, and the installer's _fail()
aborted before set_default_disabled() — leaving OpenWebRX installed and ENABLED
at boot, where it seizes the RTL-SDR from GrayWolf / the APRS feed / ADS-B.
"""

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from services.openwebrx.common import openwebrx  # noqa: E402


class _Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class AptArgvTest(unittest.TestCase):
    def test_vetoes_minimal_when_blessed_dump1090_installed(self):
        with mock.patch.object(openwebrx, "dpkg_installed_version",
                               side_effect=lambda p: "11.1" if p == "dump1090-fa" else None):
            argv = openwebrx._apt_install_argv()
        # apt's trailing '-' means "do not install this package"
        self.assertIn("dump1090-fa-minimal-", argv)
        self.assertIn("openwebrx", argv)

    def test_leaves_minimal_alone_without_dump1090_fa(self):
        # No ADS-B feature on this box: nothing owns /usr/bin/dump1090-fa, so the
        # recommends can install normally and give ORX its ADS-B mode.
        with mock.patch.object(openwebrx, "dpkg_installed_version", return_value=None):
            argv = openwebrx._apt_install_argv()
        self.assertNotIn("dump1090-fa-minimal-", argv)
        self.assertIn("openwebrx", argv)


class InstallFailureToleranceTest(unittest.TestCase):
    def test_failed_optional_recommends_does_not_abort(self):
        installed = {"openwebrx": "1.2.120"}
        with mock.patch.object(openwebrx, "_run", return_value=_Result(returncode=1)), \
             mock.patch.object(openwebrx, "dpkg_installed_version",
                               side_effect=installed.get):
            openwebrx.install()   # must return, NOT sys.exit — the caller still
                                  # has to run set_default_disabled()

    def test_missing_package_still_fails(self):
        with mock.patch.object(openwebrx, "_run", return_value=_Result(returncode=1)), \
             mock.patch.object(openwebrx, "dpkg_installed_version", return_value=None):
            with self.assertRaises(SystemExit):
                openwebrx.install()


class DefaultDisabledTest(unittest.TestCase):
    def test_warns_when_disable_did_not_take(self):
        with mock.patch.object(openwebrx, "_run", return_value=_Result(stdout="enabled\n")), \
             mock.patch.object(openwebrx, "_warn") as warn, \
             mock.patch.object(openwebrx, "_ok") as ok:
            openwebrx.set_default_disabled()
        self.assertTrue(warn.called)
        self.assertFalse(ok.called)

    def test_reports_ok_once_disabled(self):
        with mock.patch.object(openwebrx, "_run", return_value=_Result(stdout="disabled\n")), \
             mock.patch.object(openwebrx, "_warn") as warn, \
             mock.patch.object(openwebrx, "_ok") as ok:
            openwebrx.set_default_disabled()
        self.assertTrue(ok.called)
        self.assertFalse(warn.called)


if __name__ == "__main__":
    unittest.main()
