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
        self.assertNotIn(" -d ", cmd)                # no device pin when serial omitted

    def test_record_command_pins_dongle_by_serial(self):
        # Multi-dongle Pi: rtl_fm must target the assigned dongle by serial, else
        # it grabs index 0 (often another service's dongle) and dies on startup.
        cmd = listen.record_command(137100000, "/tmp/a.wav", device_serial="00000001")
        self.assertIn("rtl_fm -d 00000001 -f 137100000", cmd)

    def test_missing_deps_injectable(self):
        self.assertEqual(listen.missing_deps(lambda b: None), ["rtl_fm", "sox", "timeout"])
        self.assertEqual(listen.missing_deps(lambda b: "/usr/bin/" + b), [])

    def test_mode_support_fm_family(self):
        for m in ("FM voice", "APRS", "APT", "fm"):
            self.assertTrue(listen.mode_support(m)["supported"], m)
            self.assertEqual(listen.mode_support(m)["demod"], "fm")

    def test_mode_support_unsupported(self):
        for m in ("LRPT", "SSB", "USB", "CW", ""):
            s = listen.mode_support(m)
            self.assertFalse(s["supported"], m)
            self.assertIsNone(s["demod"])
            self.assertTrue(s["blurb"])          # always explains why

    def test_is_active_wrapper_bridges_recorder(self):
        w = listen.is_active_wrapper(lambda u: u == "dump1090-fa")
        self.assertTrue(w("dump1090-fa"))        # delegates to base
        self.assertFalse(w("satellites-listen")) # not recording -> False
        self.assertFalse(w("aprs-sdr-feed"))

    def test_dongle_busy_same_dongle(self):
        from common import hardware
        inv = hardware.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}},
                                 assignments={"aprs": "a", "satellites": "a"})
        busy, holder = listen.dongle_busy(inv, lambda u: u == "aprs-sdr-feed")
        self.assertTrue(busy)
        self.assertEqual(holder, "aprs")

    def test_dongle_free_when_other_service_on_different_dongle(self):
        from common import hardware
        inv = hardware.Inventory(
            devices={"a": {"id": "a", "kind": "rtl-sdr"}, "b": {"id": "b", "kind": "rtl-sdr"}},
            assignments={"adsb": "a", "satellites": "b"})
        busy, holder = listen.dongle_busy(inv, lambda u: u == "dump1090-fa")
        self.assertFalse(busy)                   # ADS-B is on dongle a, not ours
        self.assertIsNone(holder)

    def test_dongle_busy_global_fallback_when_unassigned(self):
        from common import hardware
        inv = hardware.Inventory(devices={}, assignments={})
        busy, holder = listen.dongle_busy(inv, lambda u: u == "openwebrx")
        self.assertTrue(busy)
        self.assertEqual(holder, "openwebrx")

    def test_preconditions_no_deps(self):
        p = listen.preconditions(which=lambda b: None, run=None,
                                 is_active=lambda u: False, inv=None)
        self.assertEqual(p["missing_deps"], ["rtl_fm", "sox", "timeout"])
        self.assertFalse(p["dongle_present"])   # missing deps -> never "present"
        self.assertFalse(p["busy"])
        self.assertIsNone(p["holder"])

    def test_status_idle(self):
        s = listen.status()
        self.assertFalse(s["recording"])
        self.assertIsNone(s["norad"])
        self.assertEqual(s["seconds"], 0)

    def test_stop_idempotent_when_idle(self):
        self.assertEqual(listen.stop(), {"recording": False})


if __name__ == "__main__":
    unittest.main()
