#!/usr/bin/env python3
"""
test_feed_flow.py — self-tests for the APRS SDR feed flow probe (/api/health/feed-flow).

The endpoint passively sniffs the loopback feed with `sudo -n tcpdump …` to prove
audio datagrams are actually flowing (a yanked dongle leaves socat "active" but
silent). These tests never spawn tcpdump: they force Linux, stub `_resolve_tcpdump`,
and patch `subprocess.run` to simulate a healthy feed, a silent feed (timeout), a
missing tcpdump, and a missing sudo grant. They also pin the exact argv so it can't
silently drift out of sync with the sudoers rule in enable-service-controls.py.

Run directly:  .venv/bin/python tests/test_feed_flow.py
(plain unittest — no pytest, to stay within the offline wheel set: flask only.)
"""

import os
import subprocess
import sys
import unittest
from unittest import mock

_HERE   = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.join(os.path.dirname(_HERE), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import app as oasis_app   # server/app.py

_FAKE_TCPDUMP = "/usr/bin/tcpdump"


def _completed(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(args=["sudo"], returncode=rc,
                                       stdout=stdout, stderr=stderr)


class TestFeedFlow(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.client = oasis_app.app.test_client()
        # Force the Linux + tcpdump-present path; individual tests drive subprocess.
        self._p_platform = mock.patch.object(oasis_app.sys, "platform", "linux")
        self._p_tcpdump  = mock.patch.object(oasis_app, "_resolve_tcpdump",
                                             return_value=_FAKE_TCPDUMP)
        self._p_platform.start()
        self._p_tcpdump.start()

    def tearDown(self):
        self._p_platform.stop()
        self._p_tcpdump.stop()

    def test_flowing_feed_reports_pps(self):
        lines = "\n".join(f"packet {i}" for i in range(oasis_app._FEED_FLOW_NPKTS))
        with mock.patch.object(oasis_app.subprocess, "run",
                               return_value=_completed(stdout=lines, rc=0)):
            d = self.client.get("/api/health/feed-flow").get_json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["flowing"])
        self.assertEqual(d["packets"], oasis_app._FEED_FLOW_NPKTS)
        self.assertGreater(d["pps"], 0)
        self.assertEqual(d["port"], oasis_app.FEED_FLOW_PORT)

    def test_silent_feed_on_timeout_is_not_an_error(self):
        # Dead/yanked dongle: tcpdump captures nothing and we hit the timeout.
        exc = subprocess.TimeoutExpired(cmd="tcpdump", timeout=1.0,
                                        output="", stderr="")
        with mock.patch.object(oasis_app.subprocess, "run", side_effect=exc):
            d = self.client.get("/api/health/feed-flow").get_json()
        self.assertTrue(d["ok"])            # a valid result, not a fault
        self.assertFalse(d["flowing"])
        self.assertEqual(d["packets"], 0)
        self.assertEqual(d["pps"], 0)

    def test_missing_tcpdump(self):
        with mock.patch.object(oasis_app, "_resolve_tcpdump", return_value=None):
            d = self.client.get("/api/health/feed-flow").get_json()
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "tcpdump-missing")

    def test_missing_sudo_grant_is_distinguished(self):
        denied = _completed(stdout="", stderr="sudo: a password is required", rc=1)
        with mock.patch.object(oasis_app.subprocess, "run", return_value=denied):
            d = self.client.get("/api/health/feed-flow").get_json()
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "no-privilege")

    def test_non_linux_unsupported(self):
        with mock.patch.object(oasis_app.sys, "platform", "darwin"):
            d = self.client.get("/api/health/feed-flow").get_json()
        self.assertFalse(d["ok"])
        self.assertFalse(d["supported"])
        self.assertEqual(d["reason"], "not-linux")

    def test_argv_is_pinned_and_injection_free(self):
        # The privileged argv MUST stay token-for-token identical to the OASIS_SNIFF
        # sudoers Cmnd, with the port baked in (no client input on the sudo path).
        captured = {}

        def _capture(argv, *a, **k):
            captured["argv"] = argv
            return _completed(stdout="x\n", rc=0)

        with mock.patch.object(oasis_app.subprocess, "run", side_effect=_capture):
            # A bogus query param must not reach the command line.
            self.client.get("/api/health/feed-flow?port=9999&iface=eth0")
        self.assertEqual(captured["argv"],
                         ["sudo", "-n", _FAKE_TCPDUMP, "-ni", "lo", "-l", "-c",
                          str(oasis_app._FEED_FLOW_NPKTS), "udp", "port",
                          str(oasis_app.FEED_FLOW_PORT)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
