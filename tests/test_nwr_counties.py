import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import counties  # noqa: E402

# scripts/ isn't a package and the filename has hyphens, so it can't be a
# plain import — same pattern as tests/test_set_aprs_freq.py.
_spec = importlib.util.spec_from_file_location(
    "build_same_counties",
    os.path.join(_ROOT, "scripts", "build-same-counties.py"),
)
build_same_counties = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_same_counties)

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


class MergeLegacyTest(unittest.TestCase):
    """Guards the one property the legacy supplement exists for: current
    vintage always wins. A regression here silently reintroduces "Connecticut
    alerts decode but never plot" with this suite still green."""

    def test_key_in_both_keeps_current_vintage_value(self):
        current = {"09001": {"n": "Fairfield", "s": "CT", "lat": 41.0, "lon": -73.0}}
        legacy = {"09001": {"n": "Fairfield (stale)", "s": "CT", "lat": 0.0, "lon": 0.0}}
        merged, supplement = build_same_counties.merge_legacy(current, legacy)
        self.assertEqual(merged["09001"], current["09001"])
        self.assertEqual(supplement, [])

    def test_legacy_only_key_is_added(self):
        current = {"53033": {"n": "King", "s": "WA", "lat": 47.49, "lon": -121.83}}
        legacy = {"09001": {"n": "Fairfield", "s": "CT", "lat": 41.0, "lon": -73.0}}
        merged, supplement = build_same_counties.merge_legacy(current, legacy)
        self.assertEqual(merged["09001"], legacy["09001"])
        self.assertEqual(supplement, ["09001"])

    def test_current_only_key_is_untouched(self):
        current = {"53033": {"n": "King", "s": "WA", "lat": 47.49, "lon": -121.83}}
        legacy = {"09001": {"n": "Fairfield", "s": "CT", "lat": 41.0, "lon": -73.0}}
        merged, _ = build_same_counties.merge_legacy(current, legacy)
        self.assertEqual(merged["53033"], current["53033"])

    def test_supplement_keys_report_exactly_the_legacy_only_keys(self):
        current = {"53033": {}, "09001": {}}
        legacy = {"09001": {}, "09003": {}, "46113": {}}
        _, supplement = build_same_counties.merge_legacy(current, legacy)
        self.assertEqual(supplement, ["09003", "46113"])


if __name__ == "__main__":
    unittest.main()
