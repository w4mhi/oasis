import calendar
import datetime
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import alerts, counties, same  # noqa: E402

TOR = "ZCZC-WXR-TOR-053033-053053+0100-2291200-KSEW/NWS-"
RWT = "ZCZC-WXR-RWT-053033+0030-2291200-KSEW/NWS-"
STATEWIDE = "ZCZC-WXR-TOR-053000+0100-2291200-KSEW/NWS-"


def _epoch(y, mo, d, h, mi):
    return calendar.timegm(datetime.datetime(y, mo, d, h, mi).timetuple())


NOW = _epoch(2026, 8, 17, 12, 5)


class WatchMatchTest(unittest.TestCase):
    def test_empty_watch_list_matches_everything(self):
        self.assertTrue(alerts.watch_match(["053033"], []))
        self.assertTrue(alerts.watch_match(["012345"], None))

    def test_matches_a_watched_county(self):
        self.assertTrue(alerts.watch_match(["053033", "053053"], ["53053"]))

    def test_does_not_match_an_unwatched_county(self):
        self.assertFalse(alerts.watch_match(["053033"], ["53053"]))

    def test_statewide_code_matches_any_county_in_that_state(self):
        self.assertTrue(alerts.watch_match(["053000"], ["53033"]))

    def test_statewide_code_does_not_match_another_state(self):
        self.assertFalse(alerts.watch_match(["053000"], ["41051"]))

    def test_watch_entries_accept_either_width(self):
        self.assertTrue(alerts.watch_match(["053033"], ["053033"]))


class BuildTest(unittest.TestCase):
    def test_builds_a_plottable_tornado_record(self):
        rec = alerts.build(same.parse_header(TOR), _ROOT, ["53033"], NOW)
        self.assertEqual(rec["event"], "TOR")
        self.assertEqual(rec["event_name"], "Tornado Warning")
        self.assertEqual(rec["type"], "tornado")
        self.assertTrue(rec["matched"])
        self.assertFalse(rec["clock_suspect"])
        self.assertEqual(rec["expires"] - rec["issued"], 3600)
        self.assertEqual(len(rec["areas"]), 2)
        self.assertEqual(rec["areas"][0]["name"], "King")
        self.assertIsNotNone(rec["areas"][0]["lat"])

    def test_informational_record_has_no_type(self):
        rec = alerts.build(same.parse_header(RWT), _ROOT, ["53033"], NOW)
        self.assertIsNone(rec["type"])
        self.assertTrue(rec["matched"])       # it still matches the watch list
        self.assertEqual(rec["event_name"], "Required Weekly Test")

    def test_statewide_record_has_a_named_area_with_no_point(self):
        rec = alerts.build(same.parse_header(STATEWIDE), _ROOT, [], NOW)
        self.assertIsNone(rec["areas"][0]["lat"])
        self.assertEqual(rec["areas"][0]["why"], "statewide")

    def test_raw_fields_are_kept_beside_the_derived_ones(self):
        rec = alerts.build(same.parse_header(TOR), _ROOT, [], NOW)
        self.assertEqual(rec["raw_jjjhhmm"], "2291200")
        self.assertEqual(rec["raw_purge"], "0100")
        self.assertEqual(rec["raw"], TOR)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "configuration"))
        # The county table lives in the repo, not the temp root; point at the repo.
        self.data_root = _ROOT

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _record(self, header, now=NOW):
        return alerts.record(self.root, same.parse_header(header), [], now,
                             data_root=self.data_root)

    def test_first_record_is_added(self):
        added, rec = self._record(TOR)
        self.assertTrue(added)
        self.assertEqual(len(alerts.load(self.root)), 1)

    def test_the_same_alert_three_times_is_stored_once(self):
        # SAME transmits every message three times back to back.
        for _ in range(3):
            self._record(TOR)
        self.assertEqual(len(alerts.load(self.root)), 1)

    def test_a_different_event_is_a_different_alert(self):
        self._record(TOR)
        self._record(RWT)
        self.assertEqual(len(alerts.load(self.root)), 2)

    def test_active_excludes_expired(self):
        self._record(TOR)
        recs = alerts.load(self.root)
        self.assertEqual(len(alerts.active(recs, NOW)), 1)
        self.assertEqual(len(alerts.active(recs, NOW + 7200)), 0)

    def test_active_never_trusts_a_suspect_clock_to_retire_a_warning(self):
        rec = dict(alerts.build(same.parse_header(TOR), self.data_root, [], NOW))
        rec["clock_suspect"] = True
        rec["expires"] = NOW - 1          # would look long dead
        self.assertEqual(len(alerts.active([rec], NOW)), 1)

    def test_prune_caps_the_record_count(self):
        recs = []
        for i in range(alerts.MAX_RECORDS + 20):
            r = alerts.build(same.parse_header(TOR), self.data_root, [], NOW)
            r["id"] = f"id{i}"
            r["received"] = NOW + i
            recs.append(r)
        kept = alerts.prune(recs, NOW + 10_000)
        self.assertEqual(len(kept), alerts.MAX_RECORDS)
        self.assertEqual(kept[0]["id"], f"id{alerts.MAX_RECORDS + 19}")  # newest first

    def test_prune_drops_long_expired_records(self):
        old = alerts.build(same.parse_header(TOR), self.data_root, [], NOW)
        kept = alerts.prune([old], NOW + alerts.KEEP_EXPIRED_S + 10_000)
        self.assertEqual(kept, [])

    def test_prune_never_evicts_a_clock_suspect_record(self):
        # A stale-booted Pi, still inside its quarantine window: an `expires`
        # that would look long dead if trusted. active() keeps it regardless;
        # prune() must not let the retention cap quietly disagree.
        suspect = dict(alerts.build(same.parse_header(TOR), self.data_root, [], NOW))
        suspect["id"] = "suspect"
        suspect["clock_suspect"] = True
        suspect["received"] = NOW           # well inside STALE_CLOCK_QUARANTINE_S
        suspect["expires"] = NOW - 1
        recs = [suspect]
        for i in range(alerts.MAX_RECORDS + 50):
            r = dict(alerts.build(same.parse_header(TOR), self.data_root, [], NOW))
            r["id"] = f"id{i}"
            r["received"] = NOW + i
            recs.append(r)
        kept_ids = [r["id"] for r in alerts.prune(recs, NOW + 10_000)]
        self.assertIn("suspect", kept_ids)

    def test_prune_keeps_every_record_active_would_keep(self):
        # General property: prune() must never drop what active() promises
        # to still show on the map.
        recs = []
        for i in range(alerts.MAX_RECORDS + 30):
            r = dict(alerts.build(same.parse_header(TOR), self.data_root, [], NOW))
            r["id"] = f"id{i}"
            r["received"] = NOW + i
            if i % 7 == 0:
                r["clock_suspect"] = True
                r["received"] = 0
                r["expires"] = NOW - 1
            recs.append(r)
        later = NOW + 10_000
        active_ids = {r["id"] for r in alerts.active(recs, later)}
        kept_ids = {r["id"] for r in alerts.prune(recs, later)}
        self.assertTrue(active_ids.issubset(kept_ids))

    def test_suspect_record_younger_than_quarantine_stays_active_and_survives_prune(self):
        # A recently-decoded suspect record — the clock is still wrong, but
        # not wrong for long enough to trust `received` yet.
        rec = dict(alerts.build(same.parse_header(TOR), self.data_root, [], NOW))
        rec["clock_suspect"] = True
        rec["expires"] = NOW - 1          # would look long dead if trusted
        rec["received"] = NOW - (alerts.STALE_CLOCK_QUARANTINE_S - 3600)
        self.assertEqual(len(alerts.active([rec], NOW)), 1)
        kept = alerts.prune([rec], NOW)
        self.assertEqual(len(kept), 1)

    def test_suspect_record_far_older_than_quarantine_is_no_longer_active_and_is_pruned(self):
        # `now` is far enough past `received` that the record is old under
        # any reading of the clock — the quarantine may honestly end.
        rec = dict(alerts.build(same.parse_header(TOR), self.data_root, [], NOW))
        rec["clock_suspect"] = True
        rec["expires"] = NOW - 1
        rec["received"] = NOW - (alerts.STALE_CLOCK_QUARANTINE_S + 3600)
        self.assertEqual(alerts.active([rec], NOW), [])
        kept = alerts.prune([rec], NOW)
        self.assertEqual(kept, [])

    def test_prune_never_evicts_what_active_keeps_across_the_quarantine_boundary(self):
        # Mixed set: young suspect (still active), old suspect (quarantined
        # out), and ordinary records. The invariant must hold throughout.
        recs = []
        for i in range(30):
            r = dict(alerts.build(same.parse_header(TOR), self.data_root, [], NOW))
            r["id"] = f"id{i}"
            if i % 3 == 0:
                r["clock_suspect"] = True
                r["received"] = NOW - (alerts.STALE_CLOCK_QUARANTINE_S - 3600)
                r["expires"] = NOW - 1
            elif i % 3 == 1:
                r["clock_suspect"] = True
                r["received"] = NOW - (alerts.STALE_CLOCK_QUARANTINE_S + 3600)
                r["expires"] = NOW - 1
            else:
                r["received"] = NOW + i
            recs.append(r)
        active_ids = {r["id"] for r in alerts.active(recs, NOW)}
        kept_ids = {r["id"] for r in alerts.prune(recs, NOW)}
        self.assertTrue(active_ids.issubset(kept_ids))

    def test_prune_keeps_all_active_records_even_past_the_cap(self):
        # Degenerate case: more active (clock-suspect, still inside their
        # quarantine window) records than MAX_RECORDS. We keep all of them —
        # a live-warning pile is exactly what this store exists to survive,
        # and the cap only governs the inactive remainder.
        recs = []
        for i in range(alerts.MAX_RECORDS + 15):
            r = dict(alerts.build(same.parse_header(TOR), self.data_root, [], NOW))
            r["id"] = f"suspect{i}"
            r["clock_suspect"] = True
            r["received"] = NOW           # well inside STALE_CLOCK_QUARANTINE_S
            r["expires"] = NOW - 1
            recs.append(r)
        kept = alerts.prune(recs, NOW + 10_000)
        self.assertEqual(len(kept), alerts.MAX_RECORDS + 15)


class AreaWhyTest(unittest.TestCase):
    def setUp(self):
        self.table = counties.load(_ROOT)

    def test_a_known_county_has_no_why(self):
        area = alerts._area("053033", self.table)
        self.assertIsNone(area["why"])

    def test_statewide_code_is_statewide(self):
        area = alerts._area("053000", self.table)
        self.assertEqual(area["why"], "statewide")

    def test_marine_pseudo_state_is_marine_not_no_coordinates(self):
        # 057 = "Pacific Coast from Washington to California" — a real,
        # legitimate alert area that structurally has no county.
        area = alerts._area("057530", self.table)
        self.assertEqual(area["why"], "marine")

    def test_a_county_absent_from_the_gazetteer_is_no_coordinates(self):
        # A syntactically plausible county FIPS for a real land state that
        # our vendored Gazetteer vintage simply doesn't carry.
        area = alerts._area("999999", self.table)
        self.assertEqual(area["why"], "no-coordinates")


if __name__ == "__main__":
    unittest.main()
