import json, os, sys, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

class RoutesTest(unittest.TestCase):
    def setUp(self):
        # Dotted import (not bare `import routes`) — the repo already has a
        # top-level `routes` package (server/routes/), so a bare import here
        # would silently resolve to whichever one another test imported first
        # under unittest discover. Matches the existing services.map pattern
        # in tests/test_aprs_server_routes.py.
        from services.satellites import routes  # services/satellites/routes.py
        self.routes = routes
        # routes.py imports `predict` (skyfield/numpy) lazily inside each
        # function body rather than at module top, so the server boots even
        # if skyfield/numpy is missing. `services/satellites` was put on
        # sys.path as a side effect of importing `routes` above, so this
        # bare import resolves to the exact same cached module object that
        # routes.py's local `import predict` statements will pick up.
        import predict
        self.predict = predict
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()

        # Redirect config + cache to a temp dir seeded from the fixture.
        self._tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self._tmp, "configuration", "tle-cache"))
        fx = os.path.join(_HERE, "fixtures", "tle-sample.txt")
        open(os.path.join(self._tmp, "configuration", "tle-cache", "stations.txt"), "w").write(open(fx).read())
        json.dump({"lat": 47.5495, "lon": -122.0298},
                  open(os.path.join(self._tmp, "configuration", "station.json"), "w"))
        import shutil
        shutil.copy(os.path.join(_HERE, "fixtures", "satellites-sample.json"),
                    os.path.join(self._tmp, "configuration", "satellites.json"))
        self.routes.SUITE_ROOT = self._tmp

    def test_api_satellites_shape(self):
        r = self.client.get("/api/satellites")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("satellites", data)
        self.assertIn("tle_age_days", data)
        self.assertIn("station", data)
        # Each roster entry carries its TLE lines so the client can propagate
        # live look-angles for the workability pill (l1/l2 present, None if the
        # satellite is absent from the cache). ISS is in the fixture.
        for s in data["satellites"]:
            self.assertIn("l1", s)
            self.assertIn("l2", s)
        iss = [s for s in data["satellites"] if s["norad"] == 25544][0]
        self.assertTrue(iss["l1"].startswith("1 25544"))
        self.assertTrue(iss["l2"].startswith("2 25544"))
        # Each downlink is tagged with v1 support + blurb for the roster buttons.
        for dl in iss["downlinks"]:
            self.assertIn("supported", dl)
            self.assertIn("blurb", dl)
        # ISS has FM voice + APRS — both supported in v1.
        self.assertTrue(all(dl["supported"] for dl in iss["downlinks"]))

    def test_passes_endpoint(self):
        r = self.client.get("/api/satellites/passes?window=24")
        self.assertEqual(r.status_code, 200)
        self.assertIn("passes", r.get_json())

    def test_select_toggles(self):
        r = self.client.post("/api/satellites/select", json={"norad": 25544, "selected": False})
        self.assertEqual(r.status_code, 200)
        iss = [s for s in r.get_json()["satellites"] if s["norad"] == 25544][0]
        self.assertFalse(iss["selected"])

    def test_listen_status_shape(self):
        r = self.client.get("/api/satellites/listen/status")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        for k in ("recording", "missing_deps", "dongle_present", "busy", "holder"):
            self.assertIn(k, d)

    def test_listen_degrades_without_hardware(self):
        # No rtl_fm/sox/dongle in CI -> a clean 4xx with an error, never a 500.
        r = self.client.post("/api/satellites/listen", json={"norad": 25544})
        self.assertIn(r.status_code, (400, 409))
        self.assertIn("error", r.get_json())

    def test_listen_rejects_freq_not_a_downlink(self):
        # An override that matches no downlink is a 400 before any hardware check
        # would matter (the precondition order: deps first). On a dev box with no
        # rtl_fm the deps check fires first — accept either as a clean 4xx.
        r = self.client.post("/api/satellites/listen",
                             json={"norad": 25544, "freq_mhz": 999.0})
        self.assertIn(r.status_code, (400, 409))
        self.assertIn("error", r.get_json())

    def test_passes_cache_reused(self):
        calls = []
        orig = self.predict.compute_passes
        def counting(*a, **k):
            calls.append(1)
            return orig(*a, **k)
        self.predict.compute_passes = counting
        try:
            self.client.get("/api/satellites/passes?window=24")
            n1 = len(calls)
            self.assertGreater(n1, 0)                 # first request computed
            self.client.get("/api/satellites/passes?window=24")
            self.assertEqual(len(calls), n1)          # identical 2nd request hit the cache
        finally:
            self.predict.compute_passes = orig

    def test_passes_over_budget_returns_200_and_resumes(self):
        # A roster too large to finish in one budget window must NOT 500 (the old
        # behaviour: worker timeout → cache never written → stuck at 500 forever).
        # It returns 200 with partial data and persists progress; a later request
        # resumes and completes.
        orig_budget = self.routes._PASSES_BUDGET_S
        try:
            self.routes._PASSES_BUDGET_S = -1        # force "out of budget" at once
            r = self.client.get("/api/satellites/passes?window=24")
            self.assertEqual(r.status_code, 200)     # partial, never 500
            self.assertIn("passes", r.get_json())
            self.routes._PASSES_BUDGET_S = orig_budget   # normal budget → resume
            r2 = self.client.get("/api/satellites/passes?window=24")
            self.assertEqual(r2.status_code, 200)
            self.assertIn("25544", r2.get_json()["passes"])   # ISS computed on resume
        finally:
            self.routes._PASSES_BUDGET_S = orig_budget

class CachePlanTest(unittest.TestCase):
    """_cache_plan(cached, now_ts, ttl) -> (action, base_ts). Pure decision for
    how to use an on-disk passes cache. Regression guard for the frozen-cache bug:
    passes are ABSOLUTE times computed at `computed_at`; once the entry is older
    than the TTL its whole window has elapsed, so it must be RECOMPUTED from now —
    never served (the old code reused a stale-but-complete cache forever, so every
    pass eventually slid into the past and the roster went dark)."""

    def setUp(self):
        from services.satellites import routes
        self.routes = routes
        self.TTL = routes._CACHE_TTL_S

    def test_missing_cache_is_fresh_compute(self):
        self.assertEqual(self.routes._cache_plan({}, 1000.0, self.TTL),
                         ("fresh", 1000.0))

    def test_fresh_complete_is_served(self):
        c = {"computed_at": 1000.0, "complete": True}
        self.assertEqual(self.routes._cache_plan(c, 1000.0 + self.TTL - 1, self.TTL),
                         ("serve", 1000.0))

    def test_stale_complete_is_recomputed_not_served(self):
        # THE BUG: a complete cache older than the TTL describes an elapsed window.
        c = {"computed_at": 1000.0, "complete": True}
        now = 1000.0 + self.TTL + 1
        self.assertEqual(self.routes._cache_plan(c, now, self.TTL), ("fresh", now))

    def test_fresh_incomplete_resumes_from_original_start(self):
        c = {"computed_at": 1000.0, "complete": False}
        self.assertEqual(self.routes._cache_plan(c, 1000.0 + 5, self.TTL),
                         ("resume", 1000.0))

    def test_stale_incomplete_is_recomputed(self):
        c = {"computed_at": 1000.0, "complete": False}
        now = 1000.0 + self.TTL + 1
        self.assertEqual(self.routes._cache_plan(c, now, self.TTL), ("fresh", now))

    def test_legacy_cache_without_computed_at_is_recomputed(self):
        # A pre-fix cache file (no computed_at) can't be trusted to be current.
        c = {"passes": {"25544": []}}
        self.assertEqual(self.routes._cache_plan(c, 1000.0, self.TTL),
                         ("fresh", 1000.0))


class PassesStalenessTest(RoutesTest):
    """End-to-end guard: a stale complete cache on disk must trigger a real
    recompute (compute_passes called again) rather than serving elapsed passes."""

    def test_stale_cache_triggers_recompute(self):
        # Prime the cache with a normal request, then backdate its computed_at
        # past the TTL and confirm the next request recomputes instead of serving.
        self.client.get("/api/satellites/passes?window=24")
        # Locate the single cache file and age its computed_at beyond the TTL.
        cache_root = os.path.join(self._tmp, "configuration", "tle-cache", "_passes")
        files = [os.path.join(cache_root, f) for f in os.listdir(cache_root)]
        self.assertTrue(files)
        cf = files[0]
        data = json.load(open(cf))
        data["computed_at"] = data["computed_at"] - self.routes._CACHE_TTL_S - 60
        json.dump(data, open(cf, "w"))

        calls = []
        orig = self.predict.compute_passes
        def counting(*a, **k):
            calls.append(1)
            return orig(*a, **k)
        self.predict.compute_passes = counting
        try:
            r = self.client.get("/api/satellites/passes?window=24")
            self.assertEqual(r.status_code, 200)
            self.assertGreater(len(calls), 0)   # stale window → recomputed, not served
        finally:
            self.predict.compute_passes = orig


if __name__ == "__main__":
    unittest.main()
