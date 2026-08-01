#!/usr/bin/env python3
"""Unit tests for the Argon fan curve + hysteresis and the installer's WM8731
0x1a collision guard (features/argon-fan)."""
import importlib.util
import os
import tempfile
import unittest

_FEAT = os.path.join(os.path.dirname(__file__), "..", "features", "argon-fan")


def _load(name, filename):
    """Load a hyphenated feature script by path (not a valid module name)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_FEAT, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load("argon_fan", "argon-fan.py")
INST = _load("install_argon_fan", "install-argon-fan.py")


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


class Wm8731CollisionTests(unittest.TestCase):
    """The installer must not auto-enable the fan service onto a shared 0x1a bus."""

    def _config(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_overlay_detected(self):
        cfg = self._config("dtparam=audio=off\ndtoverlay=wm8731-audio\n")
        self.assertTrue(INST._wm8731_overlay_present([cfg]))

    def test_overlay_detected_case_insensitive(self):
        cfg = self._config("dtoverlay=WM8731-audio\n")
        self.assertTrue(INST._wm8731_overlay_present([cfg]))

    def test_no_overlay(self):
        cfg = self._config("dtparam=audio=on\ndtoverlay=vc4-kms-v3d\n")
        self.assertFalse(INST._wm8731_overlay_present([cfg]))

    def test_missing_files_are_not_a_collision(self):
        self.assertFalse(INST._wm8731_overlay_present(["/no/such/config.txt"]))

    def test_first_readable_file_decides(self):
        # A present-but-clean first file shadows a later wm8731 file, matching how
        # the boot firmware reads a single config.txt.
        clean = self._config("dtoverlay=vc4-kms-v3d\n")
        dirty = self._config("dtoverlay=wm8731-audio\n")
        self.assertFalse(INST._wm8731_overlay_present([clean, dirty]))

    def test_collision_blocks_enable_without_force(self):
        self.assertEqual(INST.enable_decision(no_enable=False, force=False, collision=True),
                         (False, True))

    def test_force_enables_despite_collision(self):
        self.assertEqual(INST.enable_decision(no_enable=False, force=True, collision=True),
                         (True, False))

    def test_no_collision_enables_normally(self):
        self.assertEqual(INST.enable_decision(no_enable=False, force=False, collision=False),
                         (True, False))

    def test_no_enable_always_wins(self):
        # --no-enable trumps everything, and never reports a collision block.
        self.assertEqual(INST.enable_decision(no_enable=True, force=True, collision=True),
                         (False, False))

    def test_unit_ordered_after_sound_subsystem(self):
        unit = INST.build_unit("pi")
        self.assertIn("After=multi-user.target sound.target alsa-restore.service", unit)


if __name__ == "__main__":
    unittest.main()
