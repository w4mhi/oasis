import json, os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app
from common import hardware as HW


class HardwareRoutesTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_devices_route_shape(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "label": "X"}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.get("/api/hardware/devices")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertEqual(body["devices"][0]["id"], "a")
        self.assertEqual(body["devices"][0]["assignee"], "adsb")

    def test_assign_success(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "assign") as mocked_assign:
            r = self.c.post("/api/hardware/assign",
                            json={"service": "adsb", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked_assign.called)

    def test_assign_refuses_held_device_409(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.post("/api/hardware/assign",
                            json={"service": "openwebrx", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(json.loads(r.data)["holder"], "adsb")

    def test_assign_requires_oasis_header(self):
        r = self.c.post("/api/hardware/assign", json={"service": "adsb", "device_id": "a"})
        self.assertEqual(r.status_code, 403)

    def test_release_calls_stop_and_persists(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "release") as mocked_release:
            r = self.c.post("/api/hardware/release", json={"service": "adsb"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked_release.called)

    def test_unknown_service_rejected(self):
        r = self.c.post("/api/hardware/assign",
                        json={"service": "not-a-real-service", "device_id": "a"},
                        headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
