# tests/test_sdr_tune.py — plain unittest (no pytest; offline wheel set is flask-only).
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from common import sdr_tune as S


class BuildPipelineTests(unittest.TestCase):
    def test_build_pipeline_24k(self):
        cmd = S.build_pipeline("144.390M", "32.8", 0, "0.50", 24000, "/tmp/sdr.conf")
        self.assertEqual(cmd, (
            "rtl_fm -M fm -f 144.390M -s 24000 -F 0 -g 32.8 -p 0 - "
            "| sox -t raw -r 24000 -e signed-integer -b 16 -c 1 - -t raw - vol 0.50 "
            "| direwolf -c /tmp/sdr.conf -r 24000 -D 1 -"
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


if __name__ == "__main__":
    unittest.main()
