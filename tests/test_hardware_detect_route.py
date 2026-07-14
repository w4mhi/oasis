import json, os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app
from common import hardware_detect as HD

class HardwareDetectRouteTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_route_returns_scan_result(self):
        fake = {"rtl_sdr": [{"index": 0, "serial": "1090"}], "alsa": [], "serial": []}
        with mock.patch.object(HD, "scan", return_value=fake):
            r = self.c.get("/api/hardware/detect")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertEqual(body["rtl_sdr"][0]["serial"], "1090")

    def test_scan_is_noop_on_non_linux(self):
        # Real (unmocked) scan() on this macOS box must return empty lists,
        # not raise.
        result = HD.scan()
        self.assertEqual(result, {"rtl_sdr": [], "alsa": [], "serial": []})

if __name__ == "__main__":
    unittest.main()
