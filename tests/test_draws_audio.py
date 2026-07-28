import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "draws_audio",
    os.path.join(os.path.dirname(_HERE), "features", "draws-audio", "draws_audio.py"),
)
draws_audio = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(draws_audio)


class MixerCommandsTest(unittest.TestCase):
    def test_builds_amixer_vectors_for_the_default_card(self):
        cmds = draws_audio.build_mixer_commands()
        # one vector per control, all targeting `-c draws` via amixer sset
        self.assertEqual(len(cmds), len(draws_audio.MIXER))
        for (ctrl, val), cmd in zip(draws_audio.MIXER, cmds):
            self.assertEqual(cmd, ["amixer", "-c", "draws", "sset", ctrl, val])

    def test_card_is_overridable(self):
        cmds = draws_audio.build_mixer_commands("udrc")
        self.assertTrue(all(cmd[2] == "udrc" for cmd in cmds))

    def test_mixer_covers_rx_and_tx_paths(self):
        controls = [c for c, _ in draws_audio.MIXER]
        # RX input select + level, TX playback level + output routing
        self.assertIn("ADC Level", controls)
        self.assertIn("IN2_L to Left Mixer Positive Resistor", controls)
        self.assertIn("PCM", controls)
        self.assertIn("LOL Output Mixer L_DAC", controls)
        self.assertIn("LOR Output Mixer R_DAC", controls)


class PortsTest(unittest.TestCase):
    def test_left_is_aprs_on_gpio12(self):
        left = next(p for p in draws_audio.PORTS if p["port"] == "left")
        self.assertEqual(left["gpio"], 12)
        self.assertEqual(left["service"], "APRS")

    def test_right_is_winlink_on_gpio23(self):
        right = next(p for p in draws_audio.PORTS if p["port"] == "right")
        self.assertEqual(right["gpio"], 23)
        self.assertEqual(right["service"], "Winlink")


class RemovalRecordTest(unittest.TestCase):
    def test_strips_shared_overlay_and_flags_reboot(self):
        rec = draws_audio.removal_record()
        self.assertEqual(rec["config_lines"], ["dtoverlay=draws"])
        self.assertTrue(rec["requires_reboot"])
        self.assertTrue(any("ALSA" in n for n in rec["notes"]))
        self.assertTrue(any("shared" in n for n in rec["notes"]))


class ExitCodeTest(unittest.TestCase):
    def test_reboot_when_overlay_changed(self):
        self.assertEqual(draws_audio.decide_exit_code(True, False), 10)

    def test_reboot_when_card_absent(self):
        self.assertEqual(draws_audio.decide_exit_code(False, False), 10)

    def test_zero_when_present_and_unchanged(self):
        self.assertEqual(draws_audio.decide_exit_code(False, True), 0)


if __name__ == "__main__":
    unittest.main()
