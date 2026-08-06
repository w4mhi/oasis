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
            self.assertEqual(cmd, ["amixer", "-c", "draws", "sset", "--", ctrl, val])

    def test_end_of_options_marker_precedes_the_control(self):
        """Bench regression (2026-08-06): without `--`, amixer parses a negative
        dB value like `-25.0dB,-25.0dB` as a command-line switch and fails with
        'Invalid switch or option'. That silently left PCM at +0.5dB instead of
        -25.0dB — ~25dB too hot into the radio. The marker must come before the
        control name so ctrl/val stay the last two argv slots."""
        for cmd in draws_audio.build_mixer_commands():
            self.assertEqual(cmd[4], "--")
            self.assertEqual(len(cmd), 7)

    def test_negative_db_values_survive_as_their_own_argv_slot(self):
        cmds = dict((cmd[-2], cmd[-1]) for cmd in draws_audio.build_mixer_commands())
        self.assertEqual(cmds["PCM"], "-25.0dB,-25.0dB")
        self.assertEqual(cmds["LO Driver Gain"], "-6.0dB,-6.0dB")

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

    def test_adc_channel_mutes_are_cleared(self):
        """Bench regression (2026-08-06): the ADCFGA mutes are the codec's
        power-on default and the driver declares them uninverted, so `on` means
        muted. Leaving them alone gave a RUNNING capture stream that delivered
        perfect digital zeros on both channels with a live radio and open
        squelch. RX simply never worked without these."""
        mixer = dict(draws_audio.MIXER)
        self.assertEqual(mixer["ADCFGA Left Mute"], "off")
        self.assertEqual(mixer["ADCFGA Right Mute"], "off")


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


_cli_spec = _ilu.spec_from_file_location(
    "install_draws_audio",
    os.path.join(os.path.dirname(_HERE), "features", "draws-audio", "install-draws-audio.py"),
)


class ParserTest(unittest.TestCase):
    def _load(self):
        mod = _ilu.module_from_spec(_cli_spec)
        _cli_spec.loader.exec_module(mod)
        return mod

    def test_defaults(self):
        args = self._load().build_parser().parse_args([])
        self.assertFalse(args.dry_run)
        self.assertFalse(args.check)
        self.assertFalse(args.config_only)
        self.assertFalse(args.mixer_only)

    def test_flags(self):
        args = self._load().build_parser().parse_args(
            ["--dry-run", "--check", "--config-only", "--mixer-only"])
        self.assertTrue(args.dry_run and args.check
                        and args.config_only and args.mixer_only)


if __name__ == "__main__":
    unittest.main()
