import os, sys, unittest
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
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()

    def test_api_satellites_shape(self):
        r = self.client.get("/api/satellites")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("satellites", data)
        self.assertIn("tle_age_days", data)
        self.assertIn("station", data)

if __name__ == "__main__":
    unittest.main()
