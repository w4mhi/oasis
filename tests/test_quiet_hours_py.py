import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from common import quiet_hours  # noqa: E402


class WindowTest(unittest.TestCase):
    def test_reads_the_shared_definition(self):
        self.assertEqual(quiet_hours.window(_ROOT), (22, 7))

    def test_missing_file_falls_back_rather_than_crashing(self):
        # A station with a damaged install must still decide something sane,
        # not raise inside an alert handler.
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(quiet_hours.window(d), quiet_hours.FALLBACK)

    def test_matches_the_javascript_constants(self):
        # The whole point of the shared file: if JS and Python ever disagree,
        # one alarm goes silent at 06:00 while the other chimes.
        js = open(os.path.join(_ROOT, "common", "js", "quiet-hours.js")).read()
        self.assertIn("quiet-hours.json", js,
                      "quiet-hours.js must read the shared definition")


class QuietAtTest(unittest.TestCase):
    def test_spans_midnight(self):
        for h in (22, 23, 0, 3, 6):
            self.assertTrue(quiet_hours.quiet_at(h, (22, 7)), h)
        for h in (7, 8, 12, 21):
            self.assertFalse(quiet_hours.quiet_at(h, (22, 7)), h)

    def test_boundaries(self):
        self.assertTrue(quiet_hours.quiet_at(22, (22, 7)))    # quiet begins AT 22
        self.assertFalse(quiet_hours.quiet_at(7, (22, 7)))    # and ends AT 07

    def test_empty_span_fails_loudly_rather_than_reading_as_the_fallback(self):
        # `span` is documented as None-or-a-pair, never falsy-but-present. A
        # truthiness check (`span or FALLBACK`) would silently substitute
        # 22:00-07:00 for a malformed argument nobody asked for; the explicit
        # `is None` check this guards makes that case raise instead.
        with self.assertRaises(ValueError):
            quiet_hours.quiet_at(3, ())


class QuietNowTest(unittest.TestCase):
    def test_uses_local_time_not_utc(self):
        import datetime
        # Local, deliberately: quiet hours are about when the operator is
        # asleep. At UTC-7 a UTC reading would silence 05:00-14:00 local.
        self.assertTrue(quiet_hours.quiet_now(
            _ROOT, now=datetime.datetime(2026, 8, 17, 23, 30)))
        self.assertFalse(quiet_hours.quiet_now(
            _ROOT, now=datetime.datetime(2026, 8, 17, 12, 0)))


if __name__ == "__main__":
    unittest.main()
