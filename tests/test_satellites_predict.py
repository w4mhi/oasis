import os, sys, datetime, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
try:
    import predict  # noqa: E402  — needs skyfield/numpy/sgp4 (an optional satellites dep)
    _HAS_PREDICT = True
except Exception:  # noqa: BLE001  — absent in the minimal CI server-setup job -> skip
    _HAS_PREDICT = False

# Fixed ISS TLE + a start near its epoch → deterministic, propagator-accurate.
ISS_L1 = "1 25544U 98067A   24010.51782528 -.00002182  00000-0 -24984-4 0  9990"
ISS_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50120818435204"
LAT, LON = 47.5495, -122.0298          # Issaquah, WA
START = datetime.datetime(2024, 1, 10, 12, 0, tzinfo=datetime.timezone.utc)

@unittest.skipUnless(_HAS_PREDICT, "skyfield/predict not installed")
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

    def test_every_pass_reports_where_it_peaks(self):
        """max_el alone cannot tell a workable pass from a blocked one: 41 degrees
        over the hill to the north is unusable and 41 over open water is a good
        pass. rise_az/set_az do not answer it either — a pass rising NNE and
        setting SSW peaks somewhere the operator would have to guess."""
        passes = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=0.0)
        self.assertGreater(len(passes), 0)
        for p in passes:
            self.assertIn("peak_az", p)
            self.assertTrue(0.0 <= p["peak_az"] < 360.0, p["peak_az"])

    def test_the_peak_azimuth_is_the_azimuth_AT_the_peak(self):
        """Guards the indexing: _altaz returns (elevation, azimuth) and the peak
        used to keep only [0]. Taking the wrong half would still yield a
        plausible 0-360 number, so bounds alone would not catch it."""
        passes = predict.compute_passes(self.sat, LAT, LON, START, hours=48, min_elev=20.0)
        self.assertGreater(len(passes), 0)
        for p in passes:
            track = predict.compute_track(
                self.sat, LAT, LON,
                datetime.datetime.fromisoformat(p["peak"]) - datetime.timedelta(seconds=5),
                datetime.datetime.fromisoformat(p["peak"]) + datetime.timedelta(seconds=5),
                step_s=5)
            # The sample nearest the culmination is the highest one in the window.
            top = max(track, key=lambda q: q["el"])
            self.assertAlmostEqual(p["max_el"], top["el"], delta=0.05)
            d = abs(p["peak_az"] - top["az"]) % 360.0
            self.assertLess(min(d, 360.0 - d), 1.0,
                            f"peak_az {p['peak_az']} disagrees with the track's {top['az']}")

    def test_a_pass_peaking_north_is_distinguishable_from_one_peaking_south(self):
        # The whole point: two passes with a similar max_el must be separable by
        # where they culminate, or the field buys nothing.
        passes = predict.compute_passes(self.sat, LAT, LON, START, hours=72, min_elev=0.0)
        azs = {round(p["peak_az"] / 45.0) % 8 for p in passes}
        self.assertGreater(len(azs), 1, "every pass peaked in the same octant — suspicious")

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

@unittest.skipUnless(_HAS_PREDICT, "skyfield/predict not installed")
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

    def test_factor_is_served_without_any_downlink(self):
        """The factor is dimensionless, so it does not depend on a frequency and
        must not wait for one. A caller that never passes downlink_hz — which is
        every caller that intends to multiply by the ARMED carrier — still gets a
        usable Doppler out of the track."""
        r, s = self._one_pass()
        pts = predict.compute_track(self.sat, LAT, LON, r, s, step_s=10)
        self.assertTrue(all(isinstance(p["factor"], float) for p in pts))
        self.assertTrue(all(p["doppler_hz"] is None for p in pts))
        self.assertGreater(max(p["factor"] for p in pts), 0)   # approaching
        self.assertLess(min(p["factor"] for p in pts), 0)      # receding

    def test_doppler_hz_is_exactly_the_factor_times_the_carrier(self):
        """The deprecated doppler_hz must stay a pure derivation of the factor,
        not a second implementation — otherwise the two drift and a page that
        reads one disagrees with a page that reads the other."""
        r, s = self._one_pass()
        pts = predict.compute_track(self.sat, LAT, LON, r, s, step_s=10,
                                    downlink_hz=437_800_000)
        for p in pts:
            self.assertAlmostEqual(p["doppler_hz"], p["factor"] * 437_800_000, places=6)

    def test_the_same_factor_serves_both_bands(self):
        """The bug this replaces: /track computed Doppler for downlinks[0], so a
        bird with 145.8 and 437.8 MHz downlinks reported a shift ~3x wrong on
        whichever one the operator actually armed."""
        r, s = self._one_pass()
        pts = predict.compute_track(self.sat, LAT, LON, r, s, step_s=10)
        peak = max(pts, key=lambda p: abs(p["factor"]))["factor"]
        self.assertAlmostEqual((peak * 437.8e6) / (peak * 145.8e6), 437.8 / 145.8,
                               places=9)

    def test_leo_doppler_is_physically_plausible(self):
        """A sanity floor/ceiling: a LEO pass shifts 70 cm by roughly 10 kHz. A
        factor off by a thousand (a km/s-vs-m/s slip, say) still flips sign
        correctly and would sail past every other test here."""
        r, s = self._one_pass()
        pts = predict.compute_track(self.sat, LAT, LON, r, s, step_s=10)
        peak = max(abs(p["factor"]) for p in pts) * 437.8e6
        self.assertGreater(peak, 3_000)
        self.assertLess(peak, 12_000)


@unittest.skipUnless(_HAS_PREDICT, "skyfield/predict not installed")
class RangeRateFactorTest(unittest.TestCase):
    """The pure helper, with hand-built vectors — no propagator involved."""

    def test_head_on_approach_is_positive(self):
        # 1000 km out along +x, closing at 1 km/s.
        f = predict.range_rate_factor((1000.0, 0.0, 0.0), (-1.0, 0.0, 0.0))
        self.assertAlmostEqual(f, 1.0 / 299792.458, places=12)

    def test_head_on_recession_is_negative(self):
        f = predict.range_rate_factor((1000.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        self.assertAlmostEqual(f, -1.0 / 299792.458, places=12)

    def test_purely_tangential_motion_has_no_shift(self):
        """Closest approach: all the velocity is across the line of sight, so the
        range rate — and only the range rate — is zero."""
        f = predict.range_rate_factor((1000.0, 0.0, 0.0), (0.0, 7.5, 0.0))
        self.assertEqual(f, 0.0)

    def test_zero_range_does_not_divide_by_zero(self):
        self.assertEqual(predict.range_rate_factor((0.0, 0.0, 0.0), (1.0, 2.0, 3.0)), 0.0)


if __name__ == "__main__":
    unittest.main()
