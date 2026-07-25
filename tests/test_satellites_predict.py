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

    def test_pass_in_progress_at_start_is_included(self):
        # A pass already underway at start_dt has its rise event in the past. The
        # lookback must still return it whole (rise < start <= set) — otherwise the
        # map footprint / countdown key off the *next* pass while the sat is overhead.
        p0 = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=10.0)[0]
        rise = datetime.datetime.fromisoformat(p0["rise"])
        sett = datetime.datetime.fromisoformat(p0["set"])
        mid = rise + (sett - rise) / 2          # a moment inside the pass
        passes = predict.compute_passes(self.sat, LAT, LON, mid, hours=72, min_elev=10.0)
        first = passes[0]
        self.assertLess(datetime.datetime.fromisoformat(first["rise"]), mid)
        self.assertGreater(datetime.datetime.fromisoformat(first["set"]), mid)
        # It's the same pass as the full-window computation (root-finding differs
        # only at the sub-millisecond level with a different search-window start).
        def _close(a, b):
            d = abs((datetime.datetime.fromisoformat(a) - datetime.datetime.fromisoformat(b)).total_seconds())
            self.assertLess(d, 1.0)
        _close(first["rise"], p0["rise"])
        _close(first["set"], p0["set"])

    def test_in_progress_pass_recovered_when_rise_predates_lookback(self):
        # A long HEO/Molniya dwell can be HOURS into a pass, so its rise predates
        # the normal lookback window and the builder drops it — the roster then
        # shows the NEXT rise though the bird is overhead. The wide backsearch must
        # recover it. Simulate the "rise outside the lookback" condition with
        # lookback_min=0 so only the backsearch (not the normal window) can find it.
        p0 = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=10.0)[0]
        rise = datetime.datetime.fromisoformat(p0["rise"])
        sett = datetime.datetime.fromisoformat(p0["set"])
        mid = rise + (sett - rise) / 2
        passes = predict.compute_passes(self.sat, LAT, LON, mid, hours=72,
                                        min_elev=10.0, lookback_min=0)
        first = passes[0]
        self.assertLessEqual(datetime.datetime.fromisoformat(first["rise"]), mid)
        self.assertGreater(datetime.datetime.fromisoformat(first["set"]), mid)

    def test_pass_ended_before_start_is_excluded(self):
        # Backward-compat: the lookback window must not resurrect a pass that already
        # set before start_dt — every returned pass ends at or after start_dt.
        p0 = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=10.0)[0]
        after = datetime.datetime.fromisoformat(p0["set"]) + datetime.timedelta(minutes=1)
        passes = predict.compute_passes(self.sat, LAT, LON, after, hours=72, min_elev=10.0)
        for p in passes:
            self.assertGreater(datetime.datetime.fromisoformat(p["set"]), after)

class TrackTest(unittest.TestCase):
    def setUp(self):
        self.sat = predict.make_satellite("ISS (ZARYA)", ISS_L1, ISS_L2)

    def _one_pass(self):
        p = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=10.0)[0]
        return (datetime.datetime.fromisoformat(p["rise"]),
                datetime.datetime.fromisoformat(p["set"]))

    def test_track_points_are_bounded(self):
        r, s = self._one_pass()
        pts = predict.compute_track(self.sat, LAT, LON, r, s, step_s=10)
        self.assertGreater(len(pts), 2)
        for pt in pts:
            self.assertTrue(-90.0 <= pt["lat"] <= 90.0)
            self.assertTrue(-180.0 <= pt["lon"] <= 180.0)
            self.assertGreaterEqual(pt["el"], -1.0)   # ~horizon at ends

    def test_doppler_sign_flips_across_pass(self):
        r, s = self._one_pass()
        pts = predict.compute_track(self.sat, LAT, LON, r, s, step_s=10,
                                    downlink_hz=145_800_000)
        dopps = [pt["doppler_hz"] for pt in pts]
        self.assertGreater(max(dopps), 0)    # approaching → positive shift
        self.assertLess(min(dopps), 0)       # receding → negative shift
        self.assertGreater(dopps[0], dopps[-1])

if __name__ == "__main__":
    unittest.main()
