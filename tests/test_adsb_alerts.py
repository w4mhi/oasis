import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from services.adsb.common import alerts

class AlertsTest(unittest.TestCase):
    def test_emergency_squawk(self):
        out = alerts.evaluate({"hex": "abc", "squawk": "7700"}, None, 50)
        self.assertTrue(any(a["kind"] == "squawk" for a in out))

    def test_normal_squawk_no_alert(self):
        out = alerts.evaluate({"hex": "abc", "squawk": "1200"}, None, 50)
        self.assertEqual(out, [])

    def test_proximity_within_radius(self):
        station = {"lat": 47.60, "lon": -122.33}
        ac = {"hex": "abc", "lat": 47.61, "lon": -122.34, "squawk": "1200"}
        out = alerts.evaluate(ac, station, radius_km=50)
        self.assertTrue(any(a["kind"] == "proximity" for a in out))

    def test_proximity_outside_radius(self):
        station = {"lat": 47.60, "lon": -122.33}
        ac = {"hex": "abc", "lat": 40.0, "lon": -100.0, "squawk": "1200"}
        out = alerts.evaluate(ac, station, radius_km=50)
        self.assertFalse(any(a["kind"] == "proximity" for a in out))

if __name__ == "__main__":
    unittest.main()
