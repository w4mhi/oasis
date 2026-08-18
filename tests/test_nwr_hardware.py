import os
import sys
import types
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "server"))
sys.path.insert(0, _ROOT)
from common import hardware as HW  # noqa: E402
from common import hardware_detect, sdr_rx  # noqa: E402
from routes import hardware as hardware_routes  # noqa: E402


class NwrServiceTest(unittest.TestCase):
    def test_nwr_is_a_known_service(self):
        self.assertIn("nwr", HW.SERVICE_UNITS)
        self.assertEqual(HW.SERVICE_UNITS["nwr"], ["oasis-nwr"])

    def test_nwr_takes_an_rtl_sdr_and_nothing_else(self):
        self.assertEqual(HW.DEVICE_KIND_FOR_SERVICE["nwr"], {"rtl-sdr"})

    def test_nwr_has_no_synthetic_token_any_more(self):
        # The capture moved out of Flask into the oasis-nwr daemon, so there is
        # a real unit for systemctl to answer about. Leaving "nwr-listen" in
        # SYNTHETIC_UNITS would filter the real unit's service out of
        # startable_units()/boot_start_plan() only if the token were still the
        # unit name — but keeping a dead token here is exactly the kind of
        # leftover that makes the next reader wire a wrapper for nothing.
        self.assertNotIn("nwr-listen", HW.SYNTHETIC_UNITS)
        self.assertIn("satellites-listen", HW.SYNTHETIC_UNITS)

    def test_startable_units_is_the_real_daemon(self):
        inv = HW._empty_inventory()
        inv.assignments["nwr"] = "rtl-1"
        self.assertEqual(HW.startable_units(inv, "nwr"), ["oasis-nwr"])

    def test_assigning_a_dongle_puts_the_watch_in_the_boot_plan(self):
        # Assignment IS the trigger: boot_start_plan() already starts real
        # units from persisted assignments, so nwr needs no separate mechanism.
        inv = HW._empty_inventory()
        inv.devices["rtl-1"] = {"id": "rtl-1", "kind": "rtl-sdr"}
        inv.assignments["nwr"] = "rtl-1"
        self.assertIn("oasis-nwr", HW.boot_start_plan(inv))

    def test_an_unassigned_nwr_starts_nothing_at_boot(self):
        self.assertEqual(HW.boot_start_plan(HW._empty_inventory()), [])


class NwrConsumesTheDongleTest(unittest.TestCase):
    """The .46 regression: a running watch was invisible to every arbitration
    surface that did not go through the console's wrapper chain.

    `preconditions.busy` read false while rtl_fm genuinely could not claim the
    tuner, because nothing outside services/nwr knew the claim existed —
    "nwr-listen" was a token only the Flask process could answer, and by then
    the capture had moved to the oasis-nwr daemon in another process entirely.
    A real unit name is answerable by anyone with systemctl."""

    class _Inv:
        def __init__(self, assignments):
            self.assignments = assignments
            self.devices = {}

    def test_a_live_watch_makes_a_co_assigned_dongle_busy(self):
        inv = self._Inv({"nwr": "rtl-1", "satellites": "rtl-1"})
        busy, holder = sdr_rx.dongle_busy(
            inv, lambda u: u == "oasis-nwr", "satellites")
        self.assertTrue(busy)
        self.assertEqual(holder, "nwr")

    def test_the_watch_is_an_sdr_consuming_unit(self):
        # The unassigned/no-inventory fallback in dongle_busy() and
        # can_burn_serial() both read this list; without oasis-nwr in it a
        # running watch reads as "nothing is using the dongle".
        self.assertIn("oasis-nwr", hardware_detect.SDR_CONSUMING_UNITS)

    def test_the_global_fallback_sees_the_watch(self):
        inv = self._Inv({})
        busy, holder = sdr_rx.dongle_busy(
            inv, lambda u: u == "oasis-nwr", "satellites")
        self.assertTrue(busy)
        self.assertEqual(holder, "oasis-nwr")

    def test_the_console_and_the_boot_reconciler_may_actually_start_it(self):
        # Both go through `sudo -n systemctl <verb> oasis-nwr.service`, and
        # _systemctl_seq swallows the refusal — an ungranted unit fails
        # silently, which is the failure mode this whole task exists to end.
        import importlib.util
        path = os.path.join(_ROOT, "scripts", "enable-service-controls.py")
        spec = importlib.util.spec_from_file_location("enable_service_controls", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIn("oasis-nwr", mod.UNITS)
        for verb in ("start", "stop"):
            self.assertIn(verb, mod.ACTIONS)

    def test_burning_a_serial_refuses_while_the_watch_runs(self):
        ok, reason = hardware_detect.can_burn_serial(
            ["00000001"], lambda u: u == "oasis-nwr")
        self.assertFalse(ok)
        self.assertIn("oasis-nwr", reason)


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


class ConsoleIsActiveTest(unittest.TestCase):
    """Exercises the REAL _console_is_active(), not a stand-in.

    Two things are pinned here. First, nwr is now an ORDINARY unit: the
    wrapper chain must not intercept it, because `systemctl is-active
    oasis-nwr` is the honest answer and the wrapper could only ever answer
    about the Flask process, which no longer captures anything.

    Second, the surviving satellites wrapper keeps its narrow guard: the
    try/except covers the IMPORT alone (ImportError == "not installed") and
    the is_active_wrapper(...) CALL sits outside it on purpose. A prior
    version wrapped the call too, so a bug INSIDE a present module was
    swallowed exactly like an absent one — the service sat on "assigned,
    stopped" forever while holding the dongle, with nothing to notice."""

    def _install_fake_listen(self, is_active_wrapper):
        """Stand in for services/satellites/listen.py, which _console_is_active()
        imports BARE (see its docstring — importing it as a package module
        would create a second module object with its own capture state)."""
        fake = types.ModuleType("listen")
        fake.is_active_wrapper = is_active_wrapper
        patcher = mock.patch.dict(sys.modules, {"listen": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_nwr_is_answered_by_systemd_not_by_a_wrapper(self):
        with mock.patch.object(HW, "_default_is_active",
                               side_effect=lambda u: u == "oasis-nwr") as base:
            is_active = hardware_routes._console_is_active()
            self.assertTrue(is_active("oasis-nwr"))
            self.assertFalse(is_active("pat-direwolf"))
        base.assert_any_call("oasis-nwr")

    def test_the_satellites_capture_still_answers_its_own_token(self):
        import app as _oasis_app  # noqa: F401  (registers the satellites
                                   # blueprint, putting services/satellites on
                                   # sys.path so the bare `import listen`
                                   # inside _console_is_active() resolves to
                                   # the same module object the recorder uses)
        import listen

        with mock.patch.object(listen, "is_capturing", return_value=True):
            self.assertTrue(hardware_routes._console_is_active()("satellites-listen"),
                            "a live capture must read as running")
        with mock.patch.object(listen, "is_capturing", return_value=False):
            self.assertFalse(hardware_routes._console_is_active()("satellites-listen"))

    def test_a_broken_installed_module_raises_instead_of_degrading_silently(self):
        def _broken(base):
            raise RuntimeError("boom -- is_active_wrapper itself is broken")
        self._install_fake_listen(_broken)
        with self.assertRaises(RuntimeError):
            hardware_routes._console_is_active()

    def test_a_working_module_is_chained_in(self):
        def _wrapper(base):
            def _w(unit):
                return True if unit == "satellites-listen" else base(unit)
            return _w
        self._install_fake_listen(_wrapper)
        is_active = hardware_routes._console_is_active()
        self.assertTrue(is_active("satellites-listen"))
        self.assertFalse(is_active("dump1090-fa"))


class StopSyntheticTest(unittest.TestCase):
    def test_the_nwr_token_has_no_stopper_left(self):
        # A console STOP on nwr now goes to `systemctl stop oasis-nwr` via the
        # ordinary path; nothing may route it back into Flask.
        self.assertIsNone(hardware_routes._stop_synthetic("nwr-listen"))

    def test_an_unknown_unit_is_a_safe_no_op(self):
        self.assertIsNone(hardware_routes._stop_synthetic("no-such-unit"))


if __name__ == "__main__":
    unittest.main()
