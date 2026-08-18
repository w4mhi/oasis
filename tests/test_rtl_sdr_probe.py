#!/usr/bin/env python3
"""Device-aware SDR probe selection in services/rtl-feed/common/feed.py.

Covers the "add an SDR after ADS-B" bug: with two dongles present, the APRS
audio test must probe the free dongle first and never hard-grab dump1090's
device 0 (usb_claim_interface error -6)."""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEED_PATH = os.path.join(os.path.dirname(_HERE), "services", "rtl-feed", "common", "feed.py")

_spec = importlib.util.spec_from_file_location("enable_rtl_sdr", _FEED_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

from common import hardware  # noqa: E402


def _inv(devices=None, assignments=None):
    return hardware.Inventory(devices=devices or {}, assignments=assignments or {})


# Two dongles as parse_rtl_test_devices() returns them: index + serial.
REALTEK = {"index": 0, "serial": "00001000"}   # the one ADS-B owns
BLOGV4  = {"index": 1, "serial": "00000001"}   # the one just added for APRS


class DeviceBusyTest(unittest.TestCase):
    def test_usb_claim_interface_is_busy(self):
        self.assertTrue(mod._device_busy(
            "Using device 0: Generic RTL2832U\nusb_claim_interface error -6\n"))

    def test_failed_to_open_is_busy(self):
        self.assertTrue(mod._device_busy("Failed to open rtlsdr device #0.\n"))

    def test_clean_capture_is_not_busy(self):
        self.assertFalse(mod._device_busy("Output at 48000 Hz\nTuned to 144.642 MHz\n"))

    def test_empty_is_not_busy(self):
        self.assertFalse(mod._device_busy(""))


class TestCandidatesTest(unittest.TestCase):
    def test_no_assignments_keeps_order_all_free(self):
        ordered, claimed = mod.test_candidates([REALTEK, BLOGV4], _inv())
        self.assertEqual(ordered, [REALTEK, BLOGV4])
        self.assertEqual(claimed, set())

    def test_adsb_assigned_dongle_sinks_to_end(self):
        # ADS-B owns the Realtek (serial 00001000); APRS test must try the free
        # Blog V4 FIRST, dump1090's dongle only as a last resort.
        inv = _inv(
            devices={"d0": {"id": "d0", "kind": "rtl-sdr", "serial": "00001000"}},
            assignments={"adsb": "d0"},
        )
        ordered, claimed = mod.test_candidates([REALTEK, BLOGV4], inv)
        self.assertEqual(ordered, [BLOGV4, REALTEK])
        self.assertEqual(claimed, {"00001000"})

    def test_nwr_assigned_dongle_sinks_to_end(self):
        # The NOAA Weather Radio watch is the one SDR consumer that holds its
        # dongle CONTINUOUSLY (oasis-nwr runs whether or not anyone is looking),
        # so probing it first is the worst case of the very bug this ordering
        # exists to prevent. It must sink exactly like ADS-B's.
        inv = _inv(
            devices={"d0": {"id": "d0", "kind": "rtl-sdr", "serial": "00001000"}},
            assignments={"nwr": "d0"},
        )
        ordered, claimed = mod.test_candidates([REALTEK, BLOGV4], inv)
        self.assertEqual(ordered, [BLOGV4, REALTEK])
        self.assertEqual(claimed, {"00001000"})

    def test_every_rtl_sdr_service_but_aprs_can_claim(self):
        # Not a restatement of the list: the expectation comes from
        # hardware.DEVICE_KIND_FOR_SERVICE, so a new RTL-SDR service fails here
        # until feed.py can sink its dongle too.
        for svc in hardware.DEVICE_KIND_FOR_SERVICE:
            if svc == "aprs" or "rtl-sdr" not in hardware.DEVICE_KIND_FOR_SERVICE[svc]:
                continue
            with self.subTest(service=svc):
                inv = _inv(
                    devices={"d0": {"id": "d0", "kind": "rtl-sdr", "serial": "00001000"}},
                    assignments={svc: "d0"},
                )
                ordered, claimed = mod.test_candidates([REALTEK, BLOGV4], inv)
                self.assertEqual(ordered, [BLOGV4, REALTEK])
                self.assertEqual(claimed, {"00001000"})

    def test_all_dongles_claimed_still_returns_them(self):
        inv = _inv(
            devices={
                "d0": {"id": "d0", "kind": "rtl-sdr", "serial": "00001000"},
                "d1": {"id": "d1", "kind": "rtl-sdr", "serial": "00000001"},
            },
            assignments={"adsb": "d0", "satellites": "d1"},
        )
        ordered, claimed = mod.test_candidates([REALTEK, BLOGV4], inv)
        self.assertEqual(sorted(d["index"] for d in ordered), [0, 1])
        self.assertEqual(claimed, {"00001000", "00000001"})

    def test_assignment_without_serial_claims_nothing(self):
        inv = _inv(
            devices={"d0": {"id": "d0", "kind": "rtl-sdr"}},   # no serial
            assignments={"adsb": "d0"},
        )
        ordered, claimed = mod.test_candidates([REALTEK, BLOGV4], inv)
        self.assertEqual(ordered, [REALTEK, BLOGV4])
        self.assertEqual(claimed, set())

    def test_single_dongle_is_its_own_candidate(self):
        ordered, claimed = mod.test_candidates([REALTEK], _inv())
        self.assertEqual(ordered, [REALTEK])


class CaptureRmsDeviceFlagTest(unittest.TestCase):
    """capture_rms must add `-d <index>` only when pinned to a dongle."""

    def _cmd(self, device_index, recorded):
        # Stub subprocess.Popen to capture the argv without launching rtl_fm.
        class _P:
            def __init__(self, cmd, **kw):
                recorded.append(cmd)
                self.stdout = self  # read() returns b"" -> loop exits immediately

            def read(self, _n):
                return b""

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        orig = mod.subprocess.Popen
        mod.subprocess.Popen = _P
        try:
            mod.capture_rms("rtl_fm", "144.390M", 32.8, 0, 0, device_index=device_index)
        finally:
            mod.subprocess.Popen = orig

    def test_no_device_index_omits_d_flag(self):
        rec = []
        self._cmd(None, rec)
        self.assertNotIn("-d", rec[0])

    def test_device_index_adds_d_flag(self):
        rec = []
        self._cmd(1, rec)
        cmd = rec[0]
        self.assertIn("-d", cmd)
        self.assertEqual(cmd[cmd.index("-d") + 1], "1")
        self.assertEqual(cmd[-1], "-")   # stdout sink stays last


if __name__ == "__main__":
    unittest.main()
