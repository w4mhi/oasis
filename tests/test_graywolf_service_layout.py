import importlib


def test_graywolf_service_module_is_importable():
    module = importlib.import_module("services.graywolf.common.graywolf")

    assert module.SERVICE == "graywolf"
    assert module.PORT == 8080
    assert callable(module.run)
