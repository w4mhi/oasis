import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
import listen  # noqa: E402


class ListenTest(unittest.TestCase):
    def test_recordings_dir(self):
        self.assertEqual(listen.recordings_dir("/x/oasis"),
                         "/x/oasis/configuration/sat-recordings")

    def test_mhz_to_hz(self):
        self.assertEqual(listen.mhz_to_hz(137.100), 137100000)
        self.assertEqual(listen.mhz_to_hz(145.825), 145825000)
        self.assertEqual(listen.mhz_to_hz(436.795), 436795000)

    def test_record_command_mirrors_proven_rtl_fm(self):
        cmd = listen.record_command(137100000, "/tmp/a b.wav",
                                    gain="40", ppm="0", srate=48000, max_seconds=1200)
        self.assertIn("rtl_fm -f 137100000 -M fm -s 48000 -g 40 -p 0 -", cmd)
        self.assertIn("timeout 1200 rtl_fm", cmd)   # bounded run finalises the WAV
        self.assertIn("sox -t raw -r 48000 -e signed-integer -b 16 -c 1 -", cmd)
        self.assertIn("'/tmp/a b.wav'", cmd)         # output path shell-quoted
        self.assertIn(" | sox", cmd)

    def test_missing_deps_injectable(self):
        self.assertEqual(listen.missing_deps(lambda b: None), ["rtl_fm", "sox", "timeout"])
        self.assertEqual(listen.missing_deps(lambda b: "/usr/bin/" + b), [])

    def test_feed_active_injectable(self):
        self.assertTrue(listen.feed_active(lambda u: u == "aprs-sdr-feed"))
        self.assertFalse(listen.feed_active(lambda u: False))

    def test_preconditions_no_deps(self):
        p = listen.preconditions(which=lambda b: None, run=None, is_active=lambda u: False)
        self.assertEqual(p["missing_deps"], ["rtl_fm", "sox", "timeout"])
        self.assertFalse(p["dongle_present"])   # missing deps -> never "present"
        self.assertFalse(p["feed_active"])

    def test_status_idle(self):
        s = listen.status()
        self.assertFalse(s["recording"])
        self.assertIsNone(s["norad"])
        self.assertEqual(s["seconds"], 0)

    def test_stop_idempotent_when_idle(self):
        self.assertEqual(listen.stop(), {"recording": False})


if __name__ == "__main__":
    unittest.main()
