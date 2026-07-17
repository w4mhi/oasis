import os
import sys

# Keep imports consistent with other server tests: load server/app.py directly
# by adding server/ to sys.path rather than treating server as a package.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.join(os.path.dirname(_HERE), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import app as app_module


def test_map_assets_and_ui_live_under_services_map():
    # The live map UI + shared assets moved out of services/aprs/ (the page now
    # serves both APRS and ADS-B) into the service-neutral services/map/.
    assert app_module.MAP_ASSETS.endswith("services/map/map-assets")
    assert app_module.MAP_DIR.endswith("services/map")


def test_map_ui_served_at_server_map_not_server_aprs():
    client = app_module.app.test_client()
    # New home: the map UI + its warnings catalog answer under /server/map/.
    assert client.get("/server/map/map.html").status_code == 200
    assert client.get("/server/map/warnings.json").status_code == 200
    # /map-assets/ URL is deliberately unchanged (referenced suite-wide).
    assert client.get("/map-assets/basemap-style.js").status_code == 200
    # The old /server/aprs/ static route is gone.
    assert client.get("/server/aprs/map.html").status_code == 404
