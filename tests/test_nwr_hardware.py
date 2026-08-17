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


class ConsoleIsActiveDegradedPathTest(unittest.TestCase):
    """Exercises the REAL _console_is_active(), not just the pure
    wrapper-chaining shape in WrapperChainTest above. Regression net for a
    review finding: the satellites/nwr try/except used to wrap the
    is_active_wrapper(...) CALL as well as the import, so a present-but-
    broken module was swallowed exactly like an absent one — the service
    would sit on "assigned, stopped" forever even while it held the dongle,
    with no way to notice. The fix narrows each try to the import alone
    (ImportError == "not installed"); a broken-but-present module must now
    raise loudly instead of vanishing into `is_active` silently falling back
    unwrapped.

    services/nwr/common/listener.py does not exist yet (Task 7 creates it),
    so a real broken-module scenario can't be produced for nwr today. A fake
    module is injected straight into sys.modules instead — this drives the
    exact same `from services.nwr.common import listener` / call path the
    real module will run through, so the test stays correct and meaningful
    once Task 7 lands for real (a genuinely broken listener.py would hit this
    same code path and this same assertion)."""

    def _install_fake_listener(self, is_active_wrapper):
        import services.nwr.common as pkg
        fake = types.ModuleType("services.nwr.common.listener")
        fake.is_active_wrapper = is_active_wrapper
        patcher = mock.patch.dict(sys.modules,
                                  {"services.nwr.common.listener": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        # `from X import listener` caches the resolved submodule as an
        # ATTRIBUTE of the parent package (see CPython's _handle_fromlist),
        # independent of sys.modules — patch.dict alone won't undo that, and
        # a leaked fake would silently answer any later test that expects
        # listener to be genuinely absent.
        self.addCleanup(lambda: pkg.__dict__.pop("listener", None))

    def test_absent_listener_still_returns_a_usable_callable_for_both_tokens(self):
        """Today's real state (Task 7 not landed): the callable must come
        back usable and answer BOTH synthetic tokens — satellites-listen via
        the recorder (bare `import listen`), nwr-listen via the base systemd
        check it degrades to — not raise, and not silently drop either
        token."""
        import app as _oasis_app  # noqa: F401  (registers the satellites
                                   # blueprint, putting services/satellites on
                                   # sys.path so the bare `import listen`
                                   # inside _console_is_active() resolves to
                                   # the same module object the recorder uses)
        import listen
        self.assertNotIn("services.nwr.common.listener", sys.modules)

        with mock.patch.object(listen, "is_capturing", return_value=True):
            is_active = hardware_routes._console_is_active()
            self.assertTrue(is_active("satellites-listen"),
                            "a live capture must read as running")
        with mock.patch.object(listen, "is_capturing", return_value=False):
            self.assertFalse(hardware_routes._console_is_active()("satellites-listen"))

        with mock.patch.object(HW, "_default_is_active", return_value=True) as base:
            is_active = hardware_routes._console_is_active()
            self.assertTrue(is_active("nwr-listen"))
        base.assert_called_with("nwr-listen")

    def test_a_broken_installed_module_raises_instead_of_degrading_silently(self):
        def _broken(base):
            raise RuntimeError("boom -- is_active_wrapper itself is broken")
        self._install_fake_listener(_broken)
        with self.assertRaises(RuntimeError):
            hardware_routes._console_is_active()

    def test_a_working_module_is_chained_in_like_satellites(self):
        def _wrapper(base):
            def _w(unit):
                return True if unit == "nwr-listen" else base(unit)
            return _w
        self._install_fake_listener(_wrapper)
        is_active = hardware_routes._console_is_active()
        self.assertTrue(is_active("nwr-listen"))
        self.assertFalse(is_active("dump1090-fa"))


if __name__ == "__main__":
    unittest.main()
