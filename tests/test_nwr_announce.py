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
        p = announce.phrase(_rec())
        self.assertTrue(all(ord(c) < 0x1F000 for c in p))


if __name__ == "__main__":
    unittest.main()
