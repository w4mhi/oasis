import importlib


def test_winlink_service_module_is_importable():
    module = importlib.import_module("services.winlink.common.winlink")

    assert module.SERVICE == "pat"
    assert module.DEFAULT_PORT == 8082
    assert callable(module.run)
