import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "server"))
sys.path.insert(0, _ROOT)
from common import hardware as HW  # noqa: E402
from routes import hardware as hardware_routes  # noqa: E402


class NwrServiceTest(unittest.TestCase):
    def test_nwr_is_a_known_service(self):
        self.assertIn("nwr", HW.SERVICE_UNITS)
        self.assertEqual(HW.SERVICE_UNITS["nwr"], ["nwr-listen"])

    def test_nwr_takes_an_rtl_sdr_and_nothing_else(self):
        self.assertEqual(HW.DEVICE_KIND_FOR_SERVICE["nwr"], {"rtl-sdr"})

    def test_nwr_listen_is_synthetic(self):
        # It must never be handed to systemctl: `systemctl stop nwr-listen`
        # exits fine and does nothing, which is exactly how a running capture
        # would report as stopped while rtl_fm kept the dongle.
        self.assertIn("nwr-listen", HW.SYNTHETIC_UNITS)
        self.assertIn("satellites-listen", HW.SYNTHETIC_UNITS)

    def test_startable_units_excludes_the_synthetic_token(self):
        inv = HW._empty_inventory()
        inv.assignments["nwr"] = "rtl-1"
        self.assertEqual(HW.startable_units(inv, "nwr"), [])


class WrapperChainTest(unittest.TestCase):
    """The two synthetic tokens must BOTH be answerable by one is_active."""

    def test_chained_wrappers_answer_their_own_token_and_delegate(self):
        base = lambda u: u == "dump1090-fa"                      # noqa: E731

        def sat_wrapper(inner):
            def _w(unit):
                return True if unit == "satellites-listen" else inner(unit)
            return _w

        def nwr_wrapper(inner):
            def _w(unit):
                return False if unit == "nwr-listen" else inner(unit)
            return _w

        chained = nwr_wrapper(sat_wrapper(base))
        self.assertTrue(chained("satellites-listen"))
        self.assertFalse(chained("nwr-listen"))
        self.assertTrue(chained("dump1090-fa"))
        self.assertFalse(chained("pat-direwolf"))


class ConsoleRegistrationTest(unittest.TestCase):
    """Guard against the exact gap task 6 shipped: a service wired into the
    conflict engine (SERVICE_UNITS/DEVICE_KIND_FOR_SERVICE) but never added to
    the assignment console's own service list, so the operator can never give
    it a dongle and it fails with no error anywhere.

    The real rule is NOT "every key in DEVICE_KIND_FOR_SERVICE" — openwebrx
    holds an rtl-sdr kind there too, but server/routes/hardware.py deliberately
    leaves it out of _CONSOLE_SERVICES: it has no apply hook (its RTL-SDR is
    picked entirely inside OpenWebRX's own Admin -> SDR profiles UI), so it is
    controlled from its own service card, not the matrix (see the
    _CONSOLE_SERVICES comment there). That is the one documented exception;
    everything else that can hold an rtl-sdr must be console-visible or an
    operator has no way to assign it a dongle."""

    _ADVISORY_ONLY = {"openwebrx"}

    def test_every_rtl_sdr_capable_service_is_console_visible(self):
        rtl_services = {svc for svc, kinds in HW.DEVICE_KIND_FOR_SERVICE.items()
                        if "rtl-sdr" in kinds} - self._ADVISORY_ONLY
        missing = rtl_services - set(hardware_routes._CONSOLE_SERVICES)
        self.assertFalse(missing,
            f"{missing} can hold an rtl-sdr but is absent from _CONSOLE_SERVICES "
            "in server/routes/hardware.py — the operator can never assign it a "
            "dongle and the service fails to start with no visible cause")

    def test_every_console_service_has_a_display_label(self):
        missing = set(hardware_routes._CONSOLE_SERVICES) - set(hardware_routes._SERVICE_DISPLAY)
        self.assertFalse(missing,
            f"{missing} is in _CONSOLE_SERVICES but has no entry in "
            "_SERVICE_DISPLAY, so the console would render its raw id")


if __name__ == "__main__":
    unittest.main()
