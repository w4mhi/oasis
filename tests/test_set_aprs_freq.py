#!/usr/bin/env python3
# tests/test_set_aprs_freq.py — plain unittest (no pytest; offline wheel set is
# flask-only). Covers the pure ExecStart-rewrite logic of the privileged APRS
# frequency applier — no root, no systemctl, no real unit file needed.
import importlib.util
import os
import unittest

_spec = importlib.util.spec_from_file_location(
    "set_aprs_freq",
    os.path.join(os.path.dirname(__file__), "..", "features", "rtl-sdr", "set-aprs-freq.py"),
)
saf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(saf)

# A representative unit, exactly as enable-rtl-sdr.py's build_unit() emits it.
UNIT = (
    "[Unit]\n"
    "Description=RTL-SDR APRS audio feed -> GrayWolf (sdr_udp)\n"
    "After=graywolf.service\n"
    "Wants=graywolf.service\n"
    "\n"
    "[Service]\n"
    "ExecStart=/bin/sh -c 'rtl_fm -f 144.390M -M fm -s 48000 -g 28 -p 0 - "
    "| socat -u -b 1920 - UDP-SENDTO:127.0.0.1:7355'\n"
    "Restart=always\n"
    "RestartSec=5\n"
    "\n"
    "[Install]\n"
    "WantedBy=multi-user.target\n"
)


class RewriteExecStartTests(unittest.TestCase):
    def test_replaces_frequency_token(self):
        out = saf.rewrite_execstart_freq(UNIT, "144.800M")
        self.assertIn("rtl_fm -f 144.800M -M fm", out)
        self.assertNotIn("144.390M", out)

    def test_preserves_gain_ppm_port_and_rest(self):
        out = saf.rewrite_execstart_freq(UNIT, "145.175M")
        # Gain / ppm / sample rate / socat port must survive untouched.
        self.assertIn("-g 28 -p 0", out)
        self.assertIn("-s 48000", out)
        self.assertIn("UDP-SENDTO:127.0.0.1:7355", out)
        # Non-ExecStart lines untouched.
        self.assertIn("Restart=always", out)
        self.assertIn("WantedBy=multi-user.target", out)

    def test_idempotent_same_freq(self):
        out = saf.rewrite_execstart_freq(UNIT, "144.390M")
        self.assertEqual(out, UNIT)

    def test_only_rewrites_once(self):
        out = saf.rewrite_execstart_freq(UNIT, "144.800M")
        self.assertEqual(out.count("-f 144.800M"), 1)

    def test_returns_none_when_no_rtl_fm(self):
        self.assertIsNone(saf.rewrite_execstart_freq("[Service]\nExecStart=/bin/true\n", "144.800M"))


if __name__ == "__main__":
    unittest.main()
