#!/usr/bin/env python3
import builtins
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common.gpsd_chrony as G


class ChronyServiceTests(unittest.TestCase):
    def test_restart_services_falls_back_to_chronyd(self):
        calls = []

        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode

        def fake_run(cmd, check=False, capture_output=False, text=False):
            calls.append(cmd)
            if cmd[:3] == ["sudo", "systemctl", "restart"] and cmd[3] == "chrony":
                return Result(3)
            return Result(0)

        orig = G._run
        G._run = fake_run
        try:
            G.restart_services()
        finally:
            G._run = orig

        self.assertTrue(any(cmd[:4] == ["sudo", "systemctl", "restart", "chrony"] for cmd in calls))
        self.assertTrue(any(cmd[:4] == ["sudo", "systemctl", "restart", "chronyd"] for cmd in calls))


class ChronyRefclockTests(unittest.TestCase):
    def test_pps_refclock_uses_gpsd_shm_not_raw_device(self):
        # Regression: `refclock PPS /dev/pps0` is a *fatal* chronyd error when the
        # node is absent, and with pps_ldisc the node only appears after gpsd
        # attaches it — so at boot chrony starts first and dies (works only on a
        # later manual restart). The PPS refclock must go through gpsd's SHM
        # segment, which never fatally fails.
        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode

        captured = {}

        def fake_write(path, content, append=False):
            captured["content"] = content
            return True

        orig_exists, orig_run, orig_write = os.path.exists, G._run, G._sudo_write
        orig_pps = G.pps_pulses_arriving
        os.path.exists = lambda p: True          # CHRONY_CONF present
        G._run = lambda *a, **k: Result(1)        # OASIS mark not yet in the conf
        G._sudo_write = fake_write
        G.pps_pulses_arriving = lambda *a, **k: True   # a pulse IS arriving
        try:
            G.configure_chrony()
        finally:
            os.path.exists, G._run, G._sudo_write = orig_exists, orig_run, orig_write
            G.pps_pulses_arriving = orig_pps

        self.assertIn("refclock SHM 1 refid PPS", captured["content"])
        self.assertNotIn("/dev/pps0", captured["content"])

    def test_no_pps_refclock_when_the_node_exists_but_never_pulses(self):
        # The bug this replaced: the PPS refclock was added on
        # os.path.exists('/dev/pps0'). gpsd attaches a serial line-discipline
        # PPS to the GPS's DCD pin whether or not a wire carries a pulse, so a
        # station with NO PPS hardware got a PPS refclock that never delivered
        # a sample. Found on pi4oasis: node present, lifetime pulse count zero.
        class Result:
            def __init__(self, returncode=0):
                self.returncode = returncode

        captured = {}

        def fake_write(path, content, append=False):
            captured["content"] = content
            return True

        orig_exists, orig_run, orig_write = os.path.exists, G._run, G._sudo_write
        orig_pps = G.pps_pulses_arriving
        os.path.exists = lambda p: True          # the NODE is there...
        G._run = lambda *a, **k: Result(1)
        G._sudo_write = fake_write
        G.pps_pulses_arriving = lambda *a, **k: False   # ...but nothing pulses
        try:
            G.configure_chrony()
        finally:
            os.path.exists, G._run, G._sudo_write = orig_exists, orig_run, orig_write
            G.pps_pulses_arriving = orig_pps

        self.assertNotIn("PPS", captured["content"])
        self.assertIn("refclock SHM 0 refid GPS", captured["content"])


class MutualExclusionTests(unittest.TestCase):
    """features/gps and features/gps-L76X share this module and must not
    silently clobber each other's configured gpsd device."""

    def test_configured_device_parses_devices_line(self):
        orig_exists, orig_open = os.path.exists, builtins.open
        os.path.exists = lambda p: p == G.GPSD_DEFAULT
        builtins.open = lambda path, *a, **k: (
            io.StringIO('START_DAEMON="true"\nDEVICES="/dev/ttyACM0"\n')
            if path == G.GPSD_DEFAULT else orig_open(path, *a, **k)
        )
        try:
            self.assertEqual(G.configured_device(), "/dev/ttyACM0")
        finally:
            os.path.exists, builtins.open = orig_exists, orig_open

    def test_configured_device_none_when_absent(self):
        orig_exists = os.path.exists
        os.path.exists = lambda p: False
        try:
            self.assertIsNone(G.configured_device())
        finally:
            os.path.exists = orig_exists

    def test_check_exclusive_allows_unconfigured_or_same_device(self):
        orig = G.configured_device
        try:
            G.configured_device = lambda: None
            self.assertTrue(G.check_exclusive("/dev/ttyS0"))
            G.configured_device = lambda: "/dev/ttyS0"
            self.assertTrue(G.check_exclusive("/dev/ttyS0"))
        finally:
            G.configured_device = orig

    def test_check_exclusive_blocks_different_device_unless_forced(self):
        orig = G.configured_device
        G.configured_device = lambda: "/dev/ttyACM0"
        try:
            self.assertFalse(G.check_exclusive("/dev/ttyS0"))
            self.assertTrue(G.check_exclusive("/dev/ttyS0", force=True))
        finally:
            G.configured_device = orig


if __name__ == "__main__":
    unittest.main()



class PpsIsProbedNotAssumed(unittest.TestCase):
    """`/dev/pps0` existing proves nothing.

    gpsd attaches a serial line-discipline PPS to the GPS's DCD pin whether or
    not a wire carries a pulse, so the node appears on boxes with no PPS
    hardware. A real station (pi4oasis) was found with the node present and a
    LIFETIME PULSE COUNT OF ZERO — which is why this probes the sequence
    counter rather than the artifact."""

    def _sysfs(self, tmp, values):
        """Build fake /sys/class/pps/<n>/assert files, returning a glob."""
        for name, raw in values.items():
            d = os.path.join(tmp, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "assert"), "w") as fh:
                fh.write(raw)
        return os.path.join(tmp, "*", "assert")

    def test_a_node_that_never_pulsed_is_not_pps(self):
        # Exactly what /sys/class/pps/pps0/assert reads on a box with the node
        # but no signal: timestamp zero, sequence zero, and it never moves.
        with tempfile.TemporaryDirectory() as tmp:
            g = self._sysfs(tmp, {"pps0": "0.000000000#0"})
            self.assertFalse(G.pps_pulses_arriving(window_s=0.01, _glob=g))

    def test_a_stalled_counter_is_not_pps(self):
        # A source that pulsed once long ago and stopped must not count as live.
        with tempfile.TemporaryDirectory() as tmp:
            g = self._sysfs(tmp, {"pps0": "1786504234.000000000#42"})
            self.assertFalse(G.pps_pulses_arriving(window_s=0.01, _glob=g))

    def test_an_advancing_counter_is_pps(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._sysfs(tmp, {"pps0": "1786504234.000000000#42"})
            target = os.path.join(tmp, "pps0", "assert")

            real_sleep = G.time.sleep

            def bump(_):
                with open(target, "w") as fh:
                    fh.write("1786504236.000000000#44")
                real_sleep(0)

            with mock.patch.object(G.time, "sleep", bump):
                self.assertTrue(G.pps_pulses_arriving(_glob=g))

    def test_no_pps_sources_at_all_is_false_not_a_throw(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = os.path.join(tmp, "*", "assert")
            self.assertFalse(G.pps_pulses_arriving(window_s=0.01, _glob=g))

    def test_unreadable_or_garbled_assert_never_claims_pps(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._sysfs(tmp, {"pps0": "not-a-pps-reading"})
            self.assertFalse(G.pps_pulses_arriving(window_s=0.01, _glob=g))


class NmeaDelayIsDeclaredWideEnough(unittest.TestCase):
    def test_the_bound_covers_measured_nmea_lag(self):
        # Measured on a live 3D fix: +215 ms offset, 19 ms jitter. With the old
        # `delay 0.2` (a +/-100 ms bound) chrony marked GPS a falseticker and
        # excluded it, leaving a station with a working GPS and no offline
        # time source at all. The bound must contain the measured lag.
        self.assertGreater(G.NMEA_DELAY_S / 2.0, 0.215)
