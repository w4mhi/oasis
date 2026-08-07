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
from unittest import mock

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



class ClaimTimingTest(unittest.TestCase):
    """The claim must be taken when the plan is ACTED ON, not before the settle
    sleep.

    scripts/start-server.sh runs `python server/app.py`, which imports the whole
    route tree -- starting this thread -- and then os.execv()s into gunicorn.
    execv replaces the process image and destroys every thread, so the first
    reconciler is killed a second or two into its settle sleep. If it has
    already written the stamp, the gunicorn worker that imports the module next
    sees the boot as handled and does nothing: observed on pi4oasis, where the
    stamp was written at 21:43:09.889 and gunicorn only started at 21:43:11.
    """

    def setUp(self):
        from routes import hardware as HWROUTE
        self.HW = HWROUTE
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "configuration"), exist_ok=True)
        import json
        with open(os.path.join(self.tmp.name, "configuration", "hardware.json"), "w") as fh:
            json.dump({"devices": [{"id": "r1", "kind": "rtl-sdr"}],
                       "assignments": {"adsb": "r1"}}, fh)
        self.stamp = os.path.join(self.tmp.name, "configuration", ".hw-boot-applied")

    def _patches(self, sleep, started):
        return [
            mock.patch.object(self.HW, "SUITE_ROOT", self.tmp.name),
            mock.patch.object(self.HW, "_BOOT_STAMP", self.stamp),
            mock.patch.object(self.HW, "_current_boot_id", lambda: "boot-A"),
            mock.patch.object(self.HW, "_unit_is_active", lambda u: False),
            mock.patch.object(self.HW, "_systemctl_seq",
                              lambda u, v: started.append(u)),
            mock.patch.object(self.HW.time, "sleep", sleep),
        ]

    def test_process_killed_during_settle_does_not_consume_the_claim(self):
        started = []

        def die_during_settle(_s):
            raise SystemExit("os.execv() replaced the process image")

        ctxs = self._patches(die_during_settle, started)
        for c in ctxs:
            c.start()
        try:
            with self.assertRaises(SystemExit):
                self.HW._boot_reconcile_runner()
        finally:
            for c in ctxs:
                c.stop()

        self.assertEqual(started, [], "nothing started, as expected")
        self.assertFalse(os.path.exists(self.stamp),
                         "a process that died during settle must NOT have claimed the boot")

        # The surviving process must therefore still do the work.
        started2 = []
        ctxs = self._patches(lambda _s: None, started2)
        for c in ctxs:
            c.start()
        try:
            self.HW._boot_reconcile_runner()
        finally:
            for c in ctxs:
                c.stop()
        self.assertEqual(started2, ["dump1090-fa"])
        self.assertTrue(os.path.exists(self.stamp))

    def test_second_run_in_the_same_boot_still_does_nothing(self):
        # The once-per-boot guarantee must survive the timing change.
        for expected in (["dump1090-fa"], []):
            started = []
            ctxs = self._patches(lambda _s: None, started)
            for c in ctxs:
                c.start()
            try:
                self.HW._boot_reconcile_runner()
            finally:
                for c in ctxs:
                    c.stop()
            self.assertEqual(started, expected)


if __name__ == "__main__":
    unittest.main()
