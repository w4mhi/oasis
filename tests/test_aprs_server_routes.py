import os
import sys
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


class WarningBroadcastRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        # isolate the warnings file per test
        self._orig_wf = aprs_routes.WARNINGS_FILE
        self._tmp = os.path.join(_HERE, "_warns_test.json")
        aprs_routes.WARNINGS_FILE = self._tmp
        if os.path.exists(self._tmp):
            os.remove(self._tmp)
        # fake broadcaster records calls
        calls = {"adv": [], "unadv": []}
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
                return {"created": 0, "killed": 0}
        self.calls = calls
        self.pushed = pushed
        aprs_routes._TEST_BROADCASTER = FakeB()

    def tearDown(self):
        aprs_routes._TEST_BROADCASTER = None
        aprs_routes.WARNINGS_FILE = self._orig_wf
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
        self.assertTrue(w["aprs_name"].startswith("W"))

    def test_broadcast_advertises_and_stores_id(self):
        w = self._add(True)
        self.assertEqual(self.calls["adv"], [w["id"]])
        self.assertEqual(w["gw_beacon_id"], "gw-1")

    def test_delete_broadcast_unadvertises(self):
        w = self._add(True)
        self.client.delete("/api/aprs/warnings/" + w["id"])
        self.assertEqual(self.calls["unadv"], [w["id"]])

    def test_patch_toggle_on_advertises(self):
        w = self._add(False)
        self.client.patch("/api/aprs/warnings/" + w["id"], json={"broadcast": True})
        self.assertEqual(self.calls["adv"], [w["id"]])

    def test_patch_note_with_broadcast_true_pushes_comment(self):
        w = self._add(True)                      # already broadcasting, gw_beacon_id="gw-1"
        self.client.patch("/api/aprs/warnings/" + w["id"],
                          json={"note": "road washed out", "broadcast": True})
        self.assertIn(("gw-1", "road washed out"), self.pushed["comments"])


if __name__ == "__main__":
    unittest.main()
