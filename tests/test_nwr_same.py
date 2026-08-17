import calendar
import datetime
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from services.nwr.common import same  # noqa: E402

# Upstream's own sample alert (dsame3 "sample alert/WXR-RWT.txt"): a real
# Required Weekly Test covering nine counties across Kansas and Missouri.
SAMPLE = ("ZCZC-WXR-RWT-020103-020209-020091-020121-029047-029165-029095"
          "-029037+0030-3650000-KEAX/NWS-")


def _epoch(y, mo, d, h, mi):
    return calendar.timegm(datetime.datetime(y, mo, d, h, mi).timetuple())


class ParseHeaderTest(unittest.TestCase):
    def test_parses_the_upstream_sample(self):
        got = same.parse_header(SAMPLE)
        self.assertEqual(got["org"], "WXR")
        self.assertEqual(got["event"], "RWT")
        self.assertEqual(got["fips"], ["020103", "020209", "020091", "020121",
                                       "029047", "029165", "029095", "029037"])
        self.assertEqual(got["purge"], "0030")
        self.assertEqual(got["jjjhhmm"], "3650000")
        self.assertEqual(got["station"], "KEAX/NWS")
        self.assertEqual(got["raw"], SAMPLE)

    def test_strips_the_multimon_prefix(self):
        got = same.parse_header("EAS: " + SAMPLE)
        self.assertEqual(got["event"], "RWT")
        self.assertEqual(got["raw"], SAMPLE)   # raw is the header, not the prefix

    def test_single_county_header(self):
        got = same.parse_header("ZCZC-WXR-TOR-053033+0100-2291200-KSEW/NWS-")
        self.assertEqual(got["fips"], ["053033"])
        self.assertEqual(got["event"], "TOR")

    def test_rejects_malformed(self):
        for bad in ("", "not a header", "ZCZC-WXR-TOR+0100-2291200-KSEW/NWS-",
                    "ZCZC-WXR-TO-053033+0100-2291200-KSEW/NWS-",
                    "ZCZC-WXR-TOR-53033+0100-2291200-KSEW/NWS-"):
            self.assertIsNone(same.parse_header(bad), bad)


class NameTest(unittest.TestCase):
    def test_event_name(self):
        self.assertEqual(same.event_name("TOR"), "Tornado Warning")
        self.assertEqual(same.event_name("RWT"), "Required Weekly Test")

    def test_event_name_unknown_falls_back_to_the_code(self):
        self.assertEqual(same.event_name("ZZZ"), "ZZZ")

    def test_county_name_strips_the_subdivision_digit(self):
        # PSSCCC 053033 -> SSCCC 53033 -> King County, WA
        self.assertEqual(same.county_name("053033"), "King")
        self.assertEqual(same.state_name("053033"), "Washington")

    def test_county_name_none_for_marine_pseudo_state(self):
        # 061xxx = American Samoa Waters; the vendored table only defines
        # 61150/61151/61152, so 061999 has no entry. (057535 would NOT do
        # here: the vendored table names it "Monterey Bay" — marine zones
        # generally DO have entries, just not every possible code.)
        self.assertIsNone(same.county_name("061999"))

    def test_org_name(self):
        self.assertIn("Weather", same.org_name("WXR"))


class DeriveTimesTest(unittest.TestCase):
    def test_issued_and_expires(self):
        # Day 229 of 2026 at 12:00 UTC, valid 1 hour.
        now = _epoch(2026, 8, 17, 12, 5)
        issued, expires, suspect = same.derive_times("2291200", "0100", now)
        self.assertEqual(issued, _epoch(2026, 8, 17, 12, 0))
        self.assertEqual(expires, issued + 3600)
        self.assertFalse(suspect)

    def test_purge_minutes(self):
        now = _epoch(2026, 8, 17, 12, 5)
        _, expires, _ = same.derive_times("2291200", "0030", now)
        self.assertEqual(expires - _epoch(2026, 8, 17, 12, 0), 1800)

    def test_new_year_rollover_picks_the_nearest_year(self):
        # Day 001 heard just before midnight on 31 Dec 2026 -> Jan 2027, not Jan 2026.
        now = _epoch(2026, 12, 31, 23, 55)
        issued, _, suspect = same.derive_times("0010010", "0100", now)
        self.assertEqual(issued, _epoch(2027, 1, 1, 0, 10))
        self.assertFalse(suspect)

    def test_stale_clock_is_flagged_not_hidden(self):
        # Box thinks it is May; the header says day 229. Nearest candidate year
        # is still ~3 months away -> the clock (or the header) is not to be trusted.
        now = _epoch(2026, 5, 1, 0, 0)
        issued, expires, suspect = same.derive_times("2291200", "0100", now)
        self.assertTrue(suspect)
        self.assertIsNotNone(issued)     # still derived, never dropped
        self.assertIsNotNone(expires)


if __name__ == "__main__":
    unittest.main()
