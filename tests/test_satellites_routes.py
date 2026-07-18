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
        self.routes.SUITE_ROOT = self._tmp

    def test_api_satellites_shape(self):
        r = self.client.get("/api/satellites")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("satellites", data)
        self.assertIn("tle_age_days", data)
        self.assertIn("station", data)

    def test_passes_endpoint(self):
        r = self.client.get("/api/satellites/passes?window=24")
        self.assertEqual(r.status_code, 200)
        self.assertIn("passes", r.get_json())

    def test_select_toggles(self):
        r = self.client.post("/api/satellites/select", json={"norad": 25544, "selected": False})
        self.assertEqual(r.status_code, 200)
        iss = [s for s in r.get_json()["satellites"] if s["norad"] == 25544][0]
        self.assertFalse(iss["selected"])

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

if __name__ == "__main__":
    unittest.main()
