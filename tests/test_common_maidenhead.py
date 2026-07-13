from common import maidenhead


def test_latlon_to_grid_returns_expected_locator():
    assert maidenhead.latlon_to_grid(37.69, -97.34, precision=4) == "EM17"


def test_grid_to_latlon_round_trips_known_locator():
    lat, lon = maidenhead.grid_to_latlon("EM17")
    assert lat == 37.5
    assert lon == -97.0
