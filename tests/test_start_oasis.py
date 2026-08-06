import importlib.util
import io
import os
import sys
import types
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_spec = importlib.util.spec_from_file_location(
    "start_oasis", os.path.join(_ROOT, "start-oasis.py"))
start_oasis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(start_oasis)


class TailTest(unittest.TestCase):
    def test_returns_last_nonempty_lines(self):
        self.assertEqual(start_oasis._tail("a\n\nb\nc\n\n", 2), "b\nc")

    def test_empty_input(self):
        self.assertEqual(start_oasis._tail("", 3), "")


class WheelFailureMessageTest(unittest.TestCase):
    def test_names_packages_and_stderr_tail(self):
        msg = start_oasis._wheel_failure_message(
            ["psutil"], "ERROR: No matching distribution found for psutil")
        self.assertIn("psutil", msg)
        self.assertIn("No matching distribution", msg)

    def test_includes_platform_and_online_fix_hint(self):
        msg = start_oasis._wheel_failure_message(["psutil"], "")
        self.assertIn(start_oasis._platform_tag(), msg)
        self.assertIn("pip install", msg)


class EnsureVenvSurfacesFailureTest(unittest.TestCase):
    def test_exits_and_reports_when_wheel_install_fails(self):
        # A fresh venv where the offline wheel install fails (e.g. no armv7l
        # psutil wheel in the bundle). ensure_venv must NOT silently return —
        # it must surface the real pip error and exit non-zero.
        fake = types.SimpleNamespace(
            returncode=1, stdout="",
            stderr="ERROR: No matching distribution found for psutil (from versions: none)")
        stderr = io.StringIO()
        with mock.patch.object(start_oasis, "_venv_python", return_value="/x/.venv/bin/python"), \
             mock.patch.object(start_oasis, "_venv_pip", return_value="/x/.venv/bin/pip"), \
             mock.patch.object(start_oasis, "_pkg_importable", return_value=False), \
             mock.patch("subprocess.run", return_value=fake), \
             mock.patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as cm:
                start_oasis.ensure_venv()
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("psutil", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
