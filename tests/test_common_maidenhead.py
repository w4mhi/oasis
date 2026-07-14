import os, sys, unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import maidenhead


class MaidenheadTest(unittest.TestCase):
    def test_latlon_to_grid_returns_expected_locator(self):
        self.assertEqual(maidenhead.latlon_to_grid(37.69, -97.34, precision=4), "EM17")

    def test_grid_to_latlon_round_trips_known_locator(self):
        lat, lon = maidenhead.grid_to_latlon("EM17")
        self.assertEqual(lat, 37.5)
        self.assertEqual(lon, -97.0)


if __name__ == "__main__":
    unittest.main()
