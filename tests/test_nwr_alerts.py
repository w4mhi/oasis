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
from services.nwr.common import alerts, same  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
