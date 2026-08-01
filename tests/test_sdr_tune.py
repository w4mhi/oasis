#!/usr/bin/env python3
# tests/test_sdr_tune.py — plain unittest (no pytest; offline wheel set is flask-only).
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "features", "rtl-sdr"))
import sdr_tune as S

_tune_spec = importlib.util.spec_from_file_location(
    "tune_rtl_sdr",
    os.path.join(os.path.dirname(__file__), "..", "features", "rtl-sdr", "tune-rtl-sdr.py"),
)
tune = importlib.util.module_from_spec(_tune_spec)
_tune_spec.loader.exec_module(tune)


class BuildPipelineTests(unittest.TestCase):
    def test_build_pipeline_24k(self):
        cmd = S.build_pipeline("144.390M", "32.8", 0, "0.50", 24000, "/tmp/sdr.conf")
        self.assertEqual(cmd, (
            "rtl_fm -M fm -f 144.390M -s 24000 -F 0 -g 32.8 -p 0 - "
            "| sox -t raw -r 24000 -e signed-integer -b 16 -c 1 - -t raw - vol 0.50 "
            "| direwolf -t 0 -a 2 -c /tmp/sdr.conf -r 24000 -D 1 -"
        ))

    def test_build_pipeline_48k_couples_all_stages(self):
        cmd = S.build_pipeline("144.800M", "40.2", 12, "0.75", 48000, "/tmp/sdr.conf")
        self.assertEqual(cmd.count("48000"), 3)   # rtl_fm -s, sox -r, direwolf -r
        self.assertIn("-p 12", cmd)
        self.assertIn("vol 0.75", cmd)

    def test_build_pipeline_fir_defaults_off(self):
        cmd = S.build_pipeline("144.390M", "32.8", 0, "0.50", 24000, "/tmp/sdr.conf")
        self.assertIn("-F 0", cmd)

    def test_build_pipeline_fir_selects_filter(self):
        cmd = S.build_pipeline("144.390M", "32.8", 0, "0.50", 24000,
                               "/tmp/sdr.conf", fir=9)
        self.assertIn("-F 9", cmd)
        self.assertNotIn("-F 0", cmd)

    def test_build_pipeline_device_omitted_by_default(self):
        # default (device=None) must be byte-identical to the pre-selector
        # single-dongle command — no -d leaks in.
        cmd = S.build_pipeline("144.390M", "32.8", 0, "0.50", 24000, "/tmp/sdr.conf")
        self.assertTrue(cmd.startswith("rtl_fm -M fm "))
        self.assertNotIn(" -d ", cmd)

    def test_build_pipeline_device_selects_by_index(self):
        cmd = S.build_pipeline("144.390M", "32.8", 0, "0.50", 24000,
                               "/tmp/sdr.conf", device="1")
        # -d rides on rtl_fm only (the sole hardware-opening stage).
        self.assertTrue(cmd.startswith("rtl_fm -d 1 -M fm "))
        self.assertEqual(cmd.count(" -d "), 1)

    def test_build_pipeline_device_accepts_serial(self):
        cmd = S.build_pipeline("144.390M", "32.8", 0, "0.50", 24000,
                               "/tmp/sdr.conf", device="00000001")
        self.assertIn("rtl_fm -d 00000001 -M fm ", cmd)


class ParseLineTests(unittest.TestCase):
    def test_parse_audio_level(self):
        ev = S.parse_line("KJ4XYZ-9 audio level = 46(6/6)   [NONE]   |||||||||")
        self.assertIsInstance(ev, S.AudioLevel)
        self.assertEqual((ev.level, ev.lo, ev.hi), (46, 6, 6))

    def test_parse_decoded_packet(self):
        ev = S.parse_line("[0.4] KJ4XYZ-9>APDW17,WIDE1-1,WIDE2-1:!3358.12N/08412.55W#hi")
        self.assertIsInstance(ev, S.Decoded)
        self.assertEqual(ev.src, "KJ4XYZ-9")
        self.assertEqual(ev.dest, "APDW17")
        self.assertTrue(ev.payload.startswith("!3358.12N"))

    def test_parse_audio_stat(self):
        ev = S.parse_line(
            "ADEVICE0: Sample rate approx. 24.6 k, 0 errors, "
            "receive audio level CH0 49")
        self.assertIsInstance(ev, S.AudioStat)
        self.assertEqual((ev.rate_k, ev.errors, ev.level), (24.6, 0, 49))

    def test_audio_stat_not_confused_with_audio_level(self):
        # The stats line contains the substring "audio level" but must NOT parse
        # as a decode-gated AudioLevel(46(6/6)).
        ev = S.parse_line(
            "ADEVICE0: Sample rate approx. 21.5 k, 3 errors, "
            "receive audio level CH0 48")
        self.assertIsInstance(ev, S.AudioStat)
        self.assertEqual(ev.errors, 3)

    def test_parse_ignores_noise(self):
        self.assertIsNone(S.parse_line("Ready to accept AGW client application 0 ..."))
        self.assertIsNone(S.parse_line(""))

    def test_parse_fixture_counts(self):
        path = os.path.join(os.path.dirname(__file__), "fixtures", "direwolf_output.txt")
        with open(path) as fh:
            events = [S.parse_line(ln) for ln in fh]
        levels = [e for e in events if isinstance(e, S.AudioLevel)]
        pkts = [e for e in events if isinstance(e, S.Decoded)]
        self.assertEqual(len(levels), 3)
        self.assertEqual(len(pkts), 2)


class ScoreTests(unittest.TestCase):
    def test_score_counts_and_averages(self):
        events = [
            S.AudioLevel(40, 6, 6), S.AudioLevel(60, 6, 6),
            S.Decoded("A", "B", "x"), S.Decoded("C", "D", "y"),
        ]
        count, avg = S.score(events)
        self.assertEqual(count, 2)
        self.assertEqual(avg, 50.0)

    def test_score_empty_window(self):
        self.assertEqual(S.score([]), (0, 0.0))


class SweepHelperTests(unittest.TestCase):
    def test_parse_gains_from_rtl_test(self):
        out = ("Found 1 device(s):\n  0:  Realtek, RTL2838UHIDIR\n"
               "Supported gain values (5): 0.0 8.7 16.6 32.8 49.6\n")
        self.assertEqual(S.parse_gains(out), [0.0, 8.7, 16.6, 32.8, 49.6])

    def test_parse_gains_absent_returns_empty(self):
        self.assertEqual(S.parse_gains("no gain line here"), [])

    def test_ppm_sweep_values(self):
        self.assertEqual(S.ppm_sweep_values(-10, 10, 5), [-10, -5, 0, 5, 10])

    def test_rank_sweep_prefers_more_decodes(self):
        # 32 and 40 tie on decodes(5); 40's avg_level(52) is closer to 50 → wins.
        results = [(28, 2, 45.0), (32, 5, 70.0), (40, 5, 52.0)]
        self.assertEqual(S.rank_sweep(results), 40)

    def test_rank_sweep_no_traffic_returns_none(self):
        self.assertIsNone(S.rank_sweep([(28, 0, 0.0), (32, 0, 0.0)]))

    def test_rank_sweep_min_decodes_gates_low_counts(self):
        # With min_decodes=3, the 2-packet row is inconclusive and excluded;
        # only the 4-packet row qualifies.
        results = [(28, 2, 50.0), (32, 4, 70.0)]
        self.assertEqual(S.rank_sweep(results, min_decodes=3), 32)

    def test_rank_sweep_min_decodes_all_below_returns_none(self):
        results = [(28, 1, 50.0), (32, 2, 48.0)]
        self.assertIsNone(S.rank_sweep(results, min_decodes=3))


class NormalizeFreqTests(unittest.TestCase):
    def test_accepts_and_canonicalises(self):
        self.assertEqual(S.normalize_freq("144.390M"), "144.390M")
        self.assertEqual(S.normalize_freq(" 144.800m "), "144.800M")
        self.assertEqual(S.normalize_freq("144800K"), "144800k")
        self.assertEqual(S.normalize_freq("144390000"), "144390000")

    def test_rejects_typos_and_out_of_range(self):
        self.assertIsNone(S.normalize_freq("14.4390M"))   # 14 MHz < 24 MHz floor
        self.assertIsNone(S.normalize_freq("2.5G"))        # above 1.766 GHz ceiling
        self.assertIsNone(S.normalize_freq("abc"))
        self.assertIsNone(S.normalize_freq(""))
        self.assertIsNone(S.normalize_freq("144,390M"))


class FormatterTests(unittest.TestCase):
    def test_level_band_thresholds(self):
        self.assertEqual(S.level_band(10), "low")
        self.assertEqual(S.level_band(46), "good")
        self.assertEqual(S.level_band(92), "high")
        self.assertEqual(S.level_band(20), "good")   # boundary: 20 is not low
        self.assertEqual(S.level_band(80), "good")   # boundary: 80 is not high

    def test_format_bar_fill(self):
        self.assertEqual(len(S.format_bar(50, width=10)), 10)
        self.assertEqual(S.format_bar(0, width=10), "░" * 10)
        self.assertEqual(S.format_bar(100, width=10), "█" * 10)
        self.assertEqual(S.format_bar(150, width=10), "█" * 10)   # clamped


class HandoffBuilderTests(unittest.TestCase):
    def test_build_feed_command_pins_48k(self):
        cmd = S.build_feed_command("144.390M", "32.8", 0)
        self.assertEqual(cmd, (
            "rtl_fm -f 144.390M -M fm -s 48000 -g 32.8 -p 0 - "
            "| socat -u -b 1920 - UDP-SENDTO:127.0.0.1:7355"
        ))

    def test_build_feed_command_omits_fir_when_zero(self):
        self.assertNotIn("-F", S.build_feed_command("144.390M", "32.8", 0, 0))

    def test_build_feed_command_includes_fir_when_set(self):
        cmd = S.build_feed_command("144.390M", "32.8", 0, 9)
        self.assertIn("-M fm -s 48000 -F 9 -g 32.8", cmd)

    def test_check_deps_reports_missing(self):
        which = {"rtl_fm": "/usr/bin/rtl_fm", "sox": None, "direwolf": None}
        self.assertEqual(S.check_deps(which), ["sox", "direwolf"])
        self.assertEqual(S.check_deps({"rtl_fm": "x", "sox": "y", "direwolf": "z"}), [])

    def test_deps_message_mentions_installer(self):
        msg = S.deps_message(["sox"])
        self.assertIn("sox", msg)
        self.assertIn("install-rtl-sdr.py", msg)

    def test_conf_template_has_modem(self):
        conf = S.SDR_CONF_TEMPLATE
        self.assertIn("ADEVICE - null", conf)
        self.assertIn("MODEM 1200", conf)
        self.assertIn("ACHANNELS 1", conf)


class DeviceProbeTests(unittest.TestCase):
    def test_device_error_busy(self):
        out = ("Found 1 device(s):\n  0:  RTLSDRBlog, Blog V4, SN: 00000001\n"
               "Using device 0: Generic RTL2832U OEM\n"
               "usb_claim_interface error -6\n"
               "Failed to open rtlsdr device #0.\n")
        self.assertEqual(S.device_error(out), "busy")

    def test_device_error_absent(self):
        self.assertEqual(S.device_error("No supported devices found.\n"), "absent")

    def test_device_error_ok(self):
        out = ("Found 1 device(s):\n  0:  Realtek, RTL2838UHIDIR\n"
               "Supported gain values (29): 0.0 0.9 1.4\n")
        self.assertIsNone(S.device_error(out))

    def test_device_help_busy_mentions_fixes(self):
        msg = S.device_help("busy")
        self.assertIn("aprs-sdr-feed.service", msg)
        self.assertIn("dvb_usb_rtl28xxu", msg)

    def test_device_help_busy_points_at_second_dongle(self):
        # the busy path must advertise the --device escape hatch and name the
        # ADS-B holder (the real single-box collision), not just the APRS feed.
        msg = S.device_help("busy")
        self.assertIn("--device", msg)
        self.assertIn("dump1090-fa", msg)

    def test_device_help_absent(self):
        self.assertIn("No RTL-SDR dongle", S.device_help("absent"))


class DeviceMenuTests(unittest.TestCase):
    TWO = ("Found 2 device(s):\n"
           "  0:  Realtek, RTL2832U, SN: 00001000\n"
           "  1:  RTLSDRBlog, Blog V4, SN: 00000001\n"
           "Using device 0: Generic RTL2832U\n"
           "usb_claim_interface error -6\n")

    def test_parse_devices_two_with_serials(self):
        devs = S.parse_devices(self.TWO)
        self.assertEqual(devs, [
            {"index": 0, "name": "Realtek, RTL2832U", "serial": "00001000"},
            {"index": 1, "name": "RTLSDRBlog, Blog V4", "serial": "00000001"},
        ])

    def test_parse_devices_without_serial(self):
        devs = S.parse_devices("Found 1 device(s):\n  0:  Realtek, RTL2838UHIDIR\n")
        self.assertEqual(devs, [{"index": 0, "name": "Realtek, RTL2838UHIDIR",
                                 "serial": ""}])

    def test_parse_devices_ignores_noise_lines(self):
        # "Found …", "Using …", and gain lines must never look like a device row.
        noise = ("Found 1 device(s):\n"
                 "Using device 0: Generic RTL2832U OEM\n"
                 "Supported gain values (29): 0.0 0.9 1.4\n")
        self.assertEqual(S.parse_devices(noise), [])

    def test_parse_devices_empty(self):
        self.assertEqual(S.parse_devices("No supported devices found.\n"), [])

    def test_format_device_menu_lists_index_and_serial(self):
        menu = S.format_device_menu(S.parse_devices(self.TWO))
        self.assertIn("[0]", menu)
        self.assertIn("[1]", menu)
        self.assertIn("Blog V4", menu)
        self.assertIn("SN 00000001", menu)

    def test_menu_choice_empty_defaults_to_first(self):
        self.assertEqual(S.parse_menu_choice("", S.parse_devices(self.TWO)), 0)

    def test_menu_choice_valid_index(self):
        self.assertEqual(S.parse_menu_choice("1", S.parse_devices(self.TWO)), 1)

    def test_menu_choice_out_of_range_returns_none(self):
        self.assertIsNone(S.parse_menu_choice("5", S.parse_devices(self.TWO)))

    def test_menu_choice_non_numeric_returns_none(self):
        self.assertIsNone(S.parse_menu_choice("x", S.parse_devices(self.TWO)))


class ResolveDeviceTests(unittest.TestCase):
    DEVS = [
        {"index": 0, "name": "Realtek, RTL2832U", "serial": "00001000"},
        {"index": 1, "name": "RTLSDRBlog, Blog V4", "serial": "00000001"},
    ]

    def test_default_target_picks_first(self):
        self.assertEqual(S.resolve_device(self.DEVS, None), self.DEVS[0])

    def test_target_by_index_string(self):
        self.assertEqual(S.resolve_device(self.DEVS, "1"), self.DEVS[1])

    def test_target_by_serial(self):
        self.assertEqual(S.resolve_device(self.DEVS, "00000001"), self.DEVS[1])

    def test_unknown_target_returns_none(self):
        self.assertIsNone(S.resolve_device(self.DEVS, "nope"))

    def test_no_devices_returns_none(self):
        self.assertIsNone(S.resolve_device([], None))


class UsbPortTests(unittest.TestCase):
    RECORDS = [
        {"port": "1-1", "vid": "1d6b", "pid": "0002", "serial": "", "name": "xHCI"},
        {"port": "1-1.4", "vid": "0bda", "pid": "2838", "serial": "00000001",
         "name": "Blog V4"},
        {"port": "3-1", "vid": "0bda", "pid": "2832", "serial": "00001000",
         "name": "RTL2832U"},
    ]

    def test_is_rtl_usb_by_vid_pid(self):
        self.assertTrue(S.is_rtl_usb("0bda", "2838"))
        self.assertTrue(S.is_rtl_usb("0BDA", "2832"))     # case-insensitive
        self.assertFalse(S.is_rtl_usb("1d6b", "0002"))    # a USB hub

    def test_is_rtl_usb_by_name(self):
        self.assertTrue(S.is_rtl_usb("1234", "5678", "Generic RTL2832U OEM"))

    def test_match_port_by_serial(self):
        self.assertEqual(S.match_usb_port(self.RECORDS, "00000001"), "1-1.4")
        self.assertEqual(S.match_usb_port(self.RECORDS, "00001000"), "3-1")

    def test_match_port_blank_serial_returns_none(self):
        self.assertIsNone(S.match_usb_port(self.RECORDS, ""))
        self.assertIsNone(S.match_usb_port(self.RECORDS, None))

    def test_match_port_duplicate_serial_returns_none(self):
        dupes = [
            {"port": "1-1.1", "vid": "0bda", "pid": "2838", "serial": "00000001",
             "name": "RTL2838"},
            {"port": "1-1.2", "vid": "0bda", "pid": "2838", "serial": "00000001",
             "name": "RTL2838"},
        ]
        self.assertIsNone(S.match_usb_port(dupes, "00000001"))

    def test_match_port_no_match_returns_none(self):
        self.assertIsNone(S.match_usb_port(self.RECORDS, "deadbeef"))


class FormatDongleTests(unittest.TestCase):
    ENTRY = {"index": 1, "name": "RTLSDRBlog, Blog V4", "serial": "00000001"}

    def test_full_line_with_port(self):
        line = S.format_dongle(self.ENTRY, "1-1.4")
        self.assertIn("[1]", line)
        self.assertIn("RTLSDRBlog, Blog V4", line)
        self.assertIn("SN 00000001", line)
        self.assertIn("USB 1-1.4", line)

    def test_unknown_port_shows_question_mark(self):
        self.assertIn("USB ?", S.format_dongle(self.ENTRY, None))

    def test_missing_serial_shows_dash(self):
        entry = {"index": 0, "name": "Realtek, RTL2838UHIDIR", "serial": ""}
        self.assertIn("SN —", S.format_dongle(entry, "2-1"))

    def test_no_entry_falls_back_to_default(self):
        line = S.format_dongle(None, None)
        self.assertIn("[0]", line)
        self.assertIn("USB ?", line)


class ReadUsbDevicesTests(unittest.TestCase):
    def test_reads_and_skips_interfaces_and_hubs(self):
        with tempfile.TemporaryDirectory() as root:
            # A real dongle at port 1-1.4 …
            dev = os.path.join(root, "1-1.4")
            os.mkdir(dev)
            for f, v in (("idVendor", "0bda"), ("idProduct", "2838"),
                         ("serial", "00000001"), ("product", "Blog V4")):
                with open(os.path.join(dev, f), "w") as fh:
                    fh.write(v + "\n")
            # … an interface dir (has ':') and a root hub ('usb1') must be skipped.
            os.mkdir(os.path.join(root, "1-1.4:1.0"))
            os.mkdir(os.path.join(root, "usb1"))
            recs = tune.read_usb_devices(root)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0], {"port": "1-1.4", "vid": "0bda",
                                   "pid": "2838", "serial": "00000001",
                                   "name": "Blog V4"})

    def test_missing_root_returns_empty(self):
        self.assertEqual(tune.read_usb_devices("/no/such/sysfs"), [])


class PipelineRunnerTests(unittest.TestCase):
    def test_pipeline_runner_streams_and_stops(self):
        # A stand-in for rtl_fm|sox|direwolf: emit two lines, then sleep so the
        # process is still alive for the teardown assertion.
        r = tune.PipelineRunner(
            "printf 'audio level = 40(6/6)\\n[0.1] AA>BB:hi\\n'; sleep 5")
        r.start()
        time.sleep(0.5)
        lines = []
        for _ in range(20):
            lines += r.poll_lines()
            if len(lines) >= 2:
                break
            time.sleep(0.1)
        self.assertTrue(any("audio level" in ln for ln in lines))
        self.assertTrue(any(ln.startswith("[0.1]") for ln in lines))
        self.assertTrue(r.alive())
        r.stop()
        time.sleep(0.2)
        self.assertFalse(r.alive())
        r.stop()  # idempotent — must not raise


class CliTests(unittest.TestCase):
    def test_argparser_defaults(self):
        p = tune.build_argparser()
        ns = p.parse_args([])
        self.assertEqual(ns.freq, "144.390M")
        self.assertIsNone(ns.conf)
        ns2 = p.parse_args(["--freq", "144.800M"])
        self.assertEqual(ns2.freq, "144.800M")

    def test_write_conf_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            try:
                os.chdir(d)
                conf = tune.write_conf(d)
                with open(conf) as fh:
                    body = fh.read()
            finally:
                os.chdir(cwd)
            self.assertIn("MODEM 1200", body)
            self.assertIn("ADEVICE - null", body)


class SaveResultTests(unittest.TestCase):
    def test_append_result_writes_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tune-results.json")
            rec = {"freq": "144.390M", "gain": 32.8, "ppm": 0, "vol": 0.5,
                   "srate": 24000, "decodes_per_min": 14, "ts": "2026-07-08T00:00:00"}
            tune.append_result(path, rec)
            tune.append_result(path, rec)
            with open(path) as fh:
                data = json.load(fh)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["gain"], 32.8)


class ConfTemplateTests(unittest.TestCase):
    def test_conf_declares_null_output_device(self):
        # Regression: the RX-only bench must not depend on a real audio output
        # device — Direwolf 1.7 won't start without one, and a headless Pi has
        # no ALSA 'default'. The generated conf pins output to null.
        conf = S.SDR_CONF_TEMPLATE
        self.assertIn("ADEVICE - null", conf)


if __name__ == "__main__":
    unittest.main()
