"""The pure mode classifier, and the roster-seat policy built on it.

This module exists so the roster aggregator can ask "could this station ever do
anything with this transmission?" without importing the recorder. The tests here
guard the classification itself; test_satnogs.py guards what build_records does
with the answer.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
import demod  # noqa: E402
import listen  # noqa: E402


class DemodParamsTest(unittest.TestCase):
    def test_the_fm_family_is_wide_fm(self):
        for mode in ("FM", "NFM", "SSTV", "APRS", "APT", "AFSK1k2", "GMSK USP"):
            self.assertEqual(demod.demod_params(mode)[0], "fm", mode)

    def test_cw_is_usb_tuned_low_so_the_carrier_beats(self):
        self.assertEqual(demod.demod_params("CW"), ("usb", 12000, -demod.CW_OFFSET_HZ))

    def test_sideband_modes(self):
        self.assertEqual(demod.demod_params("USB")[0], "usb")
        self.assertEqual(demod.demod_params("SSB")[0], "usb")
        self.assertEqual(demod.demod_params("LSB")[0], "lsb")

    def test_phase_shift_keying_and_digital_video_are_not_demodulable(self):
        # The whole reason the roster gate exists. BPSK must not be caught by the
        # "MSK"/"FSK" prefixes in the FM family -- startswith on a token would
        # match if the tokens were tested as substrings.
        for mode in ("BPSK1k2", "QPSK", "DVB-S2", "PSK31"):
            self.assertIsNone(demod.demod_params(mode)[0], mode)

    def test_junk_is_not_demodulable_and_does_not_throw(self):
        for mode in (None, "", "   ", "???"):
            self.assertIsNone(demod.demod_params(mode)[0])


class RosterWorthyTest(unittest.TestCase):
    def test_anything_demodulable_earns_a_seat(self):
        for mode in ("FM", "SSTV", "CW", "USB", "APRS"):
            self.assertTrue(demod.roster_worthy(mode), mode)

    def test_lrpt_earns_a_seat_despite_being_undemodulable_today(self):
        # Deliberate: LRPT is weather imagery we intend to record now and decode
        # offline later. Dropping those birds would need internet to undo, which
        # is exactly the situation the roster has to survive.
        self.assertIsNone(demod.demod_params("LRPT")[0])
        self.assertTrue(demod.roster_worthy("LRPT"))

    def test_bare_telemetry_earns_nothing(self):
        for mode in ("BPSK1k2", "QPSK", "DVB-S2"):
            self.assertFalse(demod.roster_worthy(mode), mode)

    def test_junk_earns_nothing(self):
        for mode in (None, "", "???"):
            self.assertFalse(demod.roster_worthy(mode))


class ReExportTest(unittest.TestCase):
    """listen.py's public surface must not change when the classifier moves.

    routes.py, the tests and mode_support all call listen.demod_params; the
    extraction is an internal move, and anything that makes callers care where it
    lives has failed."""

    def test_listen_still_exposes_the_classifier(self):
        self.assertEqual(listen.demod_params("fm"), ("fm", 48000, 0))
        self.assertEqual(listen.demod_params("CW"), ("usb", 12000, -700))
        self.assertIs(listen.demod_params, demod.demod_params)

    def test_mode_support_still_classifies_through_it(self):
        self.assertTrue(listen.mode_support("FM")["supported"])
        self.assertFalse(listen.mode_support("BPSK1k2")["supported"])
        # LRPT is roster-worthy but NOT supported: the seat and the demodulator
        # are different questions, and collapsing them would offer a Record
        # button that produces a WAV of noise.
        self.assertFalse(listen.mode_support("LRPT")["supported"])


if __name__ == "__main__":
    unittest.main()
