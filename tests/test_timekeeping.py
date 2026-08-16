"""Tests for common/timekeeping.py — can this station tell the time offline?

The chronyc fixtures below are REAL output captured from two live stations on
2026-08-11, not invented strings. Both were booting weeks stale while every
other health check was green, which is the whole reason this module exists.
"""

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common import timekeeping  # noqa: E402

# pi4oasis: a GENUINE 3D fix, chrony taking samples (reach 377) — and rejecting
# them. '#x' is a falseticker: the NMEA lag exceeded the declared error bound,
# so the station had a working GPS and no offline time source at all.
CHRONY_FALSETICKER = """\
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#x GPS                           0   4   377    15   +253ms[ +253ms] +/-  200ms
^+ time.ecansol.net              1  10   377   672  -2845us[-2643us] +/-   51ms
^* daisy.zia.io                  2  10   377   205  +3238us[+3442us] +/-   50ms
"""

# pi5draws: refclocks configured, never a single sample (reach 0). '#?' is
# unreachable — indoors, with no antenna, the GPS never fixed.
CHRONY_UNREACHABLE = """\
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#? GPS                           0   4     0     -     +0ns[   +0ns] +/-    0ns
#? PPS                           0   4     0     -     +0ns[   +0ns] +/-    0ns
^* pool-138-89-14-60.mad.ea>     1   6   377    43   -980us[ -527us] +/-   45ms
"""

# What a station that can actually hold time offline looks like.
CHRONY_TRUSTED = """\
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#* GPS                           0   4   377    11    -12us[  -14us] +/-  400us
#+ PPS                           0   4   377    10     +1us[   +1us] +/-    2us
"""

CHRONY_INTERNET_ONLY = """\
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
^* daisy.zia.io                  2  10   377   205  +3238us[+3442us] +/-   50ms
"""


def _chrony(out):
    return mock.patch.object(timekeeping, "_run",
                             lambda *a, **k: type("R", (), {"stdout": out}))


class GpsIsTrustedOnlyWhenChronyUsesIt(unittest.TestCase):
    def test_a_falseticker_is_not_a_time_source(self):
        # The pi4oasis shape. A GPS delivering samples chrony throws away is
        # not a clock, and counting it would be the exact false comfort that
        # let this station go 26 days stale.
        with _chrony(CHRONY_FALSETICKER):
            ok, detail = timekeeping.gps_disciplined()
        self.assertFalse(ok)
        self.assertIn("not using it", detail)

    def test_an_unreachable_refclock_is_not_a_time_source(self):
        with _chrony(CHRONY_UNREACHABLE):
            ok, _ = timekeeping.gps_disciplined()
        self.assertFalse(ok)

    def test_a_selected_refclock_is_a_time_source(self):
        with _chrony(CHRONY_TRUSTED):
            ok, _ = timekeeping.gps_disciplined()
        self.assertTrue(ok)

    def test_internet_servers_alone_do_not_count(self):
        # '^*' is an internet server. It is the one source guaranteed absent at
        # the moment this matters, so it must never satisfy the check.
        with _chrony(CHRONY_INTERNET_ONLY):
            ok, detail = timekeeping.gps_disciplined()
        self.assertFalse(ok)
        self.assertIn("no local refclock", detail)

    def test_no_chrony_at_all_is_false_not_a_throw(self):
        with _chrony(""):
            ok, _ = timekeeping.gps_disciplined()
        self.assertFalse(ok)

    def test_a_missing_chronyc_binary_is_false_not_a_throw(self):
        def boom(*a, **k):
            raise OSError("No such file or directory: 'chronyc'")
        with mock.patch.object(timekeeping, "_run", boom):
            ok, detail = timekeeping.gps_disciplined()
        self.assertFalse(ok)
        self.assertIn("not installed", detail)


class RtcMustHoldNotMerelyExist(unittest.TestCase):
    """Outcome over proxy.

    An earlier version of these tests asserted the opposite, citing pi5draws as
    the canonical "present but holds nothing, battery_voltage 0" box. pi5draws
    disproved it on 2026-08-16: its kernel log reads

        rpi-rtc: setting system clock to 2026-08-16T15:22:33 UTC

    fourteen seconds BEFORE chrony reached its first NTP server. A correct date
    at that moment cannot have come from anywhere but the RTC, so the cell is
    fine and the voltage reading was a false negative — exactly as
    server/routes/system.py:_rtc_state() had already documented.
    """

    def _rtc(self, working=(True, "present and plausible"), seeded=False, mv=0):
        return (mock.patch.object(timekeeping.rtc, "rtc_is_working",
                                  lambda: working),
                mock.patch.object(timekeeping.rtc, "set_system_clock_at_boot",
                                  lambda: seeded),
                mock.patch.object(timekeeping.rtc, "is_pi5_rtc", lambda: True),
                mock.patch.object(timekeeping.rtc, "pi5_battery_millivolts",
                                  lambda: mv))

    def test_seeding_the_clock_at_boot_proves_it_holds(self):
        a, b, c, d = self._rtc(seeded=True, mv=0)
        with a, b, c, d:
            ok, detail = timekeeping.rtc_holds()
        self.assertTrue(ok)
        self.assertIn("power cycle", detail)

    def test_zero_battery_voltage_alone_never_condemns_a_working_rtc(self):
        # THE regression. 0 mV is normal on a Pi 5 with a non-rechargeable
        # cell, because the reading is tied to trickle charging, which is
        # correctly off for such a cell.
        a, b, c, d = self._rtc(seeded=True, mv=0)
        with a, b, c, d:
            ok, detail = timekeeping.rtc_holds()
        self.assertTrue(ok)
        self.assertNotIn("NO BACKUP CELL", detail)

    def test_present_but_never_seeded_counts_yet_says_so(self):
        a, b, c, d = self._rtc(seeded=False, mv=0)
        with a, b, c, d:
            ok, detail = timekeeping.rtc_holds()
        self.assertTrue(ok)
        self.assertIn("not yet observed", detail)

    def test_an_absent_rtc_does_not_count(self):
        a, b, c, d = self._rtc(working=(False, "/dev/rtc0 is not present"))
        with a, b, c, d:
            ok, _ = timekeeping.rtc_holds()
        self.assertFalse(ok)

    def test_an_rtc_that_lost_power_does_not_count(self):
        # rtc_is_working() already catches the pre-2020 date that a flat cell
        # produces — that is the real "lost power" signal, not a voltage.
        a, b, c, d = self._rtc(
            working=(False, "RTC reads 0 — it has lost power"), seeded=False)
        with a, b, c, d:
            ok, detail = timekeeping.rtc_holds()
        self.assertFalse(ok)
        self.assertIn("lost power", detail)


class FakeHwclockNeedsBothHalves(unittest.TestCase):
    def test_installed_but_masked_is_not_ready(self):
        # pi4oasis: dpkg says `ii`, the unit is symlinked to /dev/null. Reading
        # either half alone reports the opposite of the truth.
        with mock.patch.object(timekeeping.rtc, "fake_hwclock_state", lambda: (True, False)):
            ok, detail = timekeeping.fake_hwclock_ready()
        self.assertFalse(ok)
        self.assertIn("disabled or masked", detail)

    def test_installed_and_enabled_is_ready(self):
        with mock.patch.object(timekeeping.rtc, "fake_hwclock_state", lambda: (True, True)):
            ok, _ = timekeeping.fake_hwclock_ready()
        self.assertTrue(ok)


class TheReportIsTheWholePicture(unittest.TestCase):
    def _report(self, rtc_ok, fake, gps):
        with mock.patch.object(timekeeping, "rtc_holds", lambda: (rtc_ok, "d")), \
             mock.patch.object(timekeeping, "fake_hwclock_ready", lambda: (fake, "d")), \
             mock.patch.object(timekeeping, "gps_disciplined", lambda: (gps, "d")):
            return timekeeping.offline_clock_report()

    def test_no_source_at_all_fails(self):
        # BOTH live stations were in exactly this state while reporting healthy.
        ok, sources, summary = self._report(False, False, False)
        self.assertFalse(ok)
        self.assertEqual(len(sources), 3)
        self.assertIn("NOTHING", summary)

    def test_any_single_source_is_enough(self):
        for combo in ((True, False, False), (False, True, False), (False, False, True)):
            ok, _, _ = self._report(*combo)
            self.assertTrue(ok, combo)

    def test_the_report_names_every_source_either_way(self):
        # The operator needs to see what the OTHER two would need, not just
        # that one happens to be carrying it today.
        for combo in ((True, True, True), (False, False, False)):
            _, sources, _ = self._report(*combo)
            self.assertEqual([n for n, _, _ in sources],
                             ["Hardware RTC", "fake-hwclock", "GPS-disciplined chrony"])


class EnsureFakeHwclockRunsAfterTheRtc(unittest.TestCase):
    def test_a_working_rtc_means_no_install(self):
        calls = []
        with mock.patch.object(timekeeping, "rtc_holds", lambda: (True, "ok")), \
             mock.patch.object(timekeeping, "_run", lambda *a, **k: calls.append(a)):
            self.assertTrue(timekeeping.ensure_fake_hwclock())
        self.assertEqual(calls, [], "must not touch a box whose RTC already holds")

    def test_a_masked_fallback_is_unmasked_not_merely_installed(self):
        # `apt install` alone on pi4oasis would have reported success and
        # changed nothing: the package was already there, the unit masked.
        cmds = []

        def fake_run(cmd, **kw):
            cmds.append(" ".join(cmd))
            return type("R", (), {"stdout": "", "returncode": 0})

        states = iter([(True, False), (True, True)])
        with mock.patch.object(timekeeping, "rtc_holds", lambda: (False, "no rtc")), \
             mock.patch.object(timekeeping.rtc, "fake_hwclock_state", lambda: next(states)), \
             mock.patch.object(timekeeping, "_run", fake_run):
            self.assertTrue(timekeeping.ensure_fake_hwclock())
        joined = " ; ".join(cmds)
        self.assertIn("unmask", joined)
        self.assertIn("enable", joined)
        self.assertNotIn("apt install", joined.replace("apt  install", "apt install"))


if __name__ == "__main__":
    unittest.main()
