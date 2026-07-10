# tests/test_geek_pi_case.py — plain unittest (no pytest; offline wheel set is flask-only).
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from common import geek_pi_case as G


class FanTests(unittest.TestCase):
    def test_parse_cpu_temp_millidegrees(self):
        self.assertAlmostEqual(G.parse_cpu_temp("52300\n"), 52.3)

    def test_fan_on_above_high_threshold(self):
        self.assertTrue(G.fan_decision(56.0, currently_on=False))

    def test_fan_off_below_low_threshold(self):
        self.assertFalse(G.fan_decision(47.0, currently_on=True))

    def test_fan_holds_state_in_deadband(self):
        # 48 < t < 55 → keep whatever it was
        self.assertTrue(G.fan_decision(50.0, currently_on=True))
        self.assertFalse(G.fan_decision(50.0, currently_on=False))

    def test_fan_deadband_none_defaults_off(self):
        # first pass (unknown state) inside the band → known OFF
        self.assertFalse(G.fan_decision(50.0, currently_on=None))

    def test_format_stats_line(self):
        self.assertEqual(G.format_stats_line(5, 52.3, 38), "  5%  52.3C  38%")


if __name__ == "__main__":
    unittest.main()
