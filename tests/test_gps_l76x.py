#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "features", "gps-L76X"))
import gps_l76x as G


class ConfigTxtTests(unittest.TestCase):
    def test_enable_uart_added_once(self):
        text, changed = G.transform_config_txt("dtparam=i2c_arm=on\n")
        self.assertTrue(changed)
        self.assertIn("enable_uart=1", text)

    def test_enable_uart_idempotent(self):
        text, _ = G.transform_config_txt("dtparam=i2c_arm=on\n")
        text2, changed2 = G.transform_config_txt(text)
        self.assertFalse(changed2)
        self.assertEqual(text, text2)

    def test_commented_enable_uart_is_not_active(self):
        # A commented-out line must not be treated as already enabled.
        text, changed = G.transform_config_txt("#enable_uart=1\n")
        self.assertTrue(changed)
        self.assertEqual(text.count("enable_uart=1"), 2)  # the comment + the real one

    def test_pps_overlay_added_once(self):
        text, changed = G.transform_config_txt_pps("enable_uart=1\n", enable=True)
        self.assertTrue(changed)
        self.assertIn("dtoverlay=pps-gpio,gpiopin=4", text)
        text2, changed2 = G.transform_config_txt_pps(text, enable=True)
        self.assertFalse(changed2)


class CmdlineTxtTests(unittest.TestCase):
    def test_strips_serial0_console_token(self):
        cmd = ("console=serial0,115200 console=tty1 root=PARTUUID=1234 "
               "rootfstype=ext4 fsck.repair=yes rootwait quiet")
        new_cmd, changed = G.transform_cmdline_txt(cmd)
        self.assertTrue(changed)
        self.assertNotIn("console=serial0", new_cmd)
        self.assertIn("console=tty1", new_cmd)

    def test_noop_when_no_serial_console(self):
        cmd = "console=tty1 root=PARTUUID=1234 rootwait quiet"
        new_cmd, changed = G.transform_cmdline_txt(cmd)
        self.assertFalse(changed)


class NmeaParseTests(unittest.TestCase):
    RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"

    def test_checksum_valid(self):
        self.assertTrue(G.nmea_checksum_ok(self.RMC))
        self.assertTrue(G.nmea_checksum_ok(self.GGA))

    def test_checksum_invalid(self):
        self.assertFalse(G.nmea_checksum_ok(self.RMC.replace("*6A", "*00")))

    def test_parse_gprmc_fix_and_position(self):
        r = G.parse_gprmc(self.RMC)
        self.assertTrue(r["fix"])
        self.assertAlmostEqual(r["lat"], 48.1173, places=3)
        self.assertAlmostEqual(r["lon"], 11.5167, places=3)

    def test_parse_gprmc_void_fix(self):
        void = self.RMC.replace(",A,", ",V,")
        r = G.parse_gprmc(void)
        self.assertFalse(r["fix"])

    def test_parse_gpgga_sats_and_quality(self):
        g = G.parse_gpgga(self.GGA)
        self.assertEqual(g["num_sats"], 8)
        self.assertEqual(g["fix_quality"], 1)
        self.assertAlmostEqual(g["altitude_m"], 545.4, places=1)

    def test_parse_rejects_wrong_sentence_type(self):
        self.assertIsNone(G.parse_gprmc(self.GGA))
        self.assertIsNone(G.parse_gpgga(self.RMC))

    def test_southern_western_hemisphere_is_negative(self):
        sydney = "$GPRMC,000000,A,3352.000,S,15113.000,E,0,0,010101,,*00"
        r = G.parse_gprmc(sydney)
        self.assertLess(r["lat"], 0)
        self.assertGreater(r["lon"], 0)


class DeviceMismatchTests(unittest.TestCase):
    def test_different_device_is_a_mismatch(self):
        # The exact trap: gpsd left pointing at a USB dongle from features/gps.
        self.assertTrue(G.device_mismatch("/dev/ttyUSB0", "/dev/ttyS0"))

    def test_same_device_is_not_a_mismatch(self):
        self.assertFalse(G.device_mismatch("/dev/ttyS0", "/dev/ttyS0"))

    def test_none_configured_is_not_a_mismatch(self):
        # gpsd not configured yet — nothing to conflict with.
        self.assertFalse(G.device_mismatch(None, "/dev/ttyS0"))
        self.assertFalse(G.device_mismatch("", "/dev/ttyS0"))


if __name__ == "__main__":
    unittest.main()
