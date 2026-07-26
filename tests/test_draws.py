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


if __name__ == "__main__":
    unittest.main()
