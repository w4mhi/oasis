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
    def test_left_is_winlink_on_gpio12_channel0(self):
        """Winlink MUST be channel 0: pat 1.0.0 / wl2k-go v1.0.1 panic on any
        AGW port but 0, and direwolf's channel-to-audio mapping is fixed by the
        codec, so channel 0 is the left connector."""
        left = draws_audio.port_for_channel(0)
        self.assertEqual(left["port"], "left")
        self.assertEqual(left["gpio"], 12)
        self.assertEqual(left["service"], "Winlink")

    def test_right_is_aprs_on_gpio23_channel1(self):
        right = draws_audio.port_for_channel(1)
        self.assertEqual(right["port"], "right")
        self.assertEqual(right["gpio"], 23)
        self.assertEqual(right["service"], "APRS")

    def test_unknown_channel_raises(self):
        self.assertRaises(ValueError, draws_audio.port_for_channel, 2)


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

    def test_stops_and_removes_the_shared_tnc_service(self):
        """The TNC service is ours and owns the sound card — uninstall must stop
        and remove it, or a dead feature keeps holding the codec at boot."""
        rec = draws_audio.removal_record()
        self.assertIn(draws_audio.TNC_UNIT_NAME, rec["services"])
        self.assertIn(draws_audio.TNC_UNIT_PATH, rec["files"])

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

    def test_rx_level_accepts_the_documented_equals_form(self):
        """Bench 2026-08-06: `--rx-level -9.0dB` FAILS — argparse reads the
        leading '-' as a switch, exactly like the amixer trap. The printed hint
        used to tell operators the broken form. Only '=' (or a bare number,
        which argparse's negative-number heuristic allows) works."""
        args = self._load().build_parser().parse_args(["--rx-level=-9.0dB"])
        self.assertEqual(args.rx_level, "-9.0dB")

    def test_rx_level_accepts_a_bare_negative_number(self):
        args = self._load().build_parser().parse_args(["--rx-level", "-9.0"])
        self.assertEqual(args.rx_level, "-9.0")

    def test_flags(self):
        args = self._load().build_parser().parse_args(
            ["--dry-run", "--check", "--config-only", "--mixer-only"])
        self.assertTrue(args.dry_run and args.check
                        and args.config_only and args.mixer_only)



class ProfileNamingTest(unittest.TestCase):
    """Config/profile names must say WHICH interface and WHICH service they
    drive. A bare "oasis-winlink.conf" was unplaceable on a box with two radio
    interfaces, and a stale one aimed at an absent card crash-looped direwolf in
    a way that read like a Winlink fault."""

    def test_draws_conf_is_oasis_named(self):
        self.assertEqual(draws_audio.TNC_CONF_NAME, "oasis-draws.conf")

    def test_profile_names(self):
        self.assertEqual(draws_audio.TNC_PROFILE_APRS, "oasis-draws-aprs")
        self.assertEqual(draws_audio.TNC_PROFILE_WINLINK, "oasis-draws-winlink")

    def test_conf_labels_both_profiles_next_to_their_channel(self):
        """Each banner must sit immediately above the CHANNEL it names. Compare
        DIRECTIVE lines only — the header block mentions both channels too."""
        lines = draws_audio.build_tnc_conf("W4MHI", 524, 535).splitlines()

        def line_of(pred):
            return next(i for i, ln in enumerate(lines) if pred(ln))

        ch0 = line_of(lambda ln: ln.strip() == "CHANNEL 0")
        ch1 = line_of(lambda ln: ln.strip() == "CHANNEL 1")
        aprs = line_of(lambda ln: ln.startswith("# ──")
                       and draws_audio.TNC_PROFILE_APRS in ln)
        winl = line_of(lambda ln: ln.startswith("# ──")
                       and draws_audio.TNC_PROFILE_WINLINK in ln)
        self.assertLess(winl, ch0)      # winlink banner heads CHANNEL 0
        self.assertLess(ch0, aprs)
        self.assertLess(aprs, ch1)      # aprs banner heads CHANNEL 1

    def test_conf_explains_why_it_is_one_file(self):
        """Someone WILL look for oasis-draws-winlink.conf on disk."""
        c = draws_audio.build_tnc_conf("W4MHI", 524, 535)
        self.assertIn("device busy", c)
        self.assertIn("PROFILES", c)

    def test_unit_description_carries_both_profiles(self):
        u = draws_audio.build_tnc_service("pi", "/home/pi", 524, 535)
        self.assertIn(draws_audio.TNC_PROFILE_APRS, u)
        self.assertIn(draws_audio.TNC_PROFILE_WINLINK, u)



class RxLevelTest(unittest.TestCase):
    """Bench 2026-08-06: the n7nix 0dB baseline measured too hot on BOTH test
    radios -- one clipped (direwolf's level fell as gain rose), the other read
    142 against direwolf's target of ~50. RX level is radio-specific, so the
    installer keeps shipping the baseline on a fresh board but must (a) offer a
    first-class way to trim and (b) never clobber a trim on reinstall."""

    def test_baseline_matches_the_mixer_entry(self):
        """The preserve check compares against this, so drift would silently
        make every reinstall look like a customised value."""
        self.assertEqual(dict(draws_audio.MIXER)[draws_audio.RX_LEVEL_CONTROL],
                         draws_audio.RX_LEVEL_BASELINE)

    def test_parses_a_db_string(self):
        self.assertEqual(draws_audio.parse_rx_level("-9.0dB"), "-9.0dB,-9.0dB")

    def test_parses_a_bare_number(self):
        self.assertEqual(draws_audio.parse_rx_level("-9"), "-9.0dB,-9.0dB")
        self.assertEqual(draws_audio.parse_rx_level("0"), "0.0dB,0.0dB")

    def test_accepts_an_explicit_stereo_pair(self):
        self.assertEqual(draws_audio.parse_rx_level("-9.0dB,-9.0dB"),
                         "-9.0dB,-9.0dB")

    def test_rejects_nonsense(self):
        for bad in ("", "loud", "9dBm", "--9"):
            self.assertRaises(ValueError, draws_audio.parse_rx_level, bad)

    def test_command_uses_the_end_of_options_marker(self):
        """Same negative-dB trap as the mixer list -- amixer would read -9.0dB
        as a switch."""
        cmd = draws_audio.build_rx_level_command("-9.0dB,-9.0dB")
        self.assertEqual(cmd[:5], ["amixer", "-c", "draws", "sset", "--"])
        self.assertEqual(cmd[-2:], [draws_audio.RX_LEVEL_CONTROL, "-9.0dB,-9.0dB"])

    def test_detects_a_customised_level(self):
        self.assertTrue(draws_audio.rx_level_is_customised("-9.00dB"))
        self.assertTrue(draws_audio.rx_level_is_customised("-12.00dB"))

    def test_baseline_reads_as_not_customised(self):
        """A fresh board sits at the baseline, so nothing is skipped on a first
        install."""
        self.assertFalse(draws_audio.rx_level_is_customised("0.00dB"))
        self.assertFalse(draws_audio.rx_level_is_customised("0.0dB"))

    def test_unreadable_level_is_not_treated_as_customised(self):
        """Better to re-apply the known-good baseline than to skip on garbage."""
        self.assertFalse(draws_audio.rx_level_is_customised(""))
        self.assertFalse(draws_audio.rx_level_is_customised(None))


if __name__ == "__main__":
    unittest.main()


class TncConfTest(unittest.TestCase):
    """One 2-channel Direwolf owns the stereo card: two processes cannot open
    the same PCM, so both mDin6 ports must live in ONE config."""

    def _conf(self, **kw):
        kw.setdefault("callsign", "W4MHI")
        kw.setdefault("ptt_left", 524)
        kw.setdefault("ptt_right", 535)
        return draws_audio.build_tnc_conf(**kw)

    def test_single_stereo_device_with_two_channels(self):
        c = self._conf()
        self.assertIn("ADEVICE   %s" % draws_audio.TNC_ADEVICE, c)
        self.assertIn("ACHANNELS 2", c)
        self.assertIn("CHANNEL 0", c)
        self.assertIn("CHANNEL 1", c)

    def test_ptt_lines_are_sysfs_globals_per_channel(self):
        c = self._conf(ptt_left=524, ptt_right=535)
        self.assertIn("PTT GPIO 524", c)
        self.assertIn("PTT GPIO 535", c)

    def test_winlink_channel_owns_the_left_ptt_line(self):
        """Guards the swap: channel 0 (Winlink) must key BCM 12 -> 524."""
        lines = self._conf(ptt_left=524, ptt_right=535).splitlines()
        i = next(n for n, ln in enumerate(lines) if ln.strip() == "CHANNEL 0")
        self.assertIn("PTT GPIO 524", "\n".join(lines[i:i + 5]))

    def test_channel_zero_precedes_channel_one(self):
        """Channel order defines the port mapping: 0=left/APRS, 1=right/Winlink."""
        c = self._conf()
        self.assertLess(c.index("CHANNEL 0"), c.index("CHANNEL 1"))
        self.assertLess(c.index("PTT GPIO 524"), c.index("PTT GPIO 535"))

    def test_callsign_is_used_for_both_channels(self):
        self.assertEqual(self._conf(callsign="W4MHI").count("MYCALL W4MHI"), 2)

    def test_serves_shared_agw_and_kiss_ports(self):
        c = self._conf()
        self.assertIn("AGWPORT %d" % draws_audio.TNC_AGW_PORT, c)
        self.assertIn("KISSPORT %d" % draws_audio.TNC_KISS_PORT, c)

    def test_forces_ax25_v20(self):
        """RMS gateways mishandle the v2.2 XID teardown and leave direwolf
        key-locked retransmitting DISC/XID -- matches oasis-dra-pi-winlink.conf."""
        self.assertIn("MAXV22 0", self._conf())

    def test_unresolved_ptt_is_refused(self):
        """Guessing a PTT number would key the wrong line; fail loudly instead."""
        self.assertRaises(ValueError, draws_audio.build_tnc_conf,
                          callsign="W4MHI", ptt_left=None, ptt_right=535)


class TncServiceTest(unittest.TestCase):
    def _unit(self, **kw):
        kw.setdefault("user", "mihaim")
        kw.setdefault("home", "/home/mihaim")
        kw.setdefault("ptt_left", 524)
        kw.setdefault("ptt_right", 535)
        return draws_audio.build_tnc_service(**kw)

    def test_runs_direwolf_with_the_shared_conf(self):
        u = self._unit()
        self.assertIn("/usr/bin/direwolf", u)
        self.assertIn(draws_audio.TNC_CONF_NAME, u)

    def test_runs_as_the_target_user(self):
        u = self._unit(user="pi", home="/home/pi")
        self.assertIn("User=pi", u)
        self.assertIn("Environment=HOME=/home/pi", u)

    def test_unexports_both_ptt_lines_on_stop(self):
        """A restart must be able to re-claim both lines; leaking either one
        leaves the next start unable to key that port."""
        u = self._unit(ptt_left=524, ptt_right=535)
        self.assertIn("ExecStopPost", u)
        stop = [ln for ln in u.splitlines() if ln.startswith("ExecStopPost")][0]
        self.assertIn("524", stop)
        self.assertIn("535", stop)

    def test_enabled_at_boot(self):
        self.assertIn("WantedBy=multi-user.target", self._unit())
