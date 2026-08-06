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



class PttBcmParsingTest(unittest.TestCase):
    """The DRAWS ports sit on different BCM lines (left 12, right 23), so the
    sysfs GPIO computation can no longer assume BCM 12."""

    def test_parses_gpio_token(self):
        self.assertEqual(winlink.ptt_bcm({"ptt": "gpio23"}), 23)
        self.assertEqual(winlink.ptt_bcm({"ptt": "gpio12"}), 12)

    def test_falls_back_to_the_default_bcm(self):
        self.assertEqual(winlink.ptt_bcm({}), winlink.MODEM_PTT_BCM)
        self.assertEqual(winlink.ptt_bcm({"ptt": ""}), winlink.MODEM_PTT_BCM)

    def test_ignores_a_serial_path_ptt(self):
        """digirig PTT is a serial by-id path, not a GPIO token."""
        self.assertEqual(winlink.ptt_bcm({"ptt": "/dev/serial/by-id/usb-x-if00"}),
                         winlink.MODEM_PTT_BCM)

    def test_compute_ptt_gpio_accepts_a_bcm_argument(self):
        """Off-Pi this returns None; the point is the signature accepts bcm and
        an explicit override still wins."""
        self.assertEqual(winlink.compute_ptt_gpio(override=999, bcm=23), 999)


class DrawsRadioPortTest(unittest.TestCase):
    def test_draws_maps_to_the_draws_card(self):
        adev, ptt = winlink.radio_port_config(
            {"id": "draws-right", "kind": "draws", "ptt": "gpio23",
             "alsa": "draws", "channel": 1})
        self.assertEqual(adev, winlink.MODEM_DRAWS_ADEVICE)
        self.assertTrue(ptt is None or isinstance(ptt, int))

    def test_both_ports_are_accepted(self):
        for pid, gpio in (("draws-left", "gpio12"), ("draws-right", "gpio23")):
            adev, _ = winlink.radio_port_config(
                {"id": pid, "kind": "draws", "ptt": gpio, "alsa": "draws"})
            self.assertEqual(adev, winlink.MODEM_DRAWS_ADEVICE, pid)

    def test_apply_noop_on_non_linux(self):
        winlink.apply("/tmp/x", {"id": "draws-right", "kind": "draws",
                                 "ptt": "gpio23", "channel": 1})  # must not raise


class PatRadioPortTest(unittest.TestCase):
    """pat hardcoded agwpe.radio_port = 0. DRAWS Winlink rides channel 1 of the
    shared 2-channel Direwolf, so it must be derivable from the device."""

    def test_defaults_to_channel_zero(self):
        self.assertEqual(winlink.pat_radio_port(None), 0)
        self.assertEqual(winlink.pat_radio_port({"kind": "dra-pi"}), 0)

    def test_draws_uses_the_ports_channel(self):
        self.assertEqual(winlink.pat_radio_port(
            {"kind": "draws", "channel": 1}), 1)
        self.assertEqual(winlink.pat_radio_port(
            {"kind": "draws", "channel": 0}), 0)


if __name__ == "__main__":
    unittest.main()
