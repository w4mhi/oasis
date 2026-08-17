import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import announce  # noqa: E402


def _rec(**kw):
    base = {
        "event": "TOR", "event_name": "Tornado Warning", "type": "tornado",
        "areas": [{"name": "King", "state": "WA", "lat": 47.4, "lon": -121.8,
                   "why": None},
                  {"name": "Pierce", "state": "WA", "lat": 47.0, "lon": -122.1,
                   "why": None}],
        "expires": None, "clock_suspect": False, "matched": True,
    }
    base.update(kw)
    return base


class PhraseTest(unittest.TestCase):
    def test_names_the_event_and_the_counties(self):
        p = announce.phrase(_rec())
        self.assertIn("Tornado Warning", p)
        self.assertIn("King County", p)
        self.assertIn("Pierce County", p)

    def test_caps_the_county_list_so_it_stays_speakable(self):
        areas = [{"name": "C%d" % i, "state": "WA", "lat": 1, "lon": 1,
                  "why": None} for i in range(9)]
        p = announce.phrase(_rec(areas=areas))
        self.assertIn("and 5 more", p)
        self.assertLess(len(p), 300)          # common/speech.py MAX_TEXT_CHARS

    def test_a_suspect_clock_omits_the_until_clause(self):
        # Speaking a time derived from a clock we do not trust is worse than
        # speaking no time at all.
        p = announce.phrase(_rec(expires=1786971600, clock_suspect=True))
        self.assertNotIn("Until", p)

    def test_no_emoji_and_no_odd_codepoints(self):
        # Plain ASCII is the documented contract (see phrase()'s docstring),
        # not merely "no emoji" — 0x1F000 is blind to mojibake like "Ã"
        # (U+00C3), which is exactly what a double-encoded county name from
        # the old build-same-counties.py latin-1 bug would have produced.
        p = announce.phrase(_rec())
        self.assertTrue(all(ord(c) < 128 for c in p))

    def test_a_louisiana_parish_is_spoken_as_parish_not_county(self):
        # Louisiana has no counties. Speaking one as "Acadia County" is
        # simply wrong, not just non-idiomatic.
        areas = [{"name": "Acadia", "state": "LA", "lat": 30.2, "lon": -92.4,
                  "why": None, "region_type": "Parish"}]
        p = announce.phrase(_rec(areas=areas))
        self.assertIn("Acadia Parish", p)
        self.assertNotIn("Acadia County", p)

    def test_an_alaska_borough_is_spoken_as_borough_not_county(self):
        areas = [{"name": "Kenai Peninsula", "state": "AK", "lat": 60.0,
                  "lon": -151.0, "why": None, "region_type": "Borough"}]
        p = announce.phrase(_rec(areas=areas))
        self.assertIn("Kenai Peninsula Borough", p)
        self.assertNotIn("Kenai Peninsula County", p)

    def test_a_name_with_an_explicitly_bare_region_type_is_spoken_as_is(self):
        # District of Columbia / Carson City: the Gazetteer name is already
        # complete. region_type "" means "known: nothing to append", which
        # must be distinct from region_type absent ("unknown, default").
        areas = [{"name": "District of Columbia", "state": "DC", "lat": 38.9,
                  "lon": -77.0, "why": None, "region_type": ""}]
        p = announce.phrase(_rec(areas=areas))
        self.assertIn("District of Columbia", p)
        self.assertNotIn("District of Columbia County", p)

    def test_an_area_with_no_region_type_defaults_to_county(self):
        # No region_type key at all means the name came from the dsame3
        # fallback table or a bare FIPS digit string (a code the Gazetteer
        # doesn't carry), which carries no region-type information. "County"
        # is a deliberate default: correct for the vast majority of the
        # lower 48, wrong only for the rare LA/AK/PR gap in that fallback —
        # a documented tradeoff, not an accident.
        areas = [{"name": "Somewhere", "state": "TX", "lat": None, "lon": None,
                  "why": "no-coordinates"}]
        p = announce.phrase(_rec(areas=areas))
        self.assertIn("Somewhere County", p)


if __name__ == "__main__":
    unittest.main()
