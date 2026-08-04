import os
import sys
import tempfile
import time
import unittest

# Load server/app.py and the maps package by putting the repo root and server/
# on sys.path (rather than treating them as installed packages).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SERVER = os.path.join(_ROOT, "server")
for _p in (_ROOT, _SERVER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as app_module
from maps.traffic import routes as map_routes


class MapSubsystemLayoutTest(unittest.TestCase):
    """The map subsystem is consolidated under maps/: the traffic app
    (maps/traffic), the render engine (maps/mapengine) and the tiles
    (maps/tiles). The blueprint now only carries the /api/fs/* PMTiles browser."""

    def test_blueprint_and_maps_dir(self):
        self.assertTrue(map_routes.MAPS_DIR.endswith("maps"))
        self.assertEqual(map_routes.bp.name, "map")

    def test_ui_and_assets_served_under_maps(self):
        client = app_module.app.test_client()
        # Traffic app + warnings catalog: static files under maps/traffic/.
        self.assertEqual(client.get("/maps/traffic/map.html").status_code, 200)
        self.assertEqual(client.get("/maps/traffic/warnings.json").status_code, 200)
        # Render engine: consolidated under maps/mapengine/.
        self.assertEqual(client.get("/maps/mapengine/basemap-style.js").status_code, 200)
        # APRS sprite sheets moved with the app to maps/traffic/assets/.
        self.assertEqual(client.get("/maps/traffic/assets/aprs-symbols-24-0.png").status_code, 200)

    def test_old_static_routes_are_gone(self):
        client = app_module.app.test_client()
        self.assertEqual(client.get("/server/map/map.html").status_code, 404)
        self.assertEqual(client.get("/server/aprs/map.html").status_code, 404)


import json as _json

from services.aprs import routes as aprs_routes


def _wait_reconcile_idle(timeout=2.0):
    """Block until any in-flight background reconcile thread (kicked by a
    previous test) has fully finished.

    `_load_warnings`/`_save_warnings` read the module global `WARNINGS_FILE`
    at call time, not a value captured when the thread was spawned. Merely
    resetting `_reconcile_active[0]` to False does NOT stop a thread that is
    still mid-flight — it just lies about its state, so that thread can go on
    to write into whatever `WARNINGS_FILE` a *later* test has since pointed
    the global at. Callers must wait for real quiescence before repointing
    `WARNINGS_FILE` (setUp) and before restoring/deleting it (tearDown).
    """
    deadline = time.time() + timeout
    while aprs_routes._reconcile_active[0] and time.time() < deadline:
        time.sleep(0.005)


class WarningBroadcastRouteTest(unittest.TestCase):
    """NOTE (intent model / Task R2): handlers no longer call GrayWolf
    inline — advertise/unadvertise only ever happen inside the background
    reconcile driver. These tests were updated from asserting synchronous
    advertise/unadvertise to asserting the recorded intent (broadcast flag,
    tombstoning) and that a reconcile gets kicked; the actual
    advertise/unadvertise wiring is exercised by WarningBroadcaster tests
    (R1) and by the reconcile-driven write-back covered elsewhere."""

    def setUp(self):
        self.client = app_module.app.test_client()
        # isolate the warnings file per test: a unique temp filename so a
        # stray daemon reconcile thread from a prior test can never write
        # into this test's file.
        self._orig_wf = aprs_routes.WARNINGS_FILE
        fd, self._tmp = tempfile.mkstemp(prefix="warns_", suffix=".json", dir=_HERE)
        os.close(fd)
        os.remove(self._tmp)
        # wait for any leftover reconcile thread from a prior test before
        # repointing the module global at this test's file.
        _wait_reconcile_idle()
        aprs_routes.WARNINGS_FILE = self._tmp
        aprs_routes._reconcile_active[0] = False
        aprs_routes._reconcile_dirty[0] = False
        aprs_routes._last_reconcile[0] = 0.0
        # fake broadcaster records calls
        calls = {"adv": [], "unadv": [], "reconcile": 0}
        pushed = {"comments": []}

        class FakeC:
            def update_beacon(self, bid, payload):
                pushed["comments"].append((bid, payload.get("comment")))

        class FakeB:
            def __init__(self):
                self.c = FakeC()

            def advertise(self, w):
                calls["adv"].append(w["id"])
                return "gw-1"

            def unadvertise(self, w):
                calls["unadv"].append(w["id"])

            def reconcile(self, ws):
                calls["reconcile"] += 1
                return {"created": 0, "killed": 0, "removed": []}
        self.calls = calls
        self.pushed = pushed
        aprs_routes._TEST_BROADCASTER = FakeB()

    def tearDown(self):
        # wait for any reconcile thread this test kicked to finish before
        # repointing/deleting WARNINGS_FILE out from under it.
        _wait_reconcile_idle()
        aprs_routes._TEST_BROADCASTER = None
        aprs_routes.WARNINGS_FILE = self._orig_wf
        aprs_routes._reconcile_active[0] = False
        aprs_routes._reconcile_dirty[0] = False
        aprs_routes._last_reconcile[0] = 0.0
        if os.path.exists(self._tmp):
            os.remove(self._tmp)

    def _add(self, broadcast):
        r = self.client.post("/api/aprs/warnings", json={
            "type": "flood", "lon": -122.0, "lat": 47.5, "broadcast": broadcast})
        return _json.loads(r.data)["warning"]

    def test_local_only_does_not_advertise(self):
        w = self._add(False)
        self.assertFalse(w["broadcast"])
        self.assertIsNone(w["gw_beacon_id"])
        self.assertEqual(self.calls["adv"], [])
        # Tactical name: catalog abbr for "flood" is "FLOOD", first slot "01".
        self.assertEqual(w["aprs_name"], "FLOOD01")

    def test_second_warning_of_same_type_gets_next_tactical_slot(self):
        w1 = self._add(False)
        w2 = self._add(False)
        self.assertEqual(w1["aprs_name"], "FLOOD01")
        self.assertEqual(w2["aprs_name"], "FLOOD02")

    def test_blank_note_defaults_to_tactical_name_and_station_callsign(self):
        # Pin a station callsign so the default note is deterministic
        # regardless of this machine's configuration/station.json.
        orig = aprs_routes.warning_catalog.station_callsign
        aprs_routes.warning_catalog.station_callsign = lambda root: "W4MHI"
        try:
            w = self._add(False)
        finally:
            aprs_routes.warning_catalog.station_callsign = orig
        self.assertEqual(w["note"], f"{w['aprs_name']} inserted by W4MHI-1")

    def test_explicit_note_is_kept_verbatim(self):
        r = self.client.post("/api/aprs/warnings", json={
            "type": "flood", "lon": -122.0, "lat": 47.5, "broadcast": False,
            "note": "custom note text"})
        w = _json.loads(r.data)["warning"]
        self.assertEqual(w["note"], "custom note text")

    def test_broadcast_records_intent_without_inline_advertise(self):
        w = self._add(True)
        # No inline GrayWolf call: the handler only records intent, the
        # background reconciler is the one that would call advertise().
        self.assertEqual(self.calls["adv"], [])
        self.assertTrue(w["broadcast"])
        self.assertIsNone(w["gw_beacon_id"])

    def test_delete_broadcast_tombstones_without_inline_unadvertise(self):
        import time as _t
        w = self._add(True)
        self.client.delete("/api/aprs/warnings/" + w["id"])
        # No inline GrayWolf call on the request path.
        self.assertEqual(self.calls["unadv"], [])
        _t.sleep(0.2)
        after = _json.loads(open(self._tmp).read())
        self.assertEqual(len(after), 1)
        self.assertTrue(after[0].get("pending_delete"))

    def test_patch_toggle_on_records_intent_without_inline_advertise(self):
        w = self._add(False)
        r = self.client.patch("/api/aprs/warnings/" + w["id"], json={"broadcast": True})
        self.assertEqual(self.calls["adv"], [])
        updated = _json.loads(r.data)["warning"]
        self.assertTrue(updated["broadcast"])

    def test_patch_send_path_sets_destination_from_card(self):
        # The object card picks the destination: local -> RF broadcast.
        w = self._add(False)
        r = self.client.patch("/api/aprs/warnings/" + w["id"],
                              json={"broadcast": True, "send_path": "rf"})
        updated = _json.loads(r.data)["warning"]
        self.assertTrue(updated["broadcast"])
        self.assertEqual(updated["send_path"], "rf")

    def test_patch_send_path_only_change_persists(self):
        # Changing just the destination (IS -> RF) on an already-broadcast alert
        # is recorded; the reconciler re-advertises on the new path.
        r = self.client.post("/api/aprs/warnings", json={
            "type": "flood", "lon": -122.0, "lat": 47.5,
            "broadcast": True, "send_path": "is_only"})
        w = _json.loads(r.data)["warning"]
        self.assertEqual(w["send_path"], "is_only")
        self.client.patch("/api/aprs/warnings/" + w["id"], json={"send_path": "rf"})
        after = _json.loads(open(self._tmp).read())
        self.assertEqual(after[0]["send_path"], "rf")

    def test_patch_note_on_air_pushes_comment_from_background_thread(self):
        import time as _t
        # Construct the on-air warning directly on disk (bypassing POST) so
        # no competing reconcile kick is in flight to race the write-back
        # against this fixture's gw_beacon_id.
        item = {"id": "w1", "type": "flood", "lon": -122.0, "lat": 47.5,
                "note": "", "ts": 0, "broadcast": True, "gw_beacon_id": "gw-1",
                "aprs_name": "Ww1"}
        with open(self._tmp, "w") as fh:
            _json.dump([item], fh)
        self.client.patch("/api/aprs/warnings/" + item["id"],
                          json={"note": "road washed out", "broadcast": True})
        _t.sleep(0.2)
        self.assertIn(("gw-1", "road washed out"), self.pushed["comments"])


class ReconcileTriggerTest(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self._orig_wf = aprs_routes.WARNINGS_FILE
        fd, self._tmp = tempfile.mkstemp(prefix="warns_", suffix=".json", dir=_HERE)
        os.close(fd)
        # wait for any leftover reconcile thread from a prior test before
        # repointing the module global at this test's file.
        _wait_reconcile_idle()
        aprs_routes.WARNINGS_FILE = self._tmp
        aprs_routes._reconcile_active[0] = False
        aprs_routes._reconcile_dirty[0] = False
        aprs_routes._last_reconcile[0] = 0.0
        with open(self._tmp, "w") as fh:
            _json.dump([{"id": "a1", "type": "flood", "lat": 1, "lon": 2,
                         "note": "", "ts": 0, "broadcast": True,
                         "gw_beacon_id": "x", "aprs_name": "Wa1"}], fh)
        self.hits = []
        class FakeB:
            def reconcile(_s, ws): self.hits.append(len(ws)); return {}
        aprs_routes._TEST_BROADCASTER = FakeB()

    def tearDown(self):
        # wait for any reconcile thread this test kicked to finish before
        # repointing/deleting WARNINGS_FILE out from under it.
        _wait_reconcile_idle()
        aprs_routes._TEST_BROADCASTER = None
        aprs_routes.WARNINGS_FILE = self._orig_wf
        aprs_routes._reconcile_active[0] = False
        aprs_routes._reconcile_dirty[0] = False
        aprs_routes._last_reconcile[0] = 0.0
        if os.path.exists(self._tmp): os.remove(self._tmp)

    def test_get_triggers_reconcile_once_then_throttles(self):
        import time as _t
        self.client.get("/api/aprs/warnings")
        self.client.get("/api/aprs/warnings")   # throttled
        _t.sleep(0.2)                            # let the daemon thread run
        self.assertEqual(self.hits, [1])         # exactly one reconcile


class ReconcileTriggerNoBroadcastTest(unittest.TestCase):
    """Fix A: reconcile must run even with zero broadcast warnings on disk —
    otherwise an orphan object beacon (failed delete) re-beacons forever."""

    def setUp(self):
        self.client = app_module.app.test_client()
        self._orig_wf = aprs_routes.WARNINGS_FILE
        fd, self._tmp = tempfile.mkstemp(prefix="warns_", suffix=".json", dir=_HERE)
        os.close(fd)
        # wait for any leftover reconcile thread from a prior test before
        # repointing the module global at this test's file.
        _wait_reconcile_idle()
        aprs_routes.WARNINGS_FILE = self._tmp
        aprs_routes._reconcile_active[0] = False
        aprs_routes._reconcile_dirty[0] = False
        aprs_routes._last_reconcile[0] = 0.0
        with open(self._tmp, "w") as fh:
            _json.dump([], fh)   # zero broadcast (zero) warnings on disk
        self.hits = []

        class FakeB:
            def reconcile(_s, ws):
                self.hits.append(len(ws))
                return {}
        aprs_routes._TEST_BROADCASTER = FakeB()

    def tearDown(self):
        # wait for any reconcile thread this test kicked to finish before
        # repointing/deleting WARNINGS_FILE out from under it.
        _wait_reconcile_idle()
        aprs_routes._TEST_BROADCASTER = None
        aprs_routes.WARNINGS_FILE = self._orig_wf
        aprs_routes._reconcile_active[0] = False
        aprs_routes._reconcile_dirty[0] = False
        aprs_routes._last_reconcile[0] = 0.0
        if os.path.exists(self._tmp):
            os.remove(self._tmp)

    def test_get_triggers_reconcile_with_zero_broadcast_warnings(self):
        import time as _t
        self.client.get("/api/aprs/warnings")
        _t.sleep(0.2)                            # let the daemon thread run
        self.assertEqual(self.hits, [0])         # reconcile still ran, with 0 warnings


class IntentModelRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self._orig_wf = aprs_routes.WARNINGS_FILE
        fd, self._tmp = tempfile.mkstemp(prefix="warns_", suffix=".json", dir=_HERE)
        os.close(fd)
        os.remove(self._tmp)
        # wait for any leftover reconcile thread from a prior test before
        # repointing the module global at this test's file.
        _wait_reconcile_idle()
        aprs_routes.WARNINGS_FILE = self._tmp
        aprs_routes._reconcile_active[0] = False
        aprs_routes._reconcile_dirty[0] = False
        aprs_routes._last_reconcile[0] = 0.0
        calls = {"reconcile": 0}
        class FakeB:
            def reconcile(_s, ws): calls["reconcile"] += 1; return {"created":0,"killed":0,"removed":[]}
        self.calls = calls
        aprs_routes._TEST_BROADCASTER = FakeB()

    def tearDown(self):
        # wait for any reconcile thread this test kicked to finish before
        # repointing/deleting WARNINGS_FILE out from under it.
        _wait_reconcile_idle()
        aprs_routes._TEST_BROADCASTER = None
        aprs_routes.WARNINGS_FILE = self._orig_wf
        aprs_routes._reconcile_active[0] = False
        aprs_routes._reconcile_dirty[0] = False
        aprs_routes._last_reconcile[0] = 0.0
        if os.path.exists(self._tmp): os.remove(self._tmp)

    def _add(self, broadcast):
        import json as _j
        r = self.client.post("/api/aprs/warnings", json={"type":"flood","lon":-122.0,"lat":47.5,"broadcast":broadcast})
        return _j.loads(r.data)["warning"]

    def test_post_broadcast_is_pending_not_advertised_inline(self):
        w = self._add(True)
        self.assertTrue(w["broadcast"]); self.assertIsNone(w["gw_beacon_id"])   # no inline advertise

    def test_delete_broadcast_tombstones_not_removed(self):
        import time as _t, json as _j
        w = self._add(True)
        # The POST above kicks a background reconcile thread (a broadcaster
        # is configured). Wait for it to go fully idle before hand-editing
        # the file directly below: that raw write bypasses _warnings_lock,
        # so racing it against the reconcile thread's own lock-protected
        # read/write-back can catch the file mid-write and truncate it.
        for _ in range(200):
            if not aprs_routes._reconcile_active[0]:
                break
            _t.sleep(0.01)
        # simulate it went on air
        data = _j.load(open(self._tmp)); data[0]["gw_beacon_id"] = "gw-1"; _j.dump(data, open(self._tmp,"w"))
        self.client.delete("/api/aprs/warnings/" + w["id"])
        _t.sleep(0.2)
        after = _j.load(open(self._tmp))
        self.assertEqual(len(after), 1)                 # still on disk (tombstone)
        self.assertTrue(after[0].get("pending_delete"))

    def test_delete_local_only_removed_immediately(self):
        import json as _j
        w = self._add(False)
        self.client.delete("/api/aprs/warnings/" + w["id"])
        self.assertEqual(_j.load(open(self._tmp)), [])   # gone at once, no tombstone

    def test_mutations_kick_reconcile(self):
        import time as _t
        self._add(True); _t.sleep(0.2)
        self.assertGreaterEqual(self.calls["reconcile"], 1)

    def test_warnings_response_reports_broadcast_available(self):
        # _TEST_BROADCASTER is set in setUp -> available True
        r = self.client.get("/api/aprs/warnings")
        self.assertTrue(_json.loads(r.data)["broadcast_available"])

    def test_delete_broadcast_removes_immediately_when_no_broadcaster(self):
        aprs_routes._TEST_BROADCASTER = None            # simulate no GrayWolf configured
        w = self._add(True)                              # broadcast intent, gw_beacon_id None
        self.client.delete("/api/aprs/warnings/" + w["id"])
        self.assertEqual(_json.load(open(self._tmp)), [])   # removed, NOT tombstoned

    def test_post_stores_send_path(self):
        r = self.client.post("/api/aprs/warnings", json={"type":"flood","lon":-122.0,"lat":47.5,
                                                         "broadcast":True,"send_path":"is_only"})
        self.assertEqual(_json.loads(r.data)["warning"]["send_path"], "is_only")

    def test_post_invalid_send_path_falls_back(self):
        r = self.client.post("/api/aprs/warnings", json={"type":"flood","lon":-122.0,"lat":47.5,
                                                         "broadcast":True,"send_path":"garbage"})
        self.assertIn(_json.loads(r.data)["warning"]["send_path"], ("both","is_only","rf"))


if __name__ == "__main__":
    unittest.main()
