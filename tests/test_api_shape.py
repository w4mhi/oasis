"""
common/api_shape.py — the shared §4/§6 helpers.

These were private copies in services/adsb/routes.py and services/aprs/routes.py
and were about to be copied a third time into server/routes/system.py. Two
implementations of "format a timestamp" is precisely how an API ends up with two
timestamp formats, so they now have one home and their own test rather than only
incidental coverage from whichever endpoint happens to call them.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.api_shape import clamp_limit, iso_utc, iso_utc_from_text  # noqa: E402


class IsoUtcTest(unittest.TestCase):
    def test_epoch_becomes_iso_utc_z(self):
        self.assertEqual(iso_utc(1_754_000_000), "2025-07-31T22:13:20Z")

    def test_sub_second_precision_is_dropped(self):
        self.assertEqual(iso_utc(1_754_000_000.987), "2025-07-31T22:13:20Z")

    def test_zero_is_a_real_instant_not_a_falsy_miss(self):
        # A guard written as `if not epoch` would turn the epoch itself into
        # None. It is a legitimate (if unlikely) value and must format.
        self.assertEqual(iso_utc(0), "1970-01-01T00:00:00Z")

    def test_unusable_values_are_none_not_exceptions(self):
        for bad in (None, "", "abc", [], {}, float("nan"), float("inf")):
            self.assertIsNone(iso_utc(bad), repr(bad))

    def test_numeric_strings_are_accepted(self):
        self.assertEqual(iso_utc("1754000000"), "2025-07-31T22:13:20Z")


class IsoUtcFromTextTest(unittest.TestCase):
    def test_graywolf_go_format_is_converted_to_utc(self):
        # Space separated, nanosecond precision, -07:00 offset. 20:08 at -07:00
        # is 03:08Z the NEXT day — the conversion has to move the date too.
        self.assertEqual(iso_utc_from_text("2026-08-07 20:08:04.45893926-07:00"),
                         "2026-08-08T03:08:04Z")

    def test_already_iso_utc_survives_unchanged(self):
        self.assertEqual(iso_utc_from_text("2026-08-07T20:08:04Z"),
                         "2026-08-07T20:08:04Z")

    def test_naive_input_is_assumed_utc(self):
        self.assertEqual(iso_utc_from_text("2026-08-07 20:08:04"),
                         "2026-08-07T20:08:04Z")

    def test_unparseable_input_is_none(self):
        for bad in (None, "", "   ", "not a date", "0000-00-00", 12345, []):
            self.assertIsNone(iso_utc_from_text(bad), repr(bad))

    def test_microsecond_precision_also_dropped(self):
        self.assertEqual(iso_utc_from_text("2026-08-07T20:08:04.123456Z"),
                         "2026-08-07T20:08:04Z")


class ClampLimitTest(unittest.TestCase):
    def test_a_sane_limit_passes_through(self):
        self.assertEqual(clamp_limit("25", 50, 200), 25)

    def test_over_maximum_is_capped_not_rejected(self):
        self.assertEqual(clamp_limit("100000", 50, 200), 200)

    def test_nonsense_degrades_to_the_default(self):
        # §4: never a 500, and never "quietly return everything".
        for bad in (None, "", "abc", "1.5", [], {}):
            self.assertEqual(clamp_limit(bad, 50, 200), 50, repr(bad))

    def test_zero_and_negative_degrade_to_the_default(self):
        self.assertEqual(clamp_limit("0", 50, 200), 50)
        self.assertEqual(clamp_limit("-1", 50, 200), 50)

    def test_integers_are_accepted_as_well_as_strings(self):
        self.assertEqual(clamp_limit(25, 50, 200), 25)


if __name__ == "__main__":
    unittest.main()
