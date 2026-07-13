import json, os, sys, unittest, urllib.error, urllib.request
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
import app as oasis_app

class _Resp:
    def __init__(self, body, status=200): self._b, self.status = body, status
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False

class AdsbProxyTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_aircraft_passthrough(self):
        body = json.dumps({"now": 1, "aircraft": [{"hex": "a1b2c3"}]}).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: _Resp(body)):
            r = self.c.get("/api/adsb/aircraft")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.data)["aircraft"][0]["hex"], "a1b2c3")

    def test_aircraft_503_when_api_down(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("connection refused")
        with mock.patch.object(urllib.request, "urlopen", boom):
            r = self.c.get("/api/adsb/aircraft")
        self.assertEqual(r.status_code, 503)

if __name__ == "__main__":
    unittest.main()
