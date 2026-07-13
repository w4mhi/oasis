import importlib


def test_kiwix_service_module_is_importable():
    module = importlib.import_module("services.kiwix.common.kiwix")

    assert module.SERVICE_NAME == "kiwix"
    assert module.PORT == 8081
    assert callable(module.run)
