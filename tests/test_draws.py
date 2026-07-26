import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

from common import draws


class AddOverlayLineTest(unittest.TestCase):
    def test_adds_line_when_absent(self):
        new, changed = draws.add_overlay_line("dtparam=audio=on\n")
        self.assertTrue(changed)
        self.assertIn("dtoverlay=draws", new.splitlines())

    def test_idempotent_when_present(self):
        new, changed = draws.add_overlay_line("dtoverlay=draws\n")
        self.assertFalse(changed)
        self.assertEqual(new.splitlines().count("dtoverlay=draws"), 1)

    def test_commented_line_does_not_count_as_present(self):
        new, changed = draws.add_overlay_line("# dtoverlay=draws\n")
        self.assertTrue(changed)
        self.assertIn("dtoverlay=draws", new.splitlines())


class OverlayAvailableTest(unittest.TestCase):
    def test_true_when_dtbo_present(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "draws.dtbo"), "w").close()
            self.assertTrue(draws.overlay_available(d))

    def test_false_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(draws.overlay_available(d))


class DeviceProbeTest(unittest.TestCase):
    def test_gps_device_present_true(self):
        with mock.patch("os.path.exists", return_value=True):
            self.assertTrue(draws.gps_device_present("/dev/ttySC0"))

    def test_gps_device_present_false(self):
        with mock.patch("os.path.exists", return_value=False):
            self.assertFalse(draws.gps_device_present("/dev/ttySC0"))

    def test_pps_present_checks_pps0(self):
        seen = {}
        def fake_exists(p):
            seen["path"] = p
            return True
        with mock.patch("os.path.exists", fake_exists):
            self.assertTrue(draws.pps_present())
        self.assertEqual(seen["path"], "/dev/pps0")


class EnsureOverlayTest(unittest.TestCase):
    def test_writes_line_and_reports_changed_then_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.txt")
            with open(cfg, "w") as fh:
                fh.write("dtparam=audio=on\n")
            self.assertTrue(draws.ensure_overlay(cfg))          # first: changed
            with open(cfg) as fh:
                body = fh.read()
            self.assertIn("dtoverlay=draws", body.splitlines())
            self.assertFalse(draws.ensure_overlay(cfg))         # second: no-op

    def test_raises_when_no_config(self):
        with mock.patch.object(draws, "config_path", return_value=None):
            with self.assertRaises(RuntimeError):
                draws.ensure_overlay()


if __name__ == "__main__":
    unittest.main()
