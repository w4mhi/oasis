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


if __name__ == "__main__":
    unittest.main()
