"""Unit tests for services/satellites/connector.py.

No dongle and no rtl_connector binary anywhere in here: the argv builder is pure,
and the process lifecycle is exercised with injected fakes. What the bench still
owes is in the spec's §11.2.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))

import connector  # noqa: E402  — pure module, no optional deps


class ArgvTest(unittest.TestCase):
    def test_the_shape_confirmed_against_0_6_2(self):
        argv = connector.connector_argv(437_848_000, 240_000, port=4590,
                                        gain="40", ppm="0")
        self.assertEqual(argv, ["rtl_connector", "-f", "437848000",
                                "-s", "240000", "-g", "40", "-P", "0",
                                "-p", "4590"])

    def test_the_dongle_is_pinned_by_serial(self):
        """An index on a multi-dongle Pi is whichever device enumerated first —
        usually another service's. -d takes a serial and that is what we have."""
        argv = connector.connector_argv(437_848_000, 240_000,
                                        device_serial="00000042")
        self.assertEqual(argv[1:3], ["-d", "00000042"])

    def test_no_device_flag_when_no_serial(self):
        self.assertNotIn("-d", connector.connector_argv(100e6, 240_000))

    def test_no_control_socket_is_ever_requested(self):
        """Doppler is a software shift, never a retune, so a running connector
        never needs retuning. Probing 0.6.2 found no message form that worked;
        rather than reverse-engineer a protocol we do not need, we do not open
        the port. If -c ever appears here, something has misunderstood the
        design."""
        self.assertNotIn("-c", connector.connector_argv(437e6, 240_000))

    def test_rtltcp_is_never_requested(self):
        """--rtltcp gives 8-bit rtl_tcp compatibility and loses the float IQ the
        chain reads."""
        argv = connector.connector_argv(437e6, 240_000)
        self.assertNotIn("-r", argv)
        self.assertNotIn("--rtltcp", argv)

    def test_floats_are_coerced_to_whole_hz(self):
        argv = connector.connector_argv(437_848_000.7, 240_000.0)
        self.assertIn("437848000", argv)
        self.assertIn("240000", argv)

    def test_command_string_is_quoted(self):
        cmd = connector.connector_command(437e6, 240_000, device_serial="a b")
        self.assertIn("'a b'", cmd)


class DistPackagesTest(unittest.TestCase):
    """The venv blocker: python3-csdr is a dpkg package in dist-packages and the
    OASIS venv is built without --system-site-packages, so `import pycsdr` fails
    after a completely successful apt install."""

    def test_missing_directory_is_not_added(self):
        path = []
        self.assertFalse(connector.enable_dist_packages("/no/such/dir", path))
        self.assertEqual(path, [])

    def test_added_once_and_only_once(self):
        path = ["/somewhere"]
        d = os.path.dirname(os.path.abspath(__file__))
        self.assertTrue(connector.enable_dist_packages(d, path))
        self.assertTrue(connector.enable_dist_packages(d, path))
        self.assertEqual(path.count(d), 1)

    def test_appended_never_prepended(self):
        """Nothing in dist-packages may shadow a venv package — the venv is the
        project's dependency contract and this is one narrow exception to it."""
        path = ["/venv/site-packages"]
        d = os.path.dirname(os.path.abspath(__file__))
        connector.enable_dist_packages(d, path)
        self.assertEqual(path[0], "/venv/site-packages")
        self.assertEqual(path[-1], d)


class WaitForPortTest(unittest.TestCase):
    def test_returns_as_soon_as_the_socket_answers(self):
        calls = []
        def probe(port, host):
            calls.append(port)
            return len(calls) >= 3
        self.assertTrue(connector.wait_for_port(
            4590, timeout=10, sleep=lambda s: None,
            now=lambda: len(calls) * 0.1, probe=probe))
        self.assertEqual(len(calls), 3)

    def test_gives_up_and_says_so(self):
        t = [0.0]
        def now():
            t[0] += 0.5
            return t[0]
        self.assertFalse(connector.wait_for_port(
            4590, timeout=2, sleep=lambda s: None, now=now,
            probe=lambda p, h: False))

    def test_uses_a_monotonic_clock(self):
        """An offline station can step its wall clock the moment GPS or an RTC
        arrives. A timeout measured against it could wait forever or not at all —
        the same reason doppler curves are indexed by seconds since capture start
        rather than by timestamps."""
        import time
        self.assertIs(connector.wait_for_port.__defaults__[3], time.monotonic)


def _present(binary):
    """A `which` that says the DSP stack is installed, so the lifecycle tests
    run on a laptop that has never seen rtl_connector."""
    return "/usr/bin/" + binary


class _FakeProc:
    def __init__(self, alive=True, stderr=b""):
        self.pid = -1
        self._alive = alive
        self.terminated = False
        import io
        self.stderr = io.BytesIO(stderr)

    def poll(self):
        return None if self._alive else 1

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.c = connector.Connector(437_848_000, 240_000, device_serial="00000042")

    def test_start_returns_the_data_port_once_the_socket_is_live(self):
        proc = _FakeProc()
        port = self.c.start(popen=lambda *a, **k: proc, wait=lambda p, **k: True,
                            which=_present)
        self.assertEqual(port, connector.DEFAULT_PORT)
        self.assertTrue(self.c.is_running())

    def test_a_socket_that_never_opens_raises_and_cleans_up(self):
        proc = _FakeProc()
        with self.assertRaises(RuntimeError) as cm:
            self.c.start(popen=lambda *a, **k: proc, wait=lambda p, **k: False,
                         which=_present)
        self.assertIn("did not open port", str(cm.exception))
        self.assertFalse(self.c.is_running())     # no orphan left behind

    def test_a_process_that_died_reports_its_own_last_words(self):
        """A connector that could not claim the dongle exits immediately. Saying
        'usb_claim_interface error -6' beats a generic timeout, because the two
        have completely different fixes."""
        proc = _FakeProc(alive=False, stderr=b"Found 2 device(s):\nusb_claim_interface error -6\n")
        with self.assertRaises(RuntimeError) as cm:
            self.c.start(popen=lambda *a, **k: proc, wait=lambda p, **k: False,
                         which=_present)
        self.assertIn("usb_claim_interface error -6", str(cm.exception))

    def test_starting_twice_is_refused(self):
        self.c.start(popen=lambda *a, **k: _FakeProc(), wait=lambda p, **k: True,
                     which=_present)
        with self.assertRaises(RuntimeError) as cm:
            self.c.start(popen=lambda *a, **k: _FakeProc(), wait=lambda p, **k: True,
                     which=_present)
        self.assertIn("already running", str(cm.exception))

    def test_stop_is_idempotent_and_never_raises(self):
        self.c.start(popen=lambda *a, **k: _FakeProc(), wait=lambda p, **k: True,
                     which=_present)
        self.c.stop()
        self.c.stop()
        self.assertFalse(self.c.is_running())

    def test_stop_on_a_connector_that_never_started_is_fine(self):
        connector.Connector(437e6, 240_000).stop()

    def test_a_missing_binary_names_the_installer(self):
        with self.assertRaises(RuntimeError) as cm:
            self.c.start(popen=lambda *a, **k: _FakeProc(),
                         wait=lambda p, **k: True, which=lambda b: None)
        self.assertIn("install-dsp.py", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
