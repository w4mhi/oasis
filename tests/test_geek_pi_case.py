# tests/test_geek_pi_case.py — plain unittest (no pytest; offline wheel set is flask-only).
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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


class UpsDecodeTests(unittest.TestCase):
    def test_word_le(self):
        # low=0x10 (16), high=0x0F (15) → 0x0F10 = 3856
        self.assertEqual(G.word_le(0x10, 0x0F), 3856)

    def test_decode_temp_positive(self):
        self.assertEqual(G.decode_temp(25), 25)

    def test_decode_temp_negative_twos_complement(self):
        self.assertEqual(G.decode_temp(0xFFFB), -5)   # 16-bit two's complement

    def test_on_battery_true_when_both_inputs_low(self):
        self.assertTrue(G.on_battery(0, 0))

    def test_on_battery_false_when_usbc_present(self):
        self.assertFalse(G.on_battery(5100, 0))

    def test_format_ups_line_charging(self):
        self.assertEqual(G.format_ups_line(87, 4100, on_batt=False), "BAT  87% CHG 4.10V")

    def test_format_ups_line_on_battery(self):
        self.assertEqual(G.format_ups_line(15, 3600, on_batt=True), "BAT  15% BATT 3.60V")


class ShutdownGuardTests(unittest.TestCase):
    def test_fires_after_n_consecutive_low_on_battery(self):
        g = G.ShutdownGuard(pct=15, samples=3)
        self.assertFalse(g.update(on_batt=True, capacity_pct=10))
        self.assertFalse(g.update(on_batt=True, capacity_pct=10))
        self.assertTrue(g.update(on_batt=True, capacity_pct=10))   # 3rd read fires

    def test_fires_only_once(self):
        g = G.ShutdownGuard(pct=15, samples=1)
        self.assertTrue(g.update(on_batt=True, capacity_pct=5))
        self.assertFalse(g.update(on_batt=True, capacity_pct=5))   # already fired

    def test_transient_resets_counter(self):
        g = G.ShutdownGuard(pct=15, samples=2)
        self.assertFalse(g.update(on_batt=True, capacity_pct=10))
        self.assertFalse(g.update(on_batt=False, capacity_pct=10))  # mains back → reset
        self.assertFalse(g.update(on_batt=True, capacity_pct=10))   # count restarts
        self.assertTrue(g.update(on_batt=True, capacity_pct=10))

    def test_no_fire_above_threshold(self):
        g = G.ShutdownGuard(pct=15, samples=1)
        self.assertFalse(g.update(on_batt=True, capacity_pct=50))


if __name__ == "__main__":
    unittest.main()
