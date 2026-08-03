#!/usr/bin/env python3
import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEED_PATH = os.path.join(os.path.dirname(_HERE), "services", "rtl-feed", "common", "feed.py")

_feed_spec = importlib.util.spec_from_file_location("enable_rtl_sdr", _FEED_PATH)
mod = importlib.util.module_from_spec(_feed_spec)
_feed_spec.loader.exec_module(mod)


class AprsFeedDeviceArgsTest(unittest.TestCase):
    def test_none_yields_no_device_flag(self):
        self.assertEqual(mod.aprs_feed_device_args(None), [])

    def test_radio_port_yields_no_device_flag(self):
        self.assertEqual(mod.aprs_feed_device_args({"id": "r", "kind": "digirig"}), [])

    def test_rtl_sdr_with_serial_yields_device_flag(self):
        self.assertEqual(
            mod.aprs_feed_device_args({"id": "s", "kind": "rtl-sdr", "serial": "1090"}),
            ["-d", "1090"])

    def test_rtl_sdr_without_serial_yields_no_flag(self):
        self.assertEqual(mod.aprs_feed_device_args({"id": "s", "kind": "rtl-sdr"}), [])

    def test_apply_noop_on_non_linux(self):
        mod.apply("/tmp/x", {"id": "s", "kind": "rtl-sdr", "serial": "1090"})  # must not raise


if __name__ == "__main__":
    unittest.main()
