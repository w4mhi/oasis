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


if __name__ == "__main__":
    unittest.main()
