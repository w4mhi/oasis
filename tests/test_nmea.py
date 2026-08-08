#!/usr/bin/env python3
"""Unit tests for common/nmea.py — the NMEA/gpsd verification layer shared by
every GPS feature (features/gps-L76X, features/draws-gps). Pure functions only;
the serial/gpspipe I/O wrappers are exercised on the Pi."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import nmea


class ChecksumTests(unittest.TestCase):
    RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"

    def test_valid_checksum_accepted(self):
        self.assertTrue(nmea.nmea_checksum_ok(self.RMC))

    def test_corrupt_checksum_rejected(self):
        self.assertFalse(nmea.nmea_checksum_ok(self.RMC.replace("*6A", "*00")))


class ParseTests(unittest.TestCase):
    RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"

    def test_rmc_reports_position_and_active_fix(self):
        r = nmea.parse_gprmc(self.RMC)
        self.assertTrue(r["fix"])
        self.assertAlmostEqual(r["lat"], 48.1173, places=3)

    def test_gga_reports_quality_and_sat_count(self):
        g = nmea.parse_gpgga(self.GGA)
        self.assertEqual(g["fix_quality"], 1)
        self.assertEqual(g["num_sats"], 8)

    def test_wrong_sentence_type_returns_none(self):
        self.assertIsNone(nmea.parse_gprmc(self.GGA))


class DeviceMismatchTests(unittest.TestCase):
    def test_different_device_is_a_mismatch(self):
        self.assertTrue(nmea.device_mismatch("/dev/ttyUSB0", "/dev/ttySC0"))

    def test_none_configured_is_not_a_mismatch(self):
        self.assertFalse(nmea.device_mismatch(None, "/dev/ttySC0"))


class SummarizeNmeaTests(unittest.TestCase):
    """summarize_nmea() is the pure core of verify(): it answers 'is this GPS
    talking, and does it have a fix?' from a list of raw lines."""
    RMC_FIX = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    RMC_VOID = "$GPRMC,123519,V,,,,,,,230394,,*33"
    GGA_FIX = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    GGA_NOFIX = "$GPGGA,123519,,,,,0,01,,,M,,M,,*6A"

    def test_counts_lines_and_sentences(self):
        s = nmea.summarize_nmea(["garbage", self.GGA_FIX, self.RMC_FIX])
        self.assertEqual(s["lines"], 3)
        self.assertEqual(s["sentences"], 2)

    def test_reports_fix_when_gga_quality_nonzero(self):
        s = nmea.summarize_nmea([self.GGA_FIX, self.RMC_FIX])
        self.assertTrue(s["has_fix"])
        self.assertEqual(s["gga"]["num_sats"], 8)

    def test_no_fix_but_talking_is_distinguished_from_silence(self):
        # The exact DRAWS bench case: NMEA flowing, quality 0, status V, 1 sat.
        s = nmea.summarize_nmea([self.GGA_NOFIX, self.RMC_VOID])
        self.assertTrue(s["talking"])
        self.assertFalse(s["has_fix"])
        self.assertEqual(s["gga"]["num_sats"], 1)

    def test_silence_is_not_talking(self):
        s = nmea.summarize_nmea([])
        self.assertFalse(s["talking"])
        self.assertFalse(s["has_fix"])

    def test_uses_the_most_recent_sentence(self):
        # A fix acquired mid-read must win over the earlier no-fix sentence.
        s = nmea.summarize_nmea([self.GGA_NOFIX, self.GGA_FIX])
        self.assertTrue(s["has_fix"])

    def test_ignores_corrupt_checksums(self):
        s = nmea.summarize_nmea([self.GGA_FIX.replace("*47", "*00")])
        self.assertEqual(s["sentences"], 0)
        self.assertIsNone(s["gga"])


class SummarizeGpsdTests(unittest.TestCase):
    """summarize_gpsd_json() is the pure core of verify_via_gpsd(): read gpsd's
    JSON stream rather than fighting it for the raw serial device."""
    TPV_FIX = ('{"class":"TPV","mode":3,"lat":47.6,"lon":-122.3,'
               '"time":"2026-08-06T12:00:00.000Z"}')
    SKY_NO_FIX = ('{"class":"SKY","satellites":[{"PRN":1,"used":false},'
                  '{"PRN":2,"used":true}]}')

    def test_reports_a_fix_from_tpv(self):
        s = nmea.summarize_gpsd_json(self.TPV_FIX)
        self.assertIsNotNone(s["fix"])
        self.assertEqual(s["fix"]["mode"], 3)

    def test_reports_satellites_in_use_without_a_fix(self):
        s = nmea.summarize_gpsd_json(self.SKY_NO_FIX)
        self.assertIsNone(s["fix"])
        self.assertTrue(s["saw_sky"])
        self.assertEqual(s["sats_seen"], 2)
        self.assertEqual(s["sats_used"], 1)

    def test_empty_sky_is_zero_seen_not_just_zero_used(self):
        """The distinction this whole split exists for: a SKY report with an
        empty satellite list means the receiver hears NOTHING (antenna fault),
        which counting only `used` made indistinguishable from acquiring."""
        s = nmea.summarize_gpsd_json('{"class":"SKY","satellites":[]}')
        self.assertTrue(s["saw_sky"])
        self.assertEqual(s["sats_seen"], 0)
        self.assertEqual(s["sats_used"], 0)

    def test_sky_without_a_satellites_key_does_not_crash(self):
        s = nmea.summarize_gpsd_json('{"class":"SKY"}')
        self.assertTrue(s["saw_sky"])
        self.assertEqual(s["sats_seen"], 0)

    def test_counts_are_the_max_across_reports_not_the_last(self):
        """A warming-up receiver's counts wobble; a low final sample must not
        erase the fact that it saw satellites a moment earlier."""
        s = nmea.summarize_gpsd_json(self.SKY_NO_FIX + "\n"
                                     + '{"class":"SKY","satellites":[]}')
        self.assertEqual(s["sats_seen"], 2)
        self.assertEqual(s["sats_used"], 1)

    def test_no_data_is_neither(self):
        s = nmea.summarize_gpsd_json("")
        self.assertIsNone(s["fix"])
        self.assertFalse(s["saw_sky"])
        self.assertEqual(s["sats_seen"], 0)

    def test_ignores_non_json_noise(self):
        s = nmea.summarize_gpsd_json("gpspipe: warning\n" + self.TPV_FIX + "\n{bad")
        self.assertIsNotNone(s["fix"])


class ReportSkyTests(unittest.TestCase):
    """_report_sky() routes the operator to one of three DIFFERENT places, so
    what matters is which severity fires — a hardware fault must not be narrated
    as 'give it a minute'."""

    def _say(self, seen, used):
        said = []
        with mock.patch.object(nmea, "_warn", lambda m: said.append(("warn", m))), \
             mock.patch.object(nmea, "_info", lambda m: said.append(("info", m))):
            nmea._report_sky(seen, used)
        self.assertEqual(len(said), 1)
        return said[0]

    def test_nothing_in_view_warns_about_the_antenna(self):
        level, msg = self._say(0, 0)
        self.assertEqual(level, "warn")
        self.assertIn("antenna", msg.lower())

    def test_seen_but_unused_is_informational_acquiring(self):
        level, msg = self._say(6, 0)
        self.assertEqual(level, "info")
        self.assertIn("6", msg)

    def test_partial_use_says_it_needs_four(self):
        level, msg = self._say(6, 3)
        self.assertEqual(level, "info")
        self.assertIn("4", msg)


if __name__ == "__main__":
    unittest.main()
