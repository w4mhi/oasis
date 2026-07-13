import importlib


def test_aprs_service_assets_are_visible_to_server():
    app_module = importlib.import_module("server.app")

    assert app_module.MAP_ASSETS.endswith("services/aprs/static/map-assets")
    assert app_module.APRS_DIR.endswith("services/aprs/static/aprs")
