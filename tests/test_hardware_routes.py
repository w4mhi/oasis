import json, os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app
from routes import hardware as hardware_routes
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
        # No auto-assign: the route reports only the explicit assignment. Present
        # count matches the one declared dongle so neither auto-declare nor
        # unplug-reconcile fires.
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=1):
            r = self.c.get("/api/hardware/devices")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertEqual(body["devices"][0]["id"], "a")
        # Only the manually-assigned service shows as assignee.
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
             mock.patch.object(hardware_routes, "subprocess") as mocked_subprocess:
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
             mock.patch.object(hardware_routes, "subprocess") as mocked_subprocess:
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
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=1):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        self.assertEqual(body["services"]["adsb"],
                          {"device_id": "a", "ok": True, "reason": ""})
        self.assertEqual(body["services"]["winlink"],
                          {"device_id": None, "ok": False, "reason": "unassigned"})
        # No auto-assign: openwebrx + aprs stay unassigned until the operator
        # assigns a dongle to them via the dropdown.
        self.assertEqual(body["services"]["openwebrx"],
                          {"device_id": None, "ok": False, "reason": "unassigned"})
        self.assertEqual(body["services"]["aprs"],
                          {"device_id": None, "ok": False, "reason": "unassigned"})

    def test_devices_route_leaves_lone_free_dongle_unassigned(self):
        # No auto-assign: a lone free dongle is declared but NOT assigned — the
        # operator picks which service gets it via the dropdown.
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "label": "X"}},
                           assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=1):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        self.assertEqual(body["devices"][0]["id"], "a")            # declared
        self.assertIsNone(body["services"]["adsb"]["device_id"])   # but unassigned
        self.assertFalse(body["services"]["adsb"]["ok"])

    def test_devices_route_auto_declares_detected_dongles(self):
        # present (2) > declared (0) → scan runs and declares both distinct serials.
        inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=2), \
             mock.patch.object(hardware_routes.HD_detect, "scan",
                               return_value={"rtl_sdr": [{"index": 0, "serial": "00000001"},
                                                          {"index": 1, "serial": "00001000"}]}):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        declared_ids = [d["id"] for d in body["devices"]]
        self.assertIn("rtl-sdr-00000001", declared_ids)
        self.assertIn("rtl-sdr-00001000", declared_ids)
        # No auto-assign: both dongles declared, but adsb (and the other SDR
        # services) stay unassigned until the operator picks.
        self.assertIsNone(body["services"]["adsb"]["device_id"])
        self.assertFalse(body["services"]["adsb"]["ok"])

    def test_devices_route_skips_scan_when_all_present_dongles_declared(self):
        # present (1) == declared (1) → the expensive rtl_test scan is skipped.
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1", "label": "X"}},
                           assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=1), \
             mock.patch.object(hardware_routes.HD_detect, "scan") as mocked_scan:
            r = self.c.get("/api/hardware/devices")
        self.assertEqual(r.status_code, 200)
        mocked_scan.assert_not_called()

    def test_devices_route_unassigns_on_unplug_no_failover(self):
        # present (1) < declared (2): the removed dongle is undeclared and the
        # service that had it goes UNASSIGNED — no auto-failover to the survivor
        # (that was auto-assign; assignment is manual now).
        inv = HW.Inventory(
            devices={"rtl-sdr-A": {"id": "rtl-sdr-A", "kind": "rtl-sdr", "serial": "A", "label": "A"},
                     "rtl-sdr-B": {"id": "rtl-sdr-B", "kind": "rtl-sdr", "serial": "B", "label": "B"}},
            assignments={"adsb": "rtl-sdr-A", "aprs": "rtl-sdr-B"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=1), \
             mock.patch.object(hardware_routes.HD_detect, "scan",
                               return_value={"rtl_sdr": [{"index": 0, "serial": "A"}]}):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        ids = [d["id"] for d in body["devices"]]
        self.assertIn("rtl-sdr-A", ids)
        self.assertNotIn("rtl-sdr-B", ids)                          # unplugged → undeclared
        self.assertEqual(body["services"]["adsb"]["device_id"], "rtl-sdr-A")  # survivor keeps its assignment
        self.assertIsNone(body["services"]["aprs"]["device_id"])    # its dongle gone → unassigned, no failover

    def test_devices_route_unassigns_when_last_dongle_unplugged(self):
        # present (0) < declared (1) and no survivor → the service goes unassigned.
        inv = HW.Inventory(
            devices={"rtl-sdr-A": {"id": "rtl-sdr-A", "kind": "rtl-sdr", "serial": "A", "label": "A"}},
            assignments={"adsb": "rtl-sdr-A"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=0), \
             mock.patch.object(hardware_routes.HD_detect, "scan", return_value={"rtl_sdr": []}):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        self.assertEqual(body["devices"], [])
        self.assertIsNone(body["services"]["adsb"]["device_id"])
        self.assertEqual(body["services"]["adsb"]["reason"], "unassigned")

    def test_devices_route_auto_declares_digirig_but_leaves_it_unassigned(self):
        ptt = ("/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_"
               "Controller_3e54e82a-if00-port0")
        inv = HW.Inventory(devices={}, assignments={})
        applied = []
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=0), \
             mock.patch.object(hardware_routes.HD_detect, "detect_digirig",
                               return_value={"ptt": ptt, "serial": "3e54e82a"}), \
             mock.patch.object(hardware_routes.HD_detect, "detect_dra_pi", return_value=False), \
             mock.patch.object(hardware_routes, "_apply_hardware_async",
                               side_effect=lambda: applied.append(1)), \
             mock.patch.object(oasis_app.threading, "Thread", _SyncThread):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        ids = [d["id"] for d in body["devices"]]
        self.assertIn("digirig-3e54e82a", ids)                        # declared
        self.assertIsNone(body["services"]["winlink"]["device_id"])   # but unassigned
        self.assertEqual(applied, [])   # winlink unchanged → no direwolf re-template

    def test_devices_route_auto_declares_dra_pi_but_leaves_it_unassigned(self):
        inv = HW.Inventory(devices={}, assignments={})
        applied = []
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=0), \
             mock.patch.object(hardware_routes.HD_detect, "detect_digirig", return_value=None), \
             mock.patch.object(hardware_routes.HD_detect, "detect_dra_pi", return_value=True), \
             mock.patch.object(hardware_routes, "_apply_hardware_async",
                               side_effect=lambda: applied.append(1)), \
             mock.patch.object(oasis_app.threading, "Thread", _SyncThread):
            r = self.c.get("/api/hardware/devices")
        body = json.loads(r.data)
        ids = [d["id"] for d in body["devices"]]
        self.assertIn("dra-pi", ids)                                  # declared
        self.assertIsNone(body["services"]["winlink"]["device_id"])   # but unassigned
        self.assertEqual(applied, [])

    def test_devices_route_scans_when_new_dongle_plugged(self):
        # present (2) > declared (1) → scan runs to pick up the new dongle.
        inv = HW.Inventory(
            devices={"rtl-sdr-00001000": {"id": "rtl-sdr-00001000", "kind": "rtl-sdr",
                                          "serial": "00001000", "label": "X"}},
            assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes.HD_detect, "rtl_sdr_usb_count", return_value=2), \
             mock.patch.object(hardware_routes.HD_detect, "scan",
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


    def test_release_of_satellites_stops_the_capture_not_systemctl(self):
        # Same defect on the release path: releasing the dongle from satellites
        # ran `systemctl stop satellites-listen` (exit 0, nothing stopped) and
        # cleared the assignment, so the engine believed the dongle was free
        # while rtl_fm still held the tuner.
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={"satellites": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes, "_apply_hardware_async"), \
             mock.patch.object(hardware_routes, "_stop_synthetic") as synthetic, \
             mock.patch.object(hardware_routes, "_systemctl_seq") as systemctl:
            r = self.c.post("/api/hardware/release", json={"service": "satellites"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        synthetic.assert_called_once_with("satellites-listen")
        self.assertNotIn("satellites-listen",
                         [c.args[0] for c in systemctl.call_args_list])


class AssignmentConsoleRoutesTest(unittest.TestCase):
    """The HW/SRV matrix console endpoints (design 2026-07-28)."""

    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_console_state_shape(self):
        inv = HW.Inventory(
            devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1",
                           "label": "RTL1", "locked": True}},
            assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.get("/api/hardware/console")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertTrue(any(s["id"] == "adsb" for s in body["services"]))
        dev = body["devices"][0]
        self.assertEqual(dev["id"], "a")
        self.assertTrue(dev["locked"])
        self.assertEqual(dev["assigned"], "adsb")
        self.assertIn("warnings", body)

    def test_route_success_calls_reroute(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "reroute") as mocked, \
             mock.patch.object(hardware_routes, "_apply_hardware_async"), \
             mock.patch.object(hardware_routes, "_systemctl_seq"):
            r = self.c.post("/api/hardware/route",
                            json={"service": "adsb", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked.called)

    def test_route_displacing_satellites_stops_the_capture_not_systemctl(self):
        """A displaced SYNTHETIC unit goes to its owner, never to systemctl.

        satellites' capture is an ad-hoc rtl_fm subprocess, so
        `systemctl stop satellites-listen` exits 0 and stops nothing: the
        console reported a clean reroute while the displaced recorder still
        held the dongle ADS-B was being handed."""
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={"satellites": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes, "_apply_hardware_async"), \
             mock.patch.object(hardware_routes, "_stop_synthetic") as synthetic, \
             mock.patch.object(hardware_routes, "_systemctl_seq") as systemctl:
            r = self.c.post("/api/hardware/route",
                            json={"service": "adsb", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        synthetic.assert_called_once_with("satellites-listen")
        handed = [c.args[0] for c in systemctl.call_args_list]
        self.assertNotIn("satellites-listen", handed)

    def test_route_onto_satellites_starts_no_synthetic_unit(self):
        # The other half of the same bug: nothing to start from outside, so the
        # console must not try. `systemctl start satellites-listen` is the same
        # silent no-op in the other direction.
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "save"), \
             mock.patch.object(hardware_routes, "_apply_hardware_async"), \
             mock.patch.object(hardware_routes, "_systemctl_seq") as systemctl:
            r = self.c.post("/api/hardware/route",
                            json={"service": "satellites", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("satellites-listen",
                         [c.args[0] for c in systemctl.call_args_list])

    def test_route_locked_returns_409_with_reason(self):
        inv = HW.Inventory(
            devices={"a": {"id": "a", "kind": "rtl-sdr", "locked": True}},
            assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.post("/api/hardware/route",
                            json={"service": "aprs", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(json.loads(r.data)["reason"], "target-locked")

    def test_route_wrong_kind_400(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.post("/api/hardware/route",
                            json={"service": "winlink", "device_id": "a"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 400)

    def test_lock_toggle_calls_set_lock(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}}, assignments={})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(HW, "set_lock") as mocked:
            r = self.c.post("/api/hardware/lock",
                            json={"device_id": "a", "locked": True},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked.called)

    def test_stop_all_keeps_webssh_alive(self):
        with mock.patch.object(hardware_routes, "_systemctl_seq") as mocked:
            r = self.c.post("/api/hardware/stop-all", headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        stopped = [c.args[0] for c in mocked.call_args_list]
        self.assertNotIn("webssh", stopped)      # never sever the remote connection
        self.assertIn("graywolf", stopped)       # but the load IS stopped
        self.assertEqual(mocked.call_count, len(hardware_routes._EMERGENCY_STOP))

    def test_service_stop_refused_when_device_locked(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "locked": True}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv):
            r = self.c.post("/api/hardware/service-stop", json={"service": "adsb"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(json.loads(r.data)["reason"], "source-locked")

    def test_route_forbidden_without_header(self):
        r = self.c.post("/api/hardware/route", json={"service": "adsb", "device_id": "a"})
        self.assertEqual(r.status_code, 403)

    def test_service_stop_stops_units(self):
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr"}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch.object(hardware_routes, "_systemctl_seq") as mocked:
            r = self.c.post("/api/hardware/service-stop",
                            json={"service": "adsb"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked.called)   # dump1090-fa stopped, assignment untouched


class GuardianRoutesTest(unittest.TestCase):
    """Resource-guardian endpoints (design 2026-07-28)."""

    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_guardian_state_shape(self):
        r = self.c.get("/api/hardware/guardian")
        self.assertEqual(r.status_code, 200)
        b = json.loads(r.data)
        for k in ("mode", "enabled", "thresholds", "seconds_left", "stats"):
            self.assertIn(k, b)

    def test_guardian_cancel_goes_to_cooldown(self):
        # Cancel sticks: cooldown (not idle) so it can't re-arm until recovery.
        hardware_routes._GUARD_STATE = {"mode": "armed", "deadline": 9e9, "reason": "temp_c"}
        r = self.c.post("/api/hardware/guardian/cancel", headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(hardware_routes._GUARD_STATE["mode"], "cooldown")

    def test_guardian_cancel_forbidden_without_header(self):
        self.assertEqual(self.c.post("/api/hardware/guardian/cancel").status_code, 403)

    def test_guardian_config_sets_enabled(self):
        with mock.patch.object(hardware_routes, "_save_guardian_config") as saved:
            r = self.c.post("/api/hardware/guardian/config",
                            json={"enabled": False, "thresholds": {"temp_c": 75}},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(saved.called)
        self.assertFalse(json.loads(r.data)["enabled"])


if __name__ == "__main__":
    unittest.main()
