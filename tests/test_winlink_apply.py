#!/usr/bin/env python3
"""
test_winlink_apply.py — self-tests for the Winlink device-binding hook.

radio_port_config() is a pure function: given an assigned radio-port device
dict, it returns the (adevice, ptt) pair Direwolf should use. apply() is the
Linux/root-only writer that reuses the existing write_direwolf_config /
write_digirig_config functions — no config-generation logic is duplicated.

Run directly:  .venv/bin/python tests/test_winlink_apply.py
(plain unittest — no pytest.)
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from services.winlink.common import winlink


class RadioPortConfigTest(unittest.TestCase):
    def test_dra_pi_maps_to_dra_adevice_and_gpio(self):
        adev, ptt = winlink.radio_port_config({"id": "hf", "kind": "dra-pi", "ptt": "gpio12"})
        self.assertEqual(adev, winlink.MODEM_ADEVICE)
        # compute_ptt_gpio() returns an int sysfs GPIO number (or None off-Pi).
        self.assertTrue(ptt is None or isinstance(ptt, int))

    def test_digirig_maps_to_usb_adevice_and_serial_ptt(self):
        adev, ptt = winlink.radio_port_config(
            {"id": "2m", "kind": "digirig", "alsa": "Digirig",
             "ptt": "/dev/serial/by-id/usb-x-if00"})
        self.assertIsNotNone(adev)
        self.assertIn("/dev/serial/by-id/", ptt)

    def test_non_radio_port_yields_none(self):
        self.assertEqual(winlink.radio_port_config({"id": "s", "kind": "rtl-sdr"}), (None, None))
        self.assertEqual(winlink.radio_port_config(None), (None, None))

    def test_apply_noop_on_non_linux(self):
        winlink.apply("/tmp/x", {"id": "hf", "kind": "dra-pi", "ptt": "gpio12"})  # must not raise


if __name__ == "__main__":
    unittest.main()
