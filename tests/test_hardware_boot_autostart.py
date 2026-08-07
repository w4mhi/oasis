"""Boot reconciler: assigned hardware must come back after a reboot.

The assignment console persists WHAT a dongle is for but historically issued a
plain `systemctl start` with no `enable`, so a reboot left every assigned
service stopped. These cover the pure plan (which units an assignment implies)
and the once-per-boot guard that keeps a Flask restart from being mistaken for
a reboot.
"""
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
from common import hardware


def _inv(devices=None, assignments=None):
    return hardware.Inventory(devices=devices or {}, assignments=assignments or {})


def _dev(id_, kind, **extra):
    return {"id": id_, "kind": kind, **extra}


class BootStartPlanTest(unittest.TestCase):
    def test_no_assignments_starts_nothing(self):
        self.assertEqual(hardware.boot_start_plan(_inv()), [])

    def test_aprs_on_rtl_sdr_starts_the_feed(self):
        inv = _inv(devices={"r": _dev("r", "rtl-sdr")}, assignments={"aprs": "r"})
        self.assertEqual(hardware.boot_start_plan(inv), [hardware.APRS_FEED_UNIT])

    def test_adsb_starts_the_decoder(self):
        # adsb-api follows via the unit drop-ins (Wants=/PartOf=), so the plan
        # only needs the decoder.
        inv = _inv(devices={"r": _dev("r", "rtl-sdr")}, assignments={"adsb": "r"})
        self.assertEqual(hardware.boot_start_plan(inv), ["dump1090-fa"])

    def test_two_dongles_yield_both_in_declaration_order(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr"), "b": _dev("b", "rtl-sdr")},
                   assignments={"adsb": "b", "aprs": "a"})
        # Deterministic order regardless of how the assignments dict was built,
        # so the stagger is reproducible.
        self.assertEqual(hardware.boot_start_plan(inv), [hardware.APRS_FEED_UNIT, "dump1090-fa"])

    def test_aprs_on_a_soundcard_starts_nothing(self):
        # APRS over digirig/dra-pi has no unit of its own — GrayWolf owns the
        # device — so there is nothing for the reconciler to start.
        for kind in ("digirig", "dra-pi"):
            inv = _inv(devices={"d": _dev("d", kind)}, assignments={"aprs": "d"})
            self.assertEqual(hardware.boot_start_plan(inv), [], kind)

    def test_synthetic_unit_is_never_planned(self):
        # "satellites-listen" is an ad-hoc rtl_fm subprocess, not a systemd
        # unit; handing it to systemctl would just error every boot.
        inv = _inv(devices={"r": _dev("r", "rtl-sdr")}, assignments={"satellites": "r"})
        plan = hardware.boot_start_plan(inv)
        self.assertEqual(plan, [])
        self.assertNotIn("satellites-listen", plan)

    def test_advisory_only_assignment_starts_nothing(self):
        # openwebrx is configured in its own admin UI; OASIS has no apply hook.
        inv = _inv(devices={"r": _dev("r", "rtl-sdr")}, assignments={"openwebrx": "r"})
        self.assertEqual(hardware.boot_start_plan(inv), [])

    def test_winlink_starts_direwolf(self):
        inv = _inv(devices={"d": _dev("d", "digirig")}, assignments={"winlink": "d"})
        self.assertEqual(hardware.boot_start_plan(inv), ["pat-direwolf"])

    def test_plan_has_no_duplicates(self):
        inv = _inv(devices={"r": _dev("r", "rtl-sdr"), "d": _dev("d", "digirig")},
                   assignments={"aprs": "r", "adsb": "r", "winlink": "d"})
        plan = hardware.boot_start_plan(inv)
        self.assertEqual(len(plan), len(set(plan)))

    def test_every_planned_unit_is_a_real_systemd_unit(self):
        # Guard against a future SERVICE_UNITS entry leaking a synthetic token.
        inv = _inv(devices={"r": _dev("r", "rtl-sdr"), "d": _dev("d", "digirig")},
                   assignments={s: ("d" if s == "winlink" else "r")
                                for s in hardware.SERVICE_UNITS})
        for unit in hardware.boot_start_plan(inv):
            self.assertNotIn(unit, hardware.SYNTHETIC_UNITS)


class BootIdStampTest(unittest.TestCase):
    """The once-per-boot guard. A reboot must replay the plan; a Flask restart
    within the same boot must not — otherwise a service the operator stopped by
    hand would come back every time the web app restarted.

    The stamp itself is plain file I/O, so this runs everywhere; only reading
    the kernel boot_id is Linux-only (covered separately below).
    """

    def test_stamp_round_trip_distinguishes_reboot_from_restart(self):
        from routes import hardware as HWROUTE
        with tempfile.TemporaryDirectory() as tmp:
            stamp = os.path.join(tmp, ".hw-boot-applied")
            orig = HWROUTE._BOOT_STAMP
            HWROUTE._BOOT_STAMP = stamp
            try:
                self.assertFalse(HWROUTE._boot_already_applied("boot-A"))
                HWROUTE._mark_boot_applied("boot-A")
                # Same boot (a Flask restart) -> already applied, skip.
                self.assertTrue(HWROUTE._boot_already_applied("boot-A"))
                # Different boot_id (an actual reboot) -> replay.
                self.assertFalse(HWROUTE._boot_already_applied("boot-B"))
            finally:
                HWROUTE._BOOT_STAMP = orig

    def test_unreadable_stamp_means_not_applied(self):
        from routes import hardware as HWROUTE
        orig = HWROUTE._BOOT_STAMP
        HWROUTE._BOOT_STAMP = "/nonexistent/dir/.hw-boot-applied"
        try:
            self.assertFalse(HWROUTE._boot_already_applied("boot-A"))
            HWROUTE._mark_boot_applied("boot-A")     # must not raise
        finally:
            HWROUTE._BOOT_STAMP = orig


    def test_boot_id_is_none_off_linux_so_the_reconciler_no_ops(self):
        from routes import hardware as HWROUTE
        got = HWROUTE._current_boot_id()
        if sys.platform == "linux":
            self.assertTrue(got, "Linux must expose a boot_id")
        else:
            # A dev box has no /proc/sys/kernel/random/boot_id; the reconciler
            # must quietly do nothing rather than raise on import.
            self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()
