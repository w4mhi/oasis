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

# Entries carrying a region-type tag, for describe()'s region_type passthrough.
FIXTURE_TYPED = {
    "22001": {"n": "Acadia", "s": "LA", "lat": 30.2, "lon": -92.4, "t": "Parish"},
    "11001": {"n": "District of Columbia", "s": "DC", "lat": 38.9, "lon": -77.0,
              "t": ""},
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

    def test_describe_passes_through_a_real_region_type(self):
        d = counties.describe("022001", table=FIXTURE_TYPED)
        self.assertEqual(d["region_type"], "Parish")

    def test_describe_passes_through_an_explicitly_bare_region_type(self):
        # DC's Gazetteer name is already complete — describe() must not
        # invent a suffix, so the stored "" comes through as "", not None.
        d = counties.describe("011001", table=FIXTURE_TYPED)
        self.assertEqual(d["region_type"], "")

    def test_describe_region_type_is_none_when_the_table_lacks_it(self):
        # Old-shape entries (no "t" key, e.g. test fixtures or a stale table)
        # must not crash describe() — None signals "unknown region type" to
        # the caller, same as a genuine Gazetteer/dsame3 miss.
        d = counties.describe("053033", table=FIXTURE)
        self.assertIsNone(d["region_type"])


# A picker's-eye view of the table: two counties in one state, one in another,
# and two names that share a prefix so ordering is observable.
PICKER = {
    "53033": {"n": "King", "s": "WA", "lat": 47.5, "lon": -121.8},
    "53053": {"n": "Pierce", "s": "WA", "lat": 47.0, "lon": -122.1},
    "48273": {"n": "Kleberg", "s": "TX", "lat": 27.4, "lon": -97.7},
    "06029": {"n": "Kern", "s": "CA", "lat": 35.3, "lon": -118.7},
    "01073": {"n": "Jefferson", "s": "AL", "lat": 33.5, "lon": -86.9},
    "42049": {"n": "Erie", "s": "PA", "lat": 42.1, "lon": -80.1},
}


class SearchTest(unittest.TestCase):
    """The watch-list picker's filter. Unfiltered, this route was 3234 entries
    and 174,986 bytes on every request, on a box whose supported minimum is a
    Pi 3 — and a picker types into it."""

    def _fips(self, rows):
        return [r["fips5"] for r in rows]

    def test_an_empty_query_is_the_whole_table_in_all_counties_order(self):
        self.assertEqual(counties.search("", table=PICKER),
                         counties.all_counties(PICKER))
        self.assertEqual(counties.search(None, table=PICKER),
                         counties.all_counties(PICKER))

    def test_a_two_letter_name_prefix_narrows_it(self):
        rows = counties.search("ki", table=PICKER)
        self.assertEqual(self._fips(rows), ["53033"])

    def test_prefix_matches_come_before_substring_matches(self):
        # "er" starts Erie and appears inside Jefferson, Kern, Kleberg and
        # Pierce. Erie is in PA, which sorts LAST of those states — so a plain
        # state-then-name sort would bury the one county the operator was
        # obviously typing. A picker that does that is one nobody types into
        # twice.
        rows = counties.search("er", table=PICKER)
        self.assertEqual(self._fips(rows)[0], "42049")
        self.assertEqual(self._fips(rows)[1:], ["01073", "06029", "48273", "53053"],
                         "the substring group, ordered by state then name")

    def test_a_bare_state_code_is_tried_as_a_state_too(self):
        rows = counties.search("wa", table=PICKER)
        self.assertEqual(sorted(self._fips(rows)), ["53033", "53053"])

    def test_the_comma_form_narrows_by_state(self):
        self.assertEqual(self._fips(counties.search("k, wa", table=PICKER)),
                         ["53033"])
        # No name at all: the state IS the query.
        self.assertEqual(self._fips(counties.search(", wa", table=PICKER)),
                         ["53033", "53053"])

    def test_a_fips_prefix_matches(self):
        self.assertEqual(self._fips(counties.search("5303", table=PICKER)),
                         ["53033"])

    def test_the_query_is_case_insensitive(self):
        self.assertEqual(counties.search("KING, WA", table=PICKER),
                         counties.search("king, wa", table=PICKER))

    def test_no_match_is_an_empty_list_not_an_error(self):
        self.assertEqual(counties.search("zzzz", table=PICKER), [])

    def test_an_absurd_query_is_truncated_rather_than_scanned(self):
        # Nobody types 400 characters into a county picker.
        self.assertEqual(counties.search("k" * 400, table=PICKER), [])

    def test_ordering_is_deterministic(self):
        # Contract §4: the same input always yields the same order.
        self.assertEqual(counties.search("k", table=PICKER),
                         counties.search("k", table=PICKER))


class ResolveTest(unittest.TestCase):
    """The watch list's other direction: it is STORED as codes and read back
    as names."""

    def test_codes_become_names_sorted_by_state_then_name(self):
        rows, unknown = counties.resolve(["53053", "01073", "53033"], PICKER)
        self.assertEqual([r["fips5"] for r in rows], ["01073", "53033", "53053"])
        self.assertEqual(unknown, [])

    def test_the_six_digit_same_form_is_accepted(self):
        rows, unknown = counties.resolve(["053033"], PICKER)
        self.assertEqual(rows[0]["fips5"], "53033")
        self.assertEqual(unknown, [])

    def test_duplicates_collapse(self):
        rows, _ = counties.resolve(["53033", "053033", "53033"], PICKER)
        self.assertEqual(len(rows), 1)

    def test_a_code_with_no_entry_is_reported_not_dropped(self):
        # 51560 is one of the four codes this Gazetteer vintage lost; marine
        # zones were never in it. Not being able to NAME or PLOT an area is a
        # display fact, not a reason to refuse to watch it.
        rows, unknown = counties.resolve(["53033", "51560", "02201"], PICKER)
        self.assertEqual([r["fips5"] for r in rows], ["53033"])
        self.assertEqual(unknown, ["02201", "51560"])

    def test_nothing_asked_is_nothing_answered(self):
        self.assertEqual(counties.resolve([], PICKER), ([], []))
        self.assertEqual(counties.resolve(None, PICKER), ([], []))


class MojibakeGuardTest(unittest.TestCase):
    """Regression guard for the double-encoding bug: build-same-counties.py
    once decoded a UTF-8 Census download as latin-1 unconditionally, turning
    every accented name (Doña Ana NM, 16 Puerto Rico municipios) into
    mojibake in the COMMITTED table that ships to every box. This must never
    come back silently."""

    def setUp(self):
        self.table = counties.load(_ROOT)

    def test_no_name_contains_the_mojibake_tell(self):
        # "Ã" (U+00C3) is what a UTF-8 continuation-byte lead byte looks like
        # when a UTF-8 file is wrongly decoded as latin-1 — the cheap,
        # specific tell for exactly this bug.
        bad = sorted(k for k, v in self.table.items() if "Ã" in v["n"])
        self.assertEqual(bad, [], f"mojibake county names: {bad}")

    def test_every_name_round_trips_through_utf8(self):
        # Stronger than the tell above: a name that was mis-decoded may not
        # always contain "Ã" (e.g. some sequences decode to other Latin-1
        # supplement junk), but a correctly-decoded UTF-8 string must always
        # survive an encode/decode round trip unchanged.
        for k, v in self.table.items():
            name = v["n"]
            self.assertEqual(name.encode("utf-8").decode("utf-8"), name,
                             f"{k}: {name!r} does not round-trip as UTF-8")

    def test_dona_ana_new_mexico_decodes_correctly(self):
        self.assertEqual(self.table["35013"]["n"], "Doña Ana")

    def test_bayamon_puerto_rico_decodes_correctly(self):
        self.assertEqual(self.table["72021"]["n"], "Bayamón")


class BuildRegionTypeTest(unittest.TestCase):
    """build() must both strip the region suffix from the display name AND
    remember what it stripped, so announce.py can speak "Acadia Parish"
    instead of "Acadia County". Louisiana has no counties; Alaska boroughs
    and census areas aren't counties either."""

    HEADER = "USPS\tGEOID\tANSICODE\tNAME\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG"

    def _table(self, *rows):
        return build_same_counties.build("\n".join([self.HEADER, *rows]))

    def _row(self, usps, geoid, name, lat="30.0", lon="-90.0"):
        return f"{usps}\t{geoid}\t01\t{name}\t1\t1\t1\t1\t{lat}\t{lon}"

    def test_louisiana_parish_keeps_parish_as_region_type(self):
        t = self._table(self._row("LA", "22001", "Acadia Parish"))
        self.assertEqual(t["22001"]["n"], "Acadia")
        self.assertEqual(t["22001"]["t"], "Parish")

    def test_alaska_borough_keeps_borough_as_region_type(self):
        t = self._table(self._row("AK", "02122", "Kenai Peninsula Borough"))
        self.assertEqual(t["02122"]["n"], "Kenai Peninsula")
        self.assertEqual(t["02122"]["t"], "Borough")

    def test_alaska_census_area_keeps_census_area_as_region_type(self):
        t = self._table(self._row("AK", "02158", "Kusilvak Census Area"))
        self.assertEqual(t["02158"]["n"], "Kusilvak")
        self.assertEqual(t["02158"]["t"], "Census Area")

    def test_alaska_city_and_borough_is_not_truncated_by_the_borough_suffix(self):
        # Regression: "Borough" is a suffix of "City and Borough" too, so a
        # naive suffix list checked in the wrong order strips only "Borough"
        # and leaves "Juneau City and" — a truncated, garbage name, the same
        # class of bug as the mojibake defect (an operator-visible county
        # name that is simply wrong).
        t = self._table(self._row("AK", "02110", "Juneau City and Borough"))
        self.assertEqual(t["02110"]["n"], "Juneau")
        self.assertEqual(t["02110"]["t"], "City and Borough")

    def test_puerto_rico_municipio_is_spoken_as_municipality(self):
        # The Census spells the PR suffix "Municipio"; English-language NWS
        # broadcasts and press usage call these "Municipality" — the spoken
        # word intentionally differs from the stripped Census suffix text.
        t = self._table(self._row("PR", "72021", "Bayamón Municipio"))
        self.assertEqual(t["72021"]["n"], "Bayamón")
        self.assertEqual(t["72021"]["t"], "Municipality")

    def test_plain_county_keeps_county_as_region_type(self):
        t = self._table(self._row("WA", "53033", "King County"))
        self.assertEqual(t["53033"]["n"], "King")
        self.assertEqual(t["53033"]["t"], "County")

    def test_virginia_independent_city_is_spoken_as_city(self):
        t = self._table(self._row("VA", "51710", "Norfolk city"))
        self.assertEqual(t["51710"]["n"], "Norfolk")
        self.assertEqual(t["51710"]["t"], "City")

    def test_connecticut_planning_region_keeps_its_name(self):
        t = self._table(self._row("CT", "09110", "Capitol Planning Region"))
        self.assertEqual(t["09110"]["n"], "Capitol")
        self.assertEqual(t["09110"]["t"], "Planning Region")

    def test_a_name_with_no_strippable_suffix_gets_an_explicitly_bare_type(self):
        # District of Columbia and Carson City NV are already complete names
        # in the Gazetteer — "t" must come back as "" (known: nothing to
        # say), not absent (which announce.py reads as "unknown, guess
        # County").
        t = self._table(self._row("DC", "11001", "District of Columbia"))
        self.assertEqual(t["11001"]["n"], "District of Columbia")
        self.assertEqual(t["11001"]["t"], "")


class DecodeFallbackTest(unittest.TestCase):
    """The Census Gazetteer download is UTF-8 in the vintages we've checked,
    but the generator has to tolerate a genuinely latin-1 vintage too (the
    2012 legacy file used for retired FIPS codes decodes as latin-1, not
    UTF-8) rather than assume one encoding forever."""

    def test_utf8_bytes_decode_as_utf8(self):
        raw = "Doña Ana".encode("utf-8")
        self.assertEqual(build_same_counties._decode(raw), "Doña Ana")

    def test_latin1_bytes_that_are_not_valid_utf8_fall_back_to_latin1(self):
        raw = "Doña Ana".encode("latin-1")
        self.assertEqual(build_same_counties._decode(raw), "Doña Ana")


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
