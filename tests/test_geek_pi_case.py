#!/usr/bin/env python3
# tests/test_geek_pi_case.py — plain unittest (no pytest; offline wheel set is flask-only).
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "features", "geek-pi-case"))
import geek_pi_case as G


class LogicTests(unittest.TestCase):
    def test_parse_cpu_temp_millidegrees(self):
        self.assertAlmostEqual(G.parse_cpu_temp("52300\n"), 52.3)

    # Sample temps derived from the module's own thresholds — robust to tuning
    # COOL_MAX / WARM_MAX (which are user-owned).
    def _bands(self):
        return (G.temp_colour(G.COOL_MAX - 10),
                G.temp_colour((G.COOL_MAX + G.WARM_MAX) / 2),
                G.temp_colour(G.WARM_MAX + 10))

    def test_temp_colour_three_distinct_bands(self):
        # Colours are user-owned; assert the banding logic, not specific RGB.
        cool, warm, hot = self._bands()
        self.assertEqual(len({cool, warm, hot}), 3)   # three distinct colours

    def test_temp_colour_thresholds(self):
        # < COOL_MAX cool; [COOL_MAX, WARM_MAX) warm; >= WARM_MAX hot
        cool, warm, hot = self._bands()
        self.assertEqual(G.temp_colour(G.COOL_MAX - 0.1), cool)
        self.assertEqual(G.temp_colour(G.COOL_MAX), warm)
        self.assertEqual(G.temp_colour(G.WARM_MAX - 0.1), warm)
        self.assertEqual(G.temp_colour(G.WARM_MAX), hot)

    def test_temp_colour_returns_valid_rgb(self):
        # Guards against a malformed tuple (e.g. missing a channel).
        for c in self._bands():
            self.assertEqual(len(c), 3)
            self.assertTrue(all(0 <= v <= 255 for v in c))

    def test_format_stats_line_wires_values(self):
        # Loose check — the OLED format is user-owned; just confirm the values land.
        line = G.format_stats_line(5, 52.3, 38)
        for token in ("52.3", "5", "38"):
            self.assertIn(token, line)


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
        # Loose — the OLED format is user-owned; confirm the values + source land.
        line = G.format_ups_line(87, 4100, on_batt=False)
        self.assertIn("87", line)
        self.assertIn("CHG", line)
        self.assertIn("4.10", line)

    def test_format_ups_line_on_battery(self):
        line = G.format_ups_line(15, 3600, on_batt=True)
        self.assertIn("15", line)
        self.assertIn("BATT", line)
        self.assertIn("3.60", line)


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


import shutil
import subprocess
import tempfile


class OptInstallImportTests(unittest.TestCase):
    """Regression: the installed daemon at /opt must import geek_pi_case as a
    sibling (the repo's scripts/ is not present at /opt). Simulate by copying
    only the two feature files into a temp dir and running with a clean env."""

    def test_daemon_imports_logic_when_run_from_opt_like_dir(self):
        feat = os.path.join(os.path.dirname(__file__), "..", "features", "geek-pi-case")
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(feat, "geek-pi-case.py"), os.path.join(d, "geek-pi-case.py"))
            shutil.copy(os.path.join(feat, "geek_pi_case.py"), os.path.join(d, "geek_pi_case.py"))
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)   # nothing from the repo on the path
            r = subprocess.run(
                [sys.executable, os.path.join(d, "geek-pi-case.py"), "--help"],
                cwd="/", capture_output=True, text=True, env=env,
            )
            self.assertEqual(r.returncode, 0, f"daemon --help failed: {r.stderr}")
            self.assertNotIn("ModuleNotFoundError", r.stderr)


if __name__ == "__main__":
    unittest.main()
