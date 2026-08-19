import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from services.adsb.common import adsb

# The env-file as the FlightAware package ships it (CONFIG_STYLE=6, unpinned),
# trimmed to the lines this writer reasons about.
_STYLE6 = (
    "ENABLED=yes\n"
    "RECEIVER=rtlsdr\n"
    "RECEIVER_SERIAL=\n"
    "RECEIVER_GAIN=60\n"
    "MAX_RANGE=360\n"
    "CONFIG_STYLE=6\n"
    'RECEIVER_OPTIONS=""\n'
)

class Dump1090DeviceArgsTest(unittest.TestCase):
    def test_none_device_yields_no_args(self):
        self.assertEqual(adsb.dump1090_device_args(None), [])

    def test_device_without_serial_yields_no_args(self):
        self.assertEqual(adsb.dump1090_device_args({"id": "a", "kind": "rtl-sdr"}), [])

    def test_device_with_serial_yields_device_index_flag(self):
        # start-dump1090-fa emits `--device-index $RECEIVER_SERIAL`; `--device`
        # is not a flag it ever produces.
        self.assertEqual(
            adsb.dump1090_device_args({"id": "a", "kind": "rtl-sdr", "serial": "1090"}),
            ["--device-index", "1090"])

class Dump1090EnvTextTest(unittest.TestCase):
    def test_pins_the_serial_on_the_supported_knob(self):
        out = adsb.dump1090_env_text(_STYLE6, "000000F2")
        self.assertIn("RECEIVER_SERIAL=000000F2\n", out)

    def test_never_writes_a_device_token_into_receiver_options(self):
        # The whole bug: a device token here is discarded under CONFIG_STYLE=6.
        out = adsb.dump1090_env_text(_STYLE6, "000000F2")
        self.assertNotIn("--device", out)

    def test_strips_a_stale_device_token_left_by_the_old_writer(self):
        # What the previous implementation actually left on pi5draws.
        stale = _STYLE6.replace('RECEIVER_OPTIONS=""',
                                'RECEIVER_OPTIONS="--device 000000F2"')
        out = adsb.dump1090_env_text(stale, "000000F2")
        self.assertIn('RECEIVER_OPTIONS=""\n', out)
        self.assertIn("RECEIVER_SERIAL=000000F2\n", out)

    def test_strips_device_index_form_too(self):
        stale = _STYLE6.replace('RECEIVER_OPTIONS=""',
                                'RECEIVER_OPTIONS="--device-index 3"')
        self.assertIn('RECEIVER_OPTIONS=""\n', adsb.dump1090_env_text(stale, "42"))

    def test_preserves_operator_options_that_are_not_device_tokens(self):
        mine = _STYLE6.replace('RECEIVER_OPTIONS=""',
                               'RECEIVER_OPTIONS="--device 000000F2 --ppm 3"')
        out = adsb.dump1090_env_text(mine, "000000F2")
        self.assertIn('RECEIVER_OPTIONS="--ppm 3"\n', out)

    def test_unpins_when_serial_is_empty(self):
        pinned = adsb.dump1090_env_text(_STYLE6, "000000F2")
        self.assertIn("RECEIVER_SERIAL=\n", adsb.dump1090_env_text(pinned, ""))

    def test_unpins_when_serial_is_none(self):
        pinned = adsb.dump1090_env_text(_STYLE6, "000000F2")
        self.assertIn("RECEIVER_SERIAL=\n", adsb.dump1090_env_text(pinned, None))

    def test_appends_receiver_serial_when_the_line_is_absent(self):
        without = "ENABLED=yes\nRECEIVER=rtlsdr\nCONFIG_STYLE=6\n"
        self.assertIn("RECEIVER_SERIAL=42\n", adsb.dump1090_env_text(without, "42"))

    def test_leaves_unrelated_lines_untouched(self):
        out = adsb.dump1090_env_text(_STYLE6, "000000F2")
        for keep in ("ENABLED=yes", "RECEIVER=rtlsdr", "RECEIVER_GAIN=60",
                     "MAX_RANGE=360", "CONFIG_STYLE=6"):
            self.assertIn(keep + "\n", out)

    def test_is_idempotent(self):
        once  = adsb.dump1090_env_text(_STYLE6, "000000F2")
        twice = adsb.dump1090_env_text(once, "000000F2")
        self.assertEqual(once, twice)

    def test_does_not_duplicate_the_serial_line_on_reassignment(self):
        out = adsb.dump1090_env_text(adsb.dump1090_env_text(_STYLE6, "000000F2"), "00000031")
        self.assertEqual(out.count("RECEIVER_SERIAL="), 1)
        self.assertIn("RECEIVER_SERIAL=00000031\n", out)

    def test_ends_with_exactly_one_trailing_newline(self):
        # Repeated assignment must not accrete blank lines at EOF.
        out = adsb.dump1090_env_text(adsb.dump1090_env_text(_STYLE6, "42"), "43")
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

class ApplyGuardTest(unittest.TestCase):
    def test_apply_is_noop_on_non_linux(self):
        # On this macOS dev box apply() must return without attempting any
        # root/systemd side effects (mirrors the Linux guard used across OASIS
        # install code). It must not raise.
        adsb.apply("/tmp/does-not-matter", {"id": "a", "kind": "rtl-sdr", "serial": "1090"})


class Dump1090ConfigStyleTest(unittest.TestCase):
    def test_declared_style_wins(self):
        self.assertEqual(adsb.dump1090_config_style(_STYLE6), "6")
        self.assertEqual(adsb.dump1090_config_style("CONFIG_STYLE=5\n"), "5")

    def test_absent_style_infers_5_from_a_non_empty_options_var(self):
        # This is the wrapper's own `if [ -z "$CONFIG_STYLE" ]` inference, and
        # the reason a stale device token in RECEIVER_OPTIONS is dangerous.
        self.assertEqual(
            adsb.dump1090_config_style('RECEIVER_OPTIONS="--device 1090"\n'), "5")

    def test_absent_style_infers_6_when_every_options_var_is_empty(self):
        self.assertEqual(
            adsb.dump1090_config_style('RECEIVER_OPTIONS=""\nNET_OPTIONS=""\n'), "6")

    def test_any_of_the_four_options_vars_triggers_the_inference(self):
        for key in ("RECEIVER_OPTIONS", "DECODER_OPTIONS", "NET_OPTIONS", "JSON_OPTIONS"):
            with self.subTest(key=key):
                self.assertEqual(adsb.dump1090_config_style(f'{key}="--x"\n'), "5")

    def test_last_assignment_wins_as_sourcing_would(self):
        self.assertEqual(adsb.dump1090_config_style("CONFIG_STYLE=5\nCONFIG_STYLE=6\n"), "6")


class Dump1090OverrideActiveTest(unittest.TestCase):
    def test_stock_env_file_has_no_override(self):
        self.assertFalse(adsb.dump1090_override_active(_STYLE6))

    def test_override_suppresses_the_pin_on_style_6(self):
        # The whole point: the pin is written, stored, and then ignored. apply()
        # must warn rather than report a clean win.
        text = _STYLE6 + 'OVERRIDE_OPTIONS="--device-index 9"\n'
        self.assertTrue(adsb.dump1090_override_active(text))

    def test_empty_override_is_not_active(self):
        self.assertFalse(adsb.dump1090_override_active(_STYLE6 + 'OVERRIDE_OPTIONS=""\n'))

    def test_whitespace_only_override_is_not_active(self):
        self.assertFalse(adsb.dump1090_override_active(_STYLE6 + 'OVERRIDE_OPTIONS="   "\n'))

    def test_override_is_ignored_under_style_5(self):
        # Style 5 builds the command line from the *_OPTIONS vars, so
        # OVERRIDE_OPTIONS never reaches the wrapper and the pin is not
        # suppressed by it.
        text = 'CONFIG_STYLE=5\nOVERRIDE_OPTIONS="--device-index 9"\n'
        self.assertFalse(adsb.dump1090_override_active(text))

    def test_detection_does_not_modify_the_env_text(self):
        text = _STYLE6 + 'OVERRIDE_OPTIONS="--device-index 9"\n'
        before = text
        adsb.dump1090_override_active(text)
        self.assertEqual(text, before)
        # and the writer still pins normally — warning is advisory, not a veto
        self.assertIn("RECEIVER_SERIAL=1090", adsb.dump1090_env_text(text, "1090"))


if __name__ == "__main__":
    unittest.main()
