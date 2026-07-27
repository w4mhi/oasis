import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "draws_gps",
    os.path.join(os.path.dirname(_HERE), "features", "draws-gps", "draws_gps.py"),
)
draws_gps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(draws_gps)


class RemovalRecordTest(unittest.TestCase):
    def test_strips_overlay_line_and_flags_reboot(self):
        rec = draws_gps.removal_record()
        self.assertEqual(rec["config_lines"], ["dtoverlay=draws"])
        self.assertTrue(rec["requires_reboot"])
        self.assertTrue(any("shared" in n for n in rec["notes"]))


class ExitCodeTest(unittest.TestCase):
    def test_reboot_when_overlay_changed(self):
        self.assertEqual(draws_gps.decide_exit_code(True, False), 10)

    def test_reboot_when_device_absent(self):
        self.assertEqual(draws_gps.decide_exit_code(False, False), 10)

    def test_zero_when_present_and_unchanged(self):
        self.assertEqual(draws_gps.decide_exit_code(False, True), 0)


if __name__ == "__main__":
    unittest.main()
