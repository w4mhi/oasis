"""
The assignment console vs services it cannot start.

Two defects, one visible symptom: satellites showed "assigned, stopped" (amber)
forever and tapping it did nothing.

1. _console_state() asked systemd `is-active satellites-listen`. That unit
   deliberately does not exist — it is a SYNTHETIC token so the exclusivity
   check has something to name — so systemd always answered no and the console
   could not see a running capture even mid-recording. common/hardware.py's own
   note says callers that care must wrap is_active; this one didn't.

2. Even seen correctly, an idle assignment is not "stopped". There is nothing
   for the console to start: the recorder is an ad-hoc rtl_fm subprocess
   launched from the satellites page. `startable: false` lets the client render
   that as READY instead of a failure.

And the matching stop bug: `systemctl stop satellites-listen` exits 0 and does
nothing, so a console STOP on a live capture reported success while rtl_fm kept
the dongle.
"""

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

# Importing the app registers the satellites blueprint, whose routes.py puts
# services/satellites on sys.path — which is what makes the bare `import listen`
# below resolve to the SAME module object the recorder and hardware.py use.
import app as _oasis_app                   # noqa: E402,F401
from common import hardware as HW          # noqa: E402
from routes import hardware as hw_routes   # noqa: E402


class StartableUnitsTest(unittest.TestCase):
    def _inv(self, assignments=None):
        inv = mock.Mock()
        inv.assignments = assignments or {}
        inv.devices = {}
        return inv

    def test_a_synthetic_unit_is_not_startable(self):
        self.assertEqual(HW.service_units(self._inv(), "satellites"),
                         ["satellites-listen"])
        self.assertEqual(HW.startable_units(self._inv(), "satellites"), [],
                         "the console has nothing to launch for satellites")

    def test_a_real_unit_is_startable(self):
        self.assertEqual(HW.startable_units(self._inv(), "adsb"), ["dump1090-fa"])

    def test_aprs_becomes_startable_only_with_an_rtl_sdr(self):
        """aprs is mode-dependent: the RX feed unit only exists when a dongle is
        assigned to it, so `startable` has to be computed per inventory."""
        bare = self._inv({"aprs": None})
        self.assertEqual(HW.startable_units(bare, "aprs"), [])
        with_sdr = self._inv({"aprs": "rtl-1"})
        with_sdr.devices = {"rtl-1": {"kind": "rtl-sdr"}}
        self.assertEqual(HW.startable_units(with_sdr, "aprs"), [HW.APRS_FEED_UNIT])


class ConsoleIsActiveTest(unittest.TestCase):
    def test_the_console_asks_the_recorder_not_systemd(self):
        """The bug: HW._default_is_active("satellites-listen") is asked of
        systemd, which has never heard of it."""
        import listen
        with mock.patch.object(listen, "is_capturing", return_value=True):
            is_active = hw_routes._console_is_active()
            self.assertTrue(is_active("satellites-listen"),
                            "a live capture must read as running")
        with mock.patch.object(listen, "is_capturing", return_value=False):
            self.assertFalse(hw_routes._console_is_active()("satellites-listen"))

    def test_real_units_still_go_to_systemd(self):
        with mock.patch.object(HW, "_default_is_active", return_value=True) as raw:
            self.assertTrue(hw_routes._console_is_active()("dump1090-fa"))
        raw.assert_called_with("dump1090-fa")


class StopSyntheticTest(unittest.TestCase):
    def test_stopping_satellites_stops_the_capture(self):
        import listen
        with mock.patch.object(listen, "stop") as stopped:
            hw_routes._stop_synthetic("satellites-listen")
        stopped.assert_called_once()

    def test_an_unknown_synthetic_unit_is_ignored(self):
        hw_routes._stop_synthetic("not-a-thing")     # must not raise

    def test_a_failing_stop_does_not_propagate(self):
        """Best-effort: the console must not 500 because a feature is half
        installed."""
        import listen
        with mock.patch.object(listen, "stop", side_effect=RuntimeError("boom")):
            hw_routes._stop_synthetic("satellites-listen")


if __name__ == "__main__":
    unittest.main()


class ModuleIdentityTest(unittest.TestCase):
    """The trap this whole fix nearly fell into.

    services/satellites/routes.py imports the recorder as a TOP-LEVEL `listen`
    after putting its own directory on sys.path. Importing the same file as
    `services.satellites.listen` produces a SECOND module object with its own
    `_state` and `_lock` — same source, different globals. Everything type-checks
    and reviews as correct, and then is_capturing() always says False and stop()
    acts on an empty state, on the Pi only.
    """

    def test_the_two_import_paths_are_different_objects(self):
        import listen as bare
        from services.satellites import listen as pkg
        self.assertIsNot(bare, pkg, "if these ever unify, this guard can go")
        self.assertIsNot(bare._state, pkg._state)

    def test_hardware_talks_to_the_recorder_the_routes_use(self):
        """The one that matters: hardware.py must see the capture that
        services/satellites/routes.py started."""
        import listen as recorder           # what routes.py holds
        with mock.patch.object(recorder, "is_capturing", return_value=True):
            self.assertTrue(hw_routes._console_is_active()("satellites-listen"))
        with mock.patch.object(recorder, "stop") as stopped:
            hw_routes._stop_synthetic("satellites-listen")
        stopped.assert_called_once()
