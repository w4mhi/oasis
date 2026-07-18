#!/usr/bin/env python3
"""Unit tests for the e-ink screen-1 ADS-B fold + APRS/ADS-B merge (oasis_client).

Pure logic — no Pillow, no network (the one HTTP helper is monkeypatched).
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_EINK = os.path.join(os.path.dirname(_HERE), "displays", "e-ink")
if _EINK not in sys.path:
    sys.path.insert(0, _EINK)

import oasis_client as api  # noqa: E402

CFG = {"oasis": {"base_url": "http://127.0.0.1:8083", "http_timeout_s": 5}}
NOW = 1_700_000_000


class TestAircraftRecord(unittest.TestCase):
    def test_skips_positionless(self):
        self.assertIsNone(api._aircraft_record({"flight": "ASA424"}, NOW))
        self.assertIsNone(api._aircraft_record({"lat": 47.6, "flight": "X"}, NOW))
        self.assertIsNone(api._aircraft_record({"lon": -122.3, "flight": "X"}, NOW))

    def test_folds_core_fields(self):
        r = api._aircraft_record(
            {"hex": "a1b2c3", "flight": "ASA424 ", "lat": 47.6, "lon": -122.3,
             "gs": 400, "track": 90, "alt_baro": 34000, "squawk": "1200",
             "seen": 5}, NOW)
        self.assertEqual(r["source"], "adsb")
        self.assertEqual(r["callsign"], "ASA424")          # trimmed
        self.assertEqual(r["speed_mph"], round(400 * 1.15078))
        self.assertEqual(r["course"], 90)
        self.assertEqual(r["alt_ft"], 34000)
        self.assertEqual(r["squawk"], "1200")
        self.assertEqual(r["hex"], "a1b2c3")

    def test_callsign_falls_back_to_hex(self):
        r = api._aircraft_record({"hex": "abc", "lat": 1, "lon": 2}, NOW)
        self.assertEqual(r["callsign"], "abc")

    def test_ground_and_missing_alt_preserved(self):
        r = api._aircraft_record({"lat": 1, "lon": 2, "alt_baro": "ground"}, NOW)
        self.assertEqual(r["alt_ft"], "ground")
        r2 = api._aircraft_record({"lat": 1, "lon": 2}, NOW)
        self.assertIsNone(r2["alt_ft"])

    def test_seen_maps_to_older_than_ts(self):
        recent = api._aircraft_record({"lat": 1, "lon": 2, "seen": 1}, NOW)
        old = api._aircraft_record({"lat": 1, "lon": 2, "seen": 600}, NOW)
        self.assertGreater(api._parse_iso(recent["last_heard"]),
                           api._parse_iso(old["last_heard"]))


class TestAircraftStatus(unittest.TestCase):
    def _patch(self, payload):
        api_get = api._get_json
        self.addCleanup(lambda: setattr(api, "_get_json", api_get))
        api._get_json = lambda base, path, timeout: (True, payload)

    def test_filters_and_sorts(self):
        self._patch({"ok": True, "now": NOW, "aircraft": [
            {"hex": "old", "lat": 1, "lon": 2, "seen": 300},
            {"hex": "nopos", "seen": 1},                       # dropped
            {"hex": "new", "lat": 3, "lon": 4, "seen": 2},
        ]})
        st = api.aircraft_status(CFG)
        self.assertTrue(st["ok"])
        self.assertEqual(st["count"], 2)                      # nopos skipped
        self.assertEqual(st["list"][0]["hex"], "new")         # most recent first

    def test_service_down_is_silent_empty(self):
        get = api._get_json
        self.addCleanup(lambda: setattr(api, "_get_json", get))
        api._get_json = lambda base, path, timeout: (False, None)
        st = api.aircraft_status(CFG)
        self.assertEqual(st, {"ok": False, "count": 0, "list": []})


class TestOperatorTag(unittest.TestCase):
    TABLE = {"ASA": "ALASKA", "DAL": "DELTA"}

    def test_prefix_before_digit(self):
        self.assertEqual(api.operator_tag("ASA424", self.TABLE), "ALASKA")
        self.assertEqual(api.operator_tag("asa415", self.TABLE), "ALASKA")  # cased
        self.assertEqual(api.operator_tag("DAL1 ", self.TABLE), "DELTA")

    def test_registration_and_unknown_are_blank(self):
        self.assertEqual(api.operator_tag("N172SP", self.TABLE), "")  # tail number
        self.assertEqual(api.operator_tag("XYZ999", self.TABLE), "")  # not in table
        self.assertEqual(api.operator_tag("", self.TABLE), "")
        self.assertEqual(api.operator_tag("ASA", self.TABLE), "")     # no digit

    def test_reads_vendored_table_and_tags_fold(self):
        # Real integration: the actual common/js/adsb-operators.js seed table.
        cfg = dict(CFG, _config_dir=_EINK)   # ../../common/js from displays/e-ink
        table = api._operators_table(cfg)
        self.assertEqual(table.get("ASA"), "ALASKA")
        get = api._get_json
        self.addCleanup(lambda: setattr(api, "_get_json", get))
        api._get_json = lambda base, path, timeout: (True, {
            "ok": True, "now": NOW,
            "aircraft": [{"hex": "a1", "flight": "ASA415", "lat": 47.6,
                          "lon": -122.3, "seen": 3}]})
        st = api.aircraft_status(cfg)
        self.assertEqual(st["list"][0]["operator"], "ALASKA")


class TestMergeContacts(unittest.TestCase):
    def test_interleaves_by_recency_and_tags_source(self):
        stations = {"ok": True, "list": [
            {"callsign": "W4MHI", "last_heard": "2023-11-14T22:10:00+00:00"},
        ]}
        aircraft = {"ok": True, "list": [
            {"source": "adsb", "callsign": "ASA424",
             "last_heard": "2023-11-14T22:15:00+00:00"},
        ]}
        m = api.merge_contacts(stations, aircraft)
        self.assertEqual(m["count"], 2)
        self.assertEqual(m["last"]["callsign"], "ASA424")     # newer wins
        self.assertEqual(m["list"][1]["source"], "aprs")      # station tagged

    def test_ok_when_either_source_live(self):
        self.assertTrue(api.merge_contacts({"ok": False}, {"ok": True})["ok"])
        self.assertTrue(api.merge_contacts({"ok": True}, {"ok": False})["ok"])
        self.assertFalse(api.merge_contacts({"ok": False}, {"ok": False})["ok"])

    def test_empty_merge(self):
        m = api.merge_contacts({"ok": True, "list": []}, {"ok": False, "list": []})
        self.assertIsNone(m["last"])
        self.assertEqual(m["count"], 0)


if __name__ == "__main__":
    unittest.main()
