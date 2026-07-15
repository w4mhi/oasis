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
            # "aprs" (not "openwebrx" — no longer a recognized hw service;
            # both accept rtl-sdr, so this still exercises the holder conflict)
            r = self.c.post("/api/hardware/assign",
                            json={"service": "aprs", "device_id": "a"},
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

    def test_assign_triggers_apply_hardware(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "assign"), \
             mock.patch.object(oasis_app, "subprocess") as mocked_subprocess:
            r = self.c.post("/api/hardware/assign",
                            json={"service": "adsb", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked_subprocess.run.called)
        call_args = mocked_subprocess.run.call_args[0][0]
        self.assertIn("apply_hardware.py", " ".join(call_args))

    def test_release_triggers_apply_hardware(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "release"), \
             mock.patch.object(oasis_app, "subprocess") as mocked_subprocess:
            r = self.c.post("/api/hardware/release", json={"service": "adsb"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked_subprocess.run.called)

    def test_devices_route_includes_service_state(self):
        inv = HW.Inventory(
            devices={"a": {"id": "a", "kind": "rtl-sdr", "label": "X"}},
            assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        self.assertEqual(body["services"]["adsb"],
                          {"device_id": "a", "ok": True, "reason": ""})
        self.assertEqual(body["services"]["winlink"],
                          {"device_id": None, "ok": False, "reason": "unassigned"})
        # openwebrx is intentionally never surfaced — see common/hardware.py.
        self.assertNotIn("openwebrx", body["services"])
        self.assertIn("aprs", body["services"])

    def test_devices_route_default_assigns_lone_free_dongle_to_adsb(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "label": "X"}},
                           assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        self.assertEqual(body["services"]["adsb"]["device_id"], "a")
        self.assertTrue(body["services"]["adsb"]["ok"])

    def test_declare_device_requires_oasis_header(self):
        r = self.c.post("/api/hardware/devices",
                        json={"id": "x", "kind": "rtl-sdr", "serial": "1"})
        self.assertEqual(r.status_code, 403)

    def test_declare_device_rejects_bad_id(self):
        r = self.c.post("/api/hardware/devices",
                        json={"id": "bad id!", "kind": "rtl-sdr", "serial": "1"},
                        headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 400)

    def test_declare_device_rejects_unknown_kind(self):
        r = self.c.post("/api/hardware/devices",
                        json={"id": "x", "kind": "toaster", "serial": "1"},
                        headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 400)

    def test_declare_device_rejects_missing_kind_field(self):
        r = self.c.post("/api/hardware/devices",
                        json={"id": "x", "kind": "rtl-sdr"},
                        headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 400)

    def test_declare_device_digirig_requires_ptt_and_alsa(self):
        r = self.c.post("/api/hardware/devices",
                        json={"id": "r1", "kind": "digirig", "ptt": "/dev/serial/by-id/x"},
                        headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 400)

    def test_declare_device_rejects_duplicate_id(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.post("/api/hardware/devices",
                            json={"id": "a", "kind": "rtl-sdr", "serial": "2"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 409)

    def test_declare_device_success(self):
        inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save") as mocked_save:
            r = self.c.post("/api/hardware/devices",
                            json={"id": "sdr-1", "kind": "rtl-sdr",
                                  "serial": "00000042", "label": "ADS-B dongle"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked_save.called)
        body = json.loads(r.data)
        self.assertEqual(body["device"]["id"], "sdr-1")
        self.assertEqual(body["device"]["serial"], "00000042")
        self.assertEqual(body["device"]["label"], "ADS-B dongle")

    def test_declare_device_defaults_label_to_id(self):
        inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"):
            r = self.c.post("/api/hardware/devices",
                            json={"id": "sdr-2", "kind": "rtl-sdr", "serial": "9"},
                            headers={"X-OASIS-Request": "1"})
        body = json.loads(r.data)
        self.assertEqual(body["device"]["label"], "sdr-2")


if __name__ == "__main__":
    unittest.main()
