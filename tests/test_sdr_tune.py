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


if __name__ == "__main__":
    unittest.main()
