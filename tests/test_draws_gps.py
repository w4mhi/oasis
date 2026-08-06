import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "draws_gps",
    os.path.join(os.path.dirname(_HERE), "features", "draws-gps", "draws_gps.py"),
)
draws_gps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(draws_gps)

import importlib.util as _ilu
_cli_spec = _ilu.spec_from_file_location(
    "install_draws_gps",
    os.path.join(os.path.dirname(_HERE), "features", "draws-gps", "install-draws-gps.py"),
)


class RemovalRecordTest(unittest.TestCase):
    def test_strips_overlay_line_and_flags_reboot(self):
        rec = draws_gps.removal_record()
        self.assertEqual(rec["config_lines"], ["dtoverlay=draws"])
        self.assertTrue(rec["requires_reboot"])
        self.assertTrue(any("shared" in n for n in rec["notes"]))


class ExitCodeTest(unittest.TestCase):
    def test_reboot_when_overlay_changed(self):
        self.assertEqual(draws_gps.decide_exit_code(True, False), 10)

    def test_reboot_when_device_absent(self):
        self.assertEqual(draws_gps.decide_exit_code(False, False), 10)

    def test_zero_when_present_and_unchanged(self):
        self.assertEqual(draws_gps.decide_exit_code(False, True), 0)


class ParserTest(unittest.TestCase):
    def _load(self):
        mod = _ilu.module_from_spec(_cli_spec)
        _cli_spec.loader.exec_module(mod)
        return mod

    def test_defaults(self):
        args = self._load().build_parser().parse_args([])
        self.assertEqual(args.device, "/dev/ttySC0")
        self.assertFalse(args.force)
        self.assertFalse(args.check)
        self.assertFalse(args.dry_run)

    def test_flags(self):
        args = self._load().build_parser().parse_args(
            ["--device", "/dev/ttySC1", "--force", "--check", "--dry-run"])
        self.assertEqual(args.device, "/dev/ttySC1")
        self.assertTrue(args.force and args.check and args.dry_run)

    def test_baud_defaults_to_the_bench_confirmed_rate(self):
        args = self._load().build_parser().parse_args([])
        self.assertEqual(args.baud, 9600)

    def test_baud_is_overridable(self):
        args = self._load().build_parser().parse_args(["--baud", "38400"])
        self.assertEqual(args.baud, 38400)


class NoDataHintTest(unittest.TestCase):
    def test_hint_is_draws_specific(self):
        # The DRAWS failure mode is the overlay / SC16IS752 bind, NOT the
        # TX/RX/5V/GND wiring gps-L76X warns about (there is no wiring to get
        # wrong — the GPS is on the board).
        hint = draws_gps.NO_DATA_HINT
        self.assertIn("overlay", hint.lower())
        self.assertNotIn("TX->RxD", hint)


class CheckVerifiesTest(unittest.TestCase):
    """--check must answer 'is the GPS talking, and does it have a fix?' —
    not just print four booleans about device nodes."""

    def _load_patched(self):
        mod = _ilu.module_from_spec(_cli_spec)
        _cli_spec.loader.exec_module(mod)
        return mod

    def _run_check(self, mod, argv):
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.object(mod.draws, "overlay_available", return_value=True), \
             mock.patch.object(mod.draws, "config_path", return_value=None), \
             mock.patch.object(mod.draws, "gps_device_present", return_value=True), \
             mock.patch.object(mod.draws, "pps_present", return_value=True):
            return mod.main(argv)

    def test_check_reads_nmea_from_the_device(self):
        mod = self._load_patched()
        with mock.patch.object(mod.nmea, "verify") as verify, \
             mock.patch.object(mod.gpsd_chrony, "verify"), \
             mock.patch.object(mod.gpsd_chrony, "configured_device", return_value=None):
            self._run_check(mod, ["--check"])
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[0], "/dev/ttySC0")
        self.assertEqual(verify.call_args.kwargs["baud"], 9600)

    def test_check_reports_the_gpsd_and_chrony_state(self):
        mod = self._load_patched()
        with mock.patch.object(mod.nmea, "verify"), \
             mock.patch.object(mod.gpsd_chrony, "verify") as gverify, \
             mock.patch.object(mod.gpsd_chrony, "configured_device", return_value=None):
            self._run_check(mod, ["--check"])
        gverify.assert_called_once()

    def test_check_surfaces_a_gpsd_pointed_at_another_gps_feature(self):
        # gpsd left on the USB dongle from features/gps: the DRAWS GPS then
        # looks dead for a reason that has nothing to do with the DRAWS.
        mod = self._load_patched()
        with mock.patch.object(mod.nmea, "verify") as verify, \
             mock.patch.object(mod.gpsd_chrony, "verify"), \
             mock.patch.object(mod.gpsd_chrony, "configured_device",
                               return_value="/dev/ttyUSB0"):
            self._run_check(mod, ["--check"])
        self.assertEqual(verify.call_args.kwargs["configured_device"], "/dev/ttyUSB0")

    def test_check_still_exits_zero(self):
        mod = self._load_patched()
        with mock.patch.object(mod.nmea, "verify"), \
             mock.patch.object(mod.gpsd_chrony, "verify"), \
             mock.patch.object(mod.gpsd_chrony, "configured_device", return_value=None):
            self.assertEqual(self._run_check(mod, ["--check"]), 0)


class InstallVerifiesTest(unittest.TestCase):
    """The install path gets the same answer when the device is already live —
    exit 0 must mean 'verified', not 'the node exists'."""

    def _load(self):
        mod = _ilu.module_from_spec(_cli_spec)
        _cli_spec.loader.exec_module(mod)
        return mod

    def _install(self, mod, device_present):
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.object(mod.draws, "overlay_available", return_value=True), \
             mock.patch.object(mod.draws, "ensure_overlay", return_value=False), \
             mock.patch.object(mod.draws, "gps_device_present",
                               return_value=device_present), \
             mock.patch.object(mod.gpsd_chrony, "check_exclusive", return_value=True), \
             mock.patch.object(mod.gpsd_chrony, "install_packages"), \
             mock.patch.object(mod.gpsd_chrony, "configure_gpsd"), \
             mock.patch.object(mod.gpsd_chrony, "configure_chrony"), \
             mock.patch.object(mod.gpsd_chrony, "restart_services"), \
             mock.patch.object(mod.gpsd_chrony, "configured_device", return_value=None), \
             mock.patch.object(mod.gpsd_chrony, "verify") as gverify, \
             mock.patch.object(mod.nmea, "verify") as verify:
            code = mod.main([])
        return code, verify, gverify

    def test_verifies_when_the_device_is_already_live(self):
        code, verify, gverify = self._install(self._load(), device_present=True)
        self.assertEqual(code, 0)
        verify.assert_called_once()
        gverify.assert_called_once()

    def test_skips_verification_when_a_reboot_is_still_needed(self):
        # Nothing to read from a device that has not enumerated yet.
        code, verify, _ = self._install(self._load(), device_present=False)
        self.assertEqual(code, 10)
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
