import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "features", "gps"))
import gps as G


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
        os.path.exists = lambda p: True          # CHRONY_CONF + /dev/pps0 both present
        G._run = lambda *a, **k: Result(1)        # OASIS mark not yet in the conf
        G._sudo_write = fake_write
        try:
            G.configure_chrony()
        finally:
            os.path.exists, G._run, G._sudo_write = orig_exists, orig_run, orig_write

        self.assertIn("refclock SHM 1 refid PPS", captured["content"])
        self.assertNotIn("/dev/pps0", captured["content"])


if __name__ == "__main__":
    unittest.main()
