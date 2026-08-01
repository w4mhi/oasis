#!/usr/bin/env python3
"""Unit tests for the Argon fan curve + hysteresis (features/argon-fan)."""
import importlib.util
import os
import unittest

# argon-fan.py has a hyphen (not a valid module name), so load it by path.
_PATH = os.path.join(os.path.dirname(__file__), "..", "features", "argon-fan", "argon-fan.py")
_spec = importlib.util.spec_from_file_location("argon_fan", _PATH)
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)


class FanCurveTests(unittest.TestCase):
    def test_below_all_thresholds_is_off(self):
        self.assertEqual(A.fan_percent(40.0, current=0), 0)

    def test_ramps_up_at_each_threshold(self):
        # Coming up from cold (current=0), each threshold engages its band.
        self.assertEqual(A.fan_percent(55.0, current=0), 30)
        self.assertEqual(A.fan_percent(60.0, current=30), 55)
        self.assertEqual(A.fan_percent(65.0, current=55), 100)

    def test_highest_matching_band_wins(self):
        self.assertEqual(A.fan_percent(90.0, current=0), 100)

    def test_downward_hysteresis_holds_the_band(self):
        # At 30% and cooling: 54°C is within HYSTERESIS(2) of the 55° band, so it
        # holds 30 rather than dropping to 0 and chattering.
        self.assertEqual(A.fan_percent(54.0, current=30), 30)
        # Only once it falls past 55 - 2 = 53 does it drop to off.
        self.assertEqual(A.fan_percent(52.9, current=30), 0)

    def test_hysteresis_is_directional(self):
        # Same 54°C from cold (current=0) must NOT engage the 55° band.
        self.assertEqual(A.fan_percent(54.0, current=0), 0)

    def test_set_fan_clamps_out_of_range(self):
        class FakeBus:
            def __init__(self): self.writes = []
            def write_byte(self, addr, val): self.writes.append((addr, val))
        b = FakeBus()
        A.set_fan(b, 250)
        A.set_fan(b, -5)
        self.assertEqual([v for _, v in b.writes], [100, 0])
        self.assertTrue(all(a == A.FAN_ADDR for a, _ in b.writes))


if __name__ == "__main__":
    unittest.main()
