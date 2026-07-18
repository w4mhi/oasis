import os, sys, datetime, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
import predict  # noqa: E402

# Fixed ISS TLE + a start near its epoch → deterministic, propagator-accurate.
ISS_L1 = "1 25544U 98067A   24010.51782528 -.00002182  00000-0 -24984-4 0  9990"
ISS_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50120818435204"
LAT, LON = 47.5495, -122.0298          # Issaquah, WA
START = datetime.datetime(2024, 1, 10, 12, 0, tzinfo=datetime.timezone.utc)

class PassesTest(unittest.TestCase):
    def setUp(self):
        self.sat = predict.make_satellite("ISS (ZARYA)", ISS_L1, ISS_L2)

    def test_passes_are_ordered_and_bounded(self):
        passes = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=0.0)
        self.assertGreater(len(passes), 0)
        for p in passes:
            self.assertLess(p["rise"], p["peak"])
            self.assertLess(p["peak"], p["set"])
            self.assertGreaterEqual(p["max_el"], 0.0)
            self.assertLessEqual(p["max_el"], 90.0)
            self.assertTrue(0.0 <= p["rise_az"] < 360.0)
            self.assertGreater(p["duration_s"], 0.0)

    def test_min_elev_filter_reduces_count(self):
        all_p = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=0.0)
        hi_p = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=10.0)
        self.assertLessEqual(len(hi_p), len(all_p))
        for p in hi_p:
            self.assertGreaterEqual(p["max_el"], 10.0)

if __name__ == "__main__":
    unittest.main()
