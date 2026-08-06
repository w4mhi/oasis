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

    def test_line_output_dac_is_unmuted(self):
        """Bench regression (2026-08-06): the line-output DAC path powers up
        muted. Routing the DAC into the output mixer is not sufficient — with
        `LO DAC` off, PTT keys and the radio transmits an unmodulated carrier, so
        nothing decodes anywhere. Zero decodes across a -25dB..-5dB PCM sweep at
        several receiving stations; all decoded once this was on."""
        self.assertEqual(dict(draws_audio.MIXER)["LO DAC"], "on")

    def test_both_codec_power_on_mutes_are_cleared(self):
        """The two bugs that made this feature non-functional in both directions
        were the same class: codec defaults that mute the path. Guard them
        together so neither is dropped from the baseline again."""
        mixer = dict(draws_audio.MIXER)
        self.assertEqual(mixer["LO DAC"], "on")               # TX
        self.assertEqual(mixer["ADCFGA Left Mute"], "off")    # RX
        self.assertEqual(mixer["ADCFGA Right Mute"], "off")   # RX

    def test_adc_channel_mutes_are_cleared(self):
        """Bench regression (2026-08-06): the ADCFGA mutes are the codec's
        power-on default and the driver declares them uninverted, so `on` means
        muted. Leaving them alone gave a RUNNING capture stream that delivered
        perfect digital zeros on both channels with a live radio and open
        squelch. RX simply never worked without these."""
        mixer = dict(draws_audio.MIXER)
        self.assertEqual(mixer["ADCFGA Left Mute"], "off")
        self.assertEqual(mixer["ADCFGA Right Mute"], "off")


class CallsignTest(unittest.TestCase):
    def test_parses_bare_callsign(self):
        self.assertEqual(draws_audio.parse_callsign("W4MHI"), ("W4MHI", 0))

    def test_parses_ssid(self):
        self.assertEqual(draws_audio.parse_callsign("W4MHI-6"), ("W4MHI", 6))

    def test_upcases_and_strips(self):
        self.assertEqual(draws_audio.parse_callsign("  w4mhi-6 "), ("W4MHI", 6))

    def test_rejects_empty(self):
        self.assertRaises(ValueError, draws_audio.parse_callsign, "")

    def test_rejects_overlong_base(self):
        self.assertRaises(ValueError, draws_audio.parse_callsign, "W4MHIXYZ")

    def test_rejects_out_of_range_ssid(self):
        self.assertRaises(ValueError, draws_audio.parse_callsign, "W4MHI-16")

    def test_rejects_non_numeric_ssid(self):
        self.assertRaises(ValueError, draws_audio.parse_callsign, "W4MHI-x")


class Ax25FrameTest(unittest.TestCase):
    def test_addresses_are_shifted_left_one_bit(self):
        f = draws_audio.build_ax25_ui_frame("W4MHI", 6)
        self.assertEqual(f[0:6], bytes(c << 1 for c in b"APDW17"))
        self.assertEqual(f[7:13], bytes(c << 1 for c in b"W4MHI "))

    def test_source_is_flagged_last_and_carries_ssid(self):
        f = draws_audio.build_ax25_ui_frame("W4MHI", 6)
        self.assertEqual(f[6] & 0x01, 0)            # dest: not last
        self.assertEqual(f[13] & 0x01, 1)           # source: last address
        self.assertEqual((f[13] >> 1) & 0x0F, 6)    # ssid

    def test_is_a_ui_frame_with_no_layer_3(self):
        f = draws_audio.build_ax25_ui_frame("W4MHI", 0)
        self.assertEqual(f[14], 0x03)               # UI
        self.assertEqual(f[15], 0xF0)               # no layer 3

    def test_payload_is_a_status_report_carrying_the_comment(self):
        """A status report (`>`), deliberately NOT a position — the test must
        never invent coordinates for a station that has none."""
        f = draws_audio.build_ax25_ui_frame("W4MHI", 0)
        self.assertEqual(f[16:17], b">")
        self.assertEqual(f[17:], draws_audio.LIVETEST_COMMENT.encode())


class KissTest(unittest.TestCase):
    def test_wraps_in_fend_with_channel_in_high_nibble(self):
        out = draws_audio.kiss_wrap(b"\x01\x02", channel=1)
        self.assertEqual(out[0], 0xC0)
        self.assertEqual(out[-1], 0xC0)
        self.assertEqual(out[1], 0x10)              # ch 1, data command 0

    def test_channel_zero_is_the_default(self):
        self.assertEqual(draws_audio.kiss_wrap(b"\x01")[1], 0x00)

    def test_escapes_fend_and_fesc_in_payload(self):
        self.assertEqual(draws_audio.kiss_wrap(b"\xc0\xdb"),
                         b"\xc0\x00\xdb\xdc\xdb\xdd\xc0")


class LiveTestFrameTest(unittest.TestCase):
    def test_builds_a_sendable_kiss_frame(self):
        f = draws_audio.build_livetest_frame("W4MHI-6", channel=1)
        self.assertEqual(f[0], 0xC0)
        self.assertEqual(f[1], 0x10)
        self.assertIn(draws_audio.LIVETEST_COMMENT.encode(), f)

    def test_bad_callsign_raises_before_any_io(self):
        self.assertRaises(ValueError, draws_audio.build_livetest_frame, "nope-99")


class PortsTest(unittest.TestCase):
    def test_left_is_aprs_on_gpio12(self):
        left = next(p for p in draws_audio.PORTS if p["port"] == "left")
        self.assertEqual(left["gpio"], 12)
        self.assertEqual(left["service"], "APRS")

    def test_right_is_winlink_on_gpio23(self):
        right = next(p for p in draws_audio.PORTS if p["port"] == "right")
        self.assertEqual(right["gpio"], 23)
        self.assertEqual(right["service"], "Winlink")


class AcpIgnoreRuleTest(unittest.TestCase):
    """Bench 2026-08-06: WirePlumber adopts the DRAWS codec as a desktop sound
    card and re-applies its own volume after alsactl restores at boot, so PCM
    silently reverted to the codec default (+0.5dB, ~25dB hot) on every power
    cycle while the other 44 controls survived. ACP_IGNORE keeps it out."""

    def test_rule_matches_the_card_by_alsa_id(self):
        rule = draws_audio.build_acp_ignore_rule()
        self.assertIn('SUBSYSTEM=="sound"', rule)
        self.assertIn('ATTR{id}=="draws"', rule)

    def test_rule_sets_acp_ignore(self):
        self.assertIn('ENV{ACP_IGNORE}="1"', draws_audio.build_acp_ignore_rule())

    def test_card_is_overridable(self):
        self.assertIn('ATTR{id}=="udrc"', draws_audio.build_acp_ignore_rule("udrc"))

    def test_rule_explains_itself(self):
        """This one is non-obvious enough that a bare rule would look like
        cruft to the next person reading /etc/udev/rules.d."""
        rule = draws_audio.build_acp_ignore_rule()
        self.assertTrue(rule.lstrip().startswith("#"))
        self.assertIn("OASIS", rule)


class RemovalRecordTest(unittest.TestCase):
    def test_strips_shared_overlay_and_flags_reboot(self):
        rec = draws_audio.removal_record()
        self.assertEqual(rec["config_lines"], ["dtoverlay=draws"])
        self.assertTrue(rec["requires_reboot"])
        self.assertTrue(any("ALSA" in n for n in rec["notes"]))
        self.assertTrue(any("shared" in n for n in rec["notes"]))

    def test_removes_the_acp_ignore_udev_rule(self):
        """The rule is ours alone (unlike the shared overlay line), so uninstall
        must take it with us — otherwise the card stays hidden from the desktop
        audio stack after the feature is gone."""
        rec = draws_audio.removal_record()
        self.assertIn(draws_audio.ACP_IGNORE_RULE, rec["files"])


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
