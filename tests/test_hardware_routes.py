import json, os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app
from common import hardware as HW


class _SyncThread:
    """Stand-in for threading.Thread that runs the target synchronously on
    start() — lets a test observe a fire-and-forget call within the request."""
    def __init__(self, target=None, daemon=None, **kw):
        self._target = target
    def start(self):
        if self._target:
            self._target()


class HardwareRoutesTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_devices_route_shape(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "label": "X"}},
                           assignments={"adsb": "a"})
        # The route default-assigns the lone dongle to openwebrx + aprs too
        # (shared) — mock save so nothing touches disk. Present count matches the
        # one declared dongle so neither auto-declare nor unplug-reconcile fires.
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(oasis_app.HD_detect, "rtl_sdr_usb_count", return_value=1):
            r = self.c.get("/api/hardware/devices")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertEqual(body["devices"][0]["id"], "a")
        # Shared, idle dongle → assignee lists every service that defaults to it.
        self.assertEqual(body["devices"][0]["assignee"], "adsb, openwebrx, aprs")

    def test_assign_success(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "assign") as mocked_assign:
            r = self.c.post("/api/hardware/assign",
                            json={"service": "adsb", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked_assign.called)

    def test_assign_refuses_held_exclusive_device_409(self):
        # rtl-sdr is shared now, so the holder-conflict 409 only fires on
        # exclusive kinds (digirig/dra-pi): a digirig held by winlink can't be
        # taken by aprs.
        inv = HW.Inventory(
            devices={"d": {"id": "d", "kind": "digirig", "ptt": "/dev/x", "alsa": "hw:0,0"}},
            assignments={"winlink": "d"})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.post("/api/hardware/assign",
                            json={"service": "aprs", "device_id": "d"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(json.loads(r.data)["holder"], "winlink")

    def test_assign_shares_rtl_sdr_dongle(self):
        # A dongle already held by adsb can still be assigned to aprs (shared).
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "assign") as mocked_assign:
            r = self.c.post("/api/hardware/assign",
                            json={"service": "aprs", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked_assign.called)

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
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(oasis_app.HD_detect, "rtl_sdr_usb_count", return_value=1):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        self.assertEqual(body["services"]["adsb"],
                          {"device_id": "a", "ok": True, "reason": ""})
        self.assertEqual(body["services"]["winlink"],
                          {"device_id": None, "ok": False, "reason": "unassigned"})
        # openwebrx is now surfaced (advisory) and shares the lone dongle with
        # adsb + aprs via default-assign.
        self.assertEqual(body["services"]["openwebrx"],
                          {"device_id": "a", "ok": True, "reason": ""})
        self.assertEqual(body["services"]["aprs"],
                          {"device_id": "a", "ok": True, "reason": ""})

    def test_devices_route_default_assigns_lone_free_dongle_to_adsb(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "label": "X"}},
                           assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(oasis_app.HD_detect, "rtl_sdr_usb_count", return_value=1):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        self.assertEqual(body["services"]["adsb"]["device_id"], "a")
        self.assertTrue(body["services"]["adsb"]["ok"])

    def test_devices_route_auto_declares_detected_dongles(self):
        # present (2) > declared (0) → scan runs and declares both distinct serials.
        inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(oasis_app.HD_detect, "rtl_sdr_usb_count", return_value=2), \
             mock.patch.object(oasis_app.HD_detect, "scan",
                               return_value={"rtl_sdr": [{"index": 0, "serial": "00000001"},
                                                          {"index": 1, "serial": "00001000"}]}):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        declared_ids = [d["id"] for d in body["devices"]]
        self.assertIn("rtl-sdr-00000001", declared_ids)
        self.assertIn("rtl-sdr-00001000", declared_ids)
        # adsb defaults to the first present dongle.
        self.assertEqual(body["services"]["adsb"]["device_id"], "rtl-sdr-00000001")
        self.assertTrue(body["services"]["adsb"]["ok"])

    def test_devices_route_skips_scan_when_all_present_dongles_declared(self):
        # present (1) == declared (1) → the expensive rtl_test scan is skipped.
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1", "label": "X"}},
                           assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(oasis_app.HD_detect, "rtl_sdr_usb_count", return_value=1), \
             mock.patch.object(oasis_app.HD_detect, "scan") as mocked_scan:
            r = self.c.get("/api/hardware/devices")
        self.assertEqual(r.status_code, 200)
        mocked_scan.assert_not_called()

    def test_devices_route_undeclares_unplugged_dongle(self):
        # present (1) < declared (2) → the removed dongle is undeclared so the
        # service that had it no longer shows it assigned.
        inv = HW.Inventory(
            devices={"rtl-sdr-A": {"id": "rtl-sdr-A", "kind": "rtl-sdr", "serial": "A", "label": "A"},
                     "rtl-sdr-B": {"id": "rtl-sdr-B", "kind": "rtl-sdr", "serial": "B", "label": "B"}},
            assignments={"adsb": "rtl-sdr-A", "aprs": "rtl-sdr-B"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(oasis_app.HD_detect, "rtl_sdr_usb_count", return_value=1), \
             mock.patch.object(oasis_app.HD_detect, "scan",
                               return_value={"rtl_sdr": [{"index": 0, "serial": "A"}]}):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        ids = [d["id"] for d in body["devices"]]
        self.assertIn("rtl-sdr-A", ids)
        self.assertNotIn("rtl-sdr-B", ids)                       # unplugged → undeclared
        self.assertFalse(body["services"]["aprs"]["ok"])         # its device is gone
        self.assertEqual(body["services"]["aprs"]["reason"], "device-not-attached")

    def test_devices_route_auto_declares_assigns_and_applies_digirig(self):
        ptt = ("/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_"
               "Controller_3e54e82a-if00-port0")
        inv = HW.Inventory(devices={}, assignments={})
        applied = []
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(oasis_app.HD_detect, "rtl_sdr_usb_count", return_value=0), \
             mock.patch.object(oasis_app.HD_detect, "detect_digirig",
                               return_value={"ptt": ptt, "serial": "3e54e82a"}), \
             mock.patch.object(oasis_app, "_apply_hardware_async",
                               side_effect=lambda: applied.append(1)), \
             mock.patch.object(oasis_app.threading, "Thread", _SyncThread):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        ids = [d["id"] for d in body["devices"]]
        self.assertIn("digirig-3e54e82a", ids)
        self.assertEqual(body["services"]["winlink"]["device_id"], "digirig-3e54e82a")
        self.assertTrue(body["services"]["winlink"]["ok"])
        self.assertEqual(applied, [1])   # direwolf re-templated on the change

    def test_devices_route_scans_when_new_dongle_plugged(self):
        # present (2) > declared (1) → scan runs to pick up the new dongle.
        inv = HW.Inventory(
            devices={"rtl-sdr-00001000": {"id": "rtl-sdr-00001000", "kind": "rtl-sdr",
                                          "serial": "00001000", "label": "X"}},
            assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(oasis_app.HD_detect, "rtl_sdr_usb_count", return_value=2), \
             mock.patch.object(oasis_app.HD_detect, "scan",
                               return_value={"rtl_sdr": [{"index": 0, "serial": "00001000"},
                                                          {"index": 1, "serial": "00000001"}]}) as mocked_scan:
            r = self.c.get("/api/hardware/devices")
        self.assertEqual(r.status_code, 200)
        mocked_scan.assert_called_once()
        declared_ids = [d["id"] for d in json.loads(r.data)["devices"]]
        self.assertIn("rtl-sdr-00000001", declared_ids)

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
