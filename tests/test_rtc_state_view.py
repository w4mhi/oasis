"""The GPS card's RTC line — what sets the clock at BOOT.

The card has always reported chrony's reference: a GPS refclock or an NTP
server, whichever is disciplining the clock right now. An RTC is neither, so it
could never appear there, and a station that had just fitted one had no way to
see it working.

That is the same blind spot that let two boxes run for weeks on a frozen clock
with every check green — nothing was watching the thing that sets the time
before any network or satellite exists.
"""
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "server"))

# `from routes import system`, NOT `from server.routes import system`. With both
# the suite root and server/ on sys.path, the latter resolves to a SEPARATE
# module object from the one app.py registers — and it only imports at all when
# this file happens to run before whatever else puts server/ on the path, so the
# suite passed alone and failed under discover. Same alias app.py uses; see the
# longer note in test_satellites_horizon_route.py.
from routes import system as SYS  # noqa: E402


def _rtc_stub(name="rtc-pcf8563 10-0051", ok=True, detail="fine",
              battery_mv=None, fh=(False, False), seeded=True):
    m = mock.MagicMock()
    m.sysfs_rtc_name.return_value = name
    m.rtc_is_working.return_value = (ok, detail)
    m.pi5_battery_millivolts.return_value = battery_mv
    m.set_system_clock_at_boot.return_value = seeded
    m.fake_hwclock_state.return_value = fh
    return m


def _state(**kw):
    """Run _rtc_state() against a stubbed common.rtc.

    Patches the ATTRIBUTE on the common package, not just sys.modules. Once
    common.rtc has been imported by anything, `from common import rtc` finds
    common.rtc as an attribute and never consults sys.modules — so a
    sys.modules-only patch is a silent no-op, and these tests passed alone and
    failed under discover depending on which file ran first. Same class of trap
    as the note in test_satellites_horizon_route.py."""
    import common
    stub = _rtc_stub(**kw)
    with mock.patch.object(common, "rtc", stub, create=True), \
            mock.patch.dict("sys.modules", {"common.rtc": stub}):
        return SYS._rtc_state()


class RtcStateTest(unittest.TestCase):
    def test_a_working_rtc_is_green_and_names_itself(self):
        s = _state()
        self.assertEqual(s["level"], "green")
        self.assertIn("rtc-pcf8563", s["text"])

    def test_zero_battery_voltage_does_not_condemn_a_working_rtc(self):
        """CORRECTED 2026-08-12 against a Pi 5 with a healthy coin cell reading
        0 mV. The measurement is tied to the trickle CHARGING configuration, and
        charging is correctly off for a non-rechargeable primary cell — so a
        good cell reads 0 forever. The previous logic called that "no backup
        cell" and turned a working RTC amber, which is worse than saying
        nothing."""
        s = _state(name="rpi-rtc", ok=True, battery_mv=0, seeded=True)
        self.assertEqual(s["level"], "green")
        self.assertNotIn("no backup cell", s["text"])

    def test_holding_across_a_boot_is_stated_because_it_is_provable(self):
        """hctosys is the OUTCOME — the kernel actually took the time from this
        RTC — rather than a proxy for it. It is the strongest claim available,
        and it is about the past, which is the only thing we can support."""
        self.assertIn("held across boot", _state(seeded=True)["text"])
        self.assertNotIn("held across boot", _state(seeded=False)["text"])

    def test_an_rtc_that_lost_power_is_the_real_flat_cell_signal(self):
        """A pre-2020 date, which rtc_is_working() already detects. THIS is what
        a dead cell looks like — not a zero voltage reading."""
        s = _state(name="rpi-rtc", ok=False,
                   detail="RTC reads 0 — it has lost power (no battery, or flat)",
                   fh=(False, False))
        self.assertEqual(s["level"], "red")

    def test_a_healthy_cell_voltage_is_shown(self):
        s = _state(battery_mv=3020)
        self.assertEqual(s["level"], "green")
        self.assertIn("3.02 V", s["text"])   # a non-zero reading IS informative

    def test_no_rtc_but_fake_hwclock_is_amber_and_says_which(self):
        """Not a fault — it is the documented fallback — but not green either:
        fake-hwclock restores the time it saw at shutdown, so the clock is stale
        by however long the box was off."""
        s = _state(name="", ok=False, detail="/dev/rtc0 is not present",
                   fh=(True, True))
        self.assertEqual(s["level"], "amber")
        self.assertIn("fake-hwclock", s["text"])

    def test_nothing_holding_the_clock_at_all_is_red(self):
        """How a station ends up transmitting with a timestamp weeks out of
        date, which is precisely what happened before."""
        s = _state(name="", ok=False, detail="none", fh=(False, False))
        self.assertEqual(s["level"], "red")
        self.assertIn("no RTC", s["text"])

    def test_installed_but_masked_fake_hwclock_does_not_count(self):
        """A package that is `ii` with its unit symlinked to /dev/null is present
        and inert — the same thing as absent for timekeeping, and a different
        thing entirely for the repair."""
        s = _state(name="", ok=False, detail="none", fh=(True, False))
        self.assertEqual(s["level"], "red")

    def test_the_detail_survives_for_the_tooltip(self):
        s = _state(ok=True, detail="RTC present and reads a plausible date")
        self.assertIn("plausible", s["detail"])

    def test_an_unreadable_rtc_module_reports_nothing_rather_than_raising(self):
        """The card must render on a dev laptop with no common.rtc at all — an
        exception here would take the whole /api/system payload down."""
        import common
        broken = mock.MagicMock()
        broken.sysfs_rtc_name.side_effect = OSError("no sysfs")
        with mock.patch.object(common, "rtc", broken, create=True), \
                mock.patch.dict("sys.modules", {"common.rtc": broken}):
            self.assertIsNone(SYS._rtc_state())


class PayloadTest(unittest.TestCase):
    def test_the_system_payload_carries_rtc(self):
        """The UI hides the line when the key is absent, so a missing key is not
        a crash — but it is a silently missing feature, which is worse."""
        import inspect
        src = inspect.getsource(SYS)
        self.assertIn('"rtc":', src)
        self.assertIn("_RTC      = _rtc_state()", src)


if __name__ == "__main__":
    unittest.main()
