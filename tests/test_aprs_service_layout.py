import importlib


def test_aprs_service_module_is_importable():
    module = importlib.import_module("services.aprs.common.aprs")

    assert module.SERVICE == "graywolf-api"
    assert module.PORT == 8085
    assert callable(module.run)
