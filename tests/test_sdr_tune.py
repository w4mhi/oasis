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

    def test_device_help_absent(self):
        self.assertIn("No RTL-SDR dongle", S.device_help("absent"))


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
