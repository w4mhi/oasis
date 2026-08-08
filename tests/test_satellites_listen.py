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

    def test_stream_command_prefers_ffmpeg_mp3_no_timeout(self):
        # everything present → ffmpeg wins
        cmd, mime = listen.stream_command(137100000, gain="40", ppm="0",
                                          srate=48000, which=lambda b: "/usr/bin/" + b)
        self.assertEqual(mime, "audio/mpeg")
        self.assertIn("rtl_fm -f 137100000 -M fm -s 48000 -g 40 -p 0 -", cmd)
        self.assertNotIn("timeout ", cmd)              # live stream runs unbounded
        self.assertIn("ffmpeg", cmd)
        self.assertIn("-f mp3", cmd)
        self.assertIn(" | ffmpeg", cmd)

    def test_stream_command_falls_back_to_sox(self):
        cmd, mime = listen.stream_command(   # no ffmpeg → sox
            137100000, which=lambda b: "/usr/bin/sox" if b == "sox" else None)
        self.assertEqual(mime, "audio/mpeg")
        self.assertIn(" | sox ", cmd)
        self.assertIn("-t mp3", cmd)

    def test_stream_command_no_encoder_returns_none(self):
        cmd, mime = listen.stream_command(137100000, which=lambda b: None)
        self.assertIsNone(cmd)
        self.assertIsNone(mime)

    def test_stream_command_pins_dongle_by_serial(self):
        cmd, _ = listen.stream_command(137100000, device_serial="00000001",
                                       which=lambda b: "/usr/bin/" + b)
        self.assertIn("rtl_fm -d 00000001 -f 137100000", cmd)

    def test_demod_params(self):
        self.assertEqual(listen.demod_params("fm"), ("fm", 48000, 0))
        self.assertEqual(listen.demod_params("aprs"), ("fm", 48000, 0))
        self.assertEqual(listen.demod_params("CW"), ("usb", 12000, -700))
        self.assertEqual(listen.demod_params("usb"), ("usb", 12000, 0))
        self.assertEqual(listen.demod_params("ssb"), ("usb", 12000, 0))
        self.assertEqual(listen.demod_params("lsb"), ("lsb", 12000, 0))
        # FM family, matched on messy tokens.
        self.assertEqual(listen.demod_params("AFSK1k2"), ("fm", 48000, 0))
        self.assertEqual(listen.demod_params("GMSK USP"), ("fm", 48000, 0))
        self.assertEqual(listen.demod_params("SSTV"), ("fm", 48000, 0))
        # Not live-demodulable.
        self.assertEqual(listen.demod_params("LRPT"), (None, None, None))
        self.assertEqual(listen.demod_params("PSK"), (None, None, None))

    def test_record_command_cw_uses_usb_with_offset(self):
        # CW → USB demod, narrow rate, tuned 700 Hz low so the carrier is audible.
        cmd = listen.record_command(145900000, "/tmp/cw.wav", gain="40", ppm="0", dmode="cw")
        self.assertIn("rtl_fm -f 145899300 -M usb -s 12000", cmd)
        self.assertIn("sox -t raw -r 12000", cmd)

    def test_stream_command_ssb_uses_usb_no_offset(self):
        cmd, mime = listen.stream_command(437800000, dmode="usb",
                                          which=lambda b: "/usr/bin/" + b)
        self.assertEqual(mime, "audio/mpeg")
        self.assertIn("rtl_fm -f 437800000 -M usb -s 12000", cmd)   # SSB: no CW offset

    def test_mode_support_cw_ssb_now_supported(self):
        for m in ("CW", "USB", "LSB", "SSB", "FM", "APRS", "AFSK1k2", "SSTV", "GMSK USP"):
            self.assertTrue(listen.mode_support(m)["supported"], m)
        self.assertEqual(listen.mode_support("CW")["demod"], "usb")
        self.assertEqual(listen.mode_support("LSB")["demod"], "lsb")
        self.assertEqual(listen.mode_support("AFSK1k2")["demod"], "fm")
        # Digital-image / PSK are still unsupported for live demod.
        self.assertFalse(listen.mode_support("LRPT")["supported"])
        self.assertFalse(listen.mode_support("PSK")["supported"])

    def test_mode_support_fm_family(self):
        for m in ("FM voice", "APRS", "APT", "fm"):
            self.assertTrue(listen.mode_support(m)["supported"], m)
            self.assertEqual(listen.mode_support(m)["demod"], "fm")

    def test_mode_support_unsupported(self):
        for m in ("LRPT", "DVB", ""):            # CW/SSB are now supported (see below)
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


class RadioCaptureTest(unittest.TestCase):
    """Satellite RX on a DRAWS radio port. An SDR retunes itself; a radio does
    not, and the DRAWS has no CAT — so the operator parks the radio on the
    downlink and OASIS just records the audio. Capture rides the SHARED pcm so
    the TNC keeps working the other port during a pass."""

    def test_records_one_channel_to_mono(self):
        cmd = listen.radio_record_command("/tmp/pass.wav", channel=1,
                                          srate=48000, max_seconds=600)
        self.assertIn("arecord -D draws_shared_in", cmd)
        self.assertIn("-c 2 -f S16_LE -r 48000", cmd)   # capture stereo...
        self.assertIn("remix 2", cmd)                    # ...keep the RIGHT one

    def test_channel_index_maps_to_one_based_remix(self):
        """direwolf channels are 0-based, sox remix is 1-based — off by one here
        silently records the WRONG radio."""
        self.assertIn("remix 1", listen.radio_record_command("/o.wav", channel=0))
        self.assertIn("remix 2", listen.radio_record_command("/o.wav", channel=1))

    def test_run_is_time_bounded_like_the_sdr_path(self):
        cmd = listen.radio_record_command("/tmp/a.wav", max_seconds=900)
        self.assertIn("timeout 900 arecord", cmd)

    def test_quotes_paths_with_spaces(self):
        self.assertIn("'/tmp/a b.wav'", listen.radio_record_command("/tmp/a b.wav"))

    def test_uses_the_shared_pcm_not_the_raw_device(self):
        """plughw:draws,0 would take the card exclusively and kill the TNC."""
        cmd = listen.radio_record_command("/tmp/a.wav")
        self.assertIn(listen.RADIO_PCM, cmd)
        self.assertNotIn("plughw:draws", cmd)


class RadioPreconditionsTest(unittest.TestCase):
    def test_reports_missing_tools(self):
        pre = listen.radio_preconditions(which=lambda t: None)
        self.assertEqual(sorted(pre["missing_deps"]), ["arecord", "sox"])

    def test_does_not_require_rtl_sdr_tools(self):
        """The SDR checks (rtl_fm, dongle present/busy) do not apply here."""
        pre = listen.radio_preconditions(which=lambda t: "/usr/bin/" + t)
        self.assertEqual(pre["missing_deps"], [])
        self.assertNotIn("dongle_present", pre)

    def test_detects_the_card(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(" 3 [draws          ]: simple-card - draws\n")
            path = fh.name
        pre = listen.radio_preconditions(cards_path=path,
                                         which=lambda t: "/usr/bin/" + t)
        self.assertTrue(pre["card_present"])
        os.unlink(path)

    def test_absent_card_is_reported_not_raised(self):
        pre = listen.radio_preconditions(cards_path="/nonexistent",
                                         which=lambda t: "/usr/bin/" + t)
        self.assertFalse(pre["card_present"])
