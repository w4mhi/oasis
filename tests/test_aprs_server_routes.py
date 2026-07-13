import os
import sys

# Keep imports consistent with other server tests: load server/app.py directly
# by adding server/ to sys.path rather than treating server as a package.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.join(os.path.dirname(_HERE), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import app as app_module


def test_aprs_service_assets_are_visible_to_server():
    assert app_module.MAP_ASSETS.endswith("services/aprs/static/map-assets")
    assert app_module.APRS_DIR.endswith("services/aprs/static/aprs")
