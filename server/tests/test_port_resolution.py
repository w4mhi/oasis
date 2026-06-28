#!/usr/bin/env python3
"""
test_port_resolution.py — self-tests for OASIS server port selection.

Guards the restart port-drift bug: on `systemctl restart oasis` the old gunicorn
still holds 8083 (TIME_WAIT / shutting down), so the naive `find_free_port`
probe drifted to 8084 and stranded the dashboard ("everything red"). The fix:
prefer 8083 (honoring OASIS_PORT), probe it the way gunicorn binds
(SO_REUSEADDR), and wait for it to free instead of drifting.

Run directly:  .venv/bin/python server/tests/test_port_resolution.py
(plain unittest — no pytest, to stay within the offline wheel set.)
"""

import os
import socket
import sys
import unittest
from unittest import mock

_HERE   = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.dirname(_HERE)
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import app as oasis_app   # server/app.py


def _a_free_port():
    """Ask the OS for an ephemeral free port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class PortBindableTest(unittest.TestCase):
    def test_free_port_is_bindable(self):
        self.assertTrue(oasis_app._port_bindable(_a_free_port()))


class ResolvePortTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OASIS_PORT", None)

    def tearDown(self):
        os.environ.pop("OASIS_PORT", None)

    def test_honors_oasis_port_override(self):
        os.environ["OASIS_PORT"] = "8099"
        with mock.patch.object(oasis_app, "_port_bindable", return_value=True):
            self.assertEqual(oasis_app.resolve_port(), 8099)

    def test_prefers_default_when_free(self):
        with mock.patch.object(oasis_app, "_port_bindable", return_value=True):
            self.assertEqual(oasis_app.resolve_port(preferred=8083), 8083)

    def test_waits_then_returns_preferred_when_it_frees(self):
        # Busy on the first two polls (old instance releasing), then free.
        seq = mock.Mock(side_effect=[False, False, True])
        with mock.patch.object(oasis_app, "_port_bindable", seq):
            port = oasis_app.resolve_port(preferred=8083, wait=5, poll=0.01)
        self.assertEqual(port, 8083)
        self.assertEqual(seq.call_count, 3)          # it actually waited/retried

    def test_does_not_drift_then_falls_back_if_never_frees(self):
        # Preferred never frees within the window → graceful fallback, no hang.
        with mock.patch.object(oasis_app, "_port_bindable", return_value=False), \
             mock.patch.object(oasis_app, "find_free_port", return_value=9999) as ff:
            port = oasis_app.resolve_port(preferred=8083, wait=0.05, poll=0.01)
        self.assertEqual(port, 9999)
        ff.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
