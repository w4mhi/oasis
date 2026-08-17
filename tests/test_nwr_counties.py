import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import counties  # noqa: E402

FIXTURE = {
    "53033": {"n": "King", "s": "WA", "lat": 47.4919, "lon": -121.8346},
    "53053": {"n": "Pierce", "s": "WA", "lat": 47.0328, "lon": -122.1387},
}


class CountiesTest(unittest.TestCase):
    def test_locate_strips_the_subdivision_digit(self):
        self.assertEqual(counties.locate("053033", table=FIXTURE),
                         (47.4919, -121.8346))

    def test_locate_none_for_marine_pseudo_state(self):
        self.assertIsNone(counties.locate("057535", table=FIXTURE))

    def test_locate_none_for_statewide_code(self):
        # CCC == 000 means "the entire state" — there is no single point to plot.
        self.assertIsNone(counties.locate("053000", table=FIXTURE))

    def test_describe(self):
        d = counties.describe("053053", table=FIXTURE)
        self.assertEqual(d["fips5"], "53053")
        self.assertEqual(d["name"], "Pierce")
        self.assertEqual(d["state"], "WA")

    def test_all_counties_sorted_by_state_then_name(self):
        rows = counties.all_counties(table=FIXTURE)
        self.assertEqual([r["name"] for r in rows], ["King", "Pierce"])
        self.assertEqual(rows[0]["fips5"], "53033")

    def test_real_table_loads_and_has_king_county(self):
        table = counties.load(_ROOT)
        self.assertGreater(len(table), 3000)
        self.assertEqual(table["53033"]["n"], "King")


if __name__ == "__main__":
    unittest.main()
