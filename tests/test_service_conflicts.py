import os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app
from routes import service_control
from common import hardware as HW


class HardwareGateTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_units_still_allowlisted(self):
        for unit in ("dump1090-fa", "graywolf", "pat-direwolf", "openwebrx", "aprs-sdr-feed"):
            self.assertIn(unit, service_control._OASIS_SERVICES)
            self.assertIn(unit, service_control._CONTROLLABLE_SERVICES)

    def test_service_for_unit_resolves_via_inventory(self):
        from common import hardware as HW
        empty = HW.Inventory(devices={}, assignments={})
        self.assertEqual(HW.service_for_unit(empty, "dump1090-fa"), "adsb")
        # graywolf is never hardware-gated — GrayWolf manages its own device
        # assignment inside its own UI.
        self.assertIsNone(HW.service_for_unit(empty, "graywolf"))
        self.assertEqual(HW.service_for_unit(empty, "pat-direwolf"), "winlink")
        self.assertIsNone(HW.service_for_unit(empty, "kiwix"))
        # feed maps to aprs only when aprs is assigned an sdr
        sdr = HW.Inventory(devices={"s": {"id": "s", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={"aprs": "s"})
        self.assertEqual(HW.service_for_unit(sdr, "aprs-sdr-feed"), "aprs")
        self.assertIsNone(HW.service_for_unit(empty, "aprs-sdr-feed"))

    def test_starting_unassigned_winlink_never_refused(self):
        # winlink was the LAST service on the hard "unassigned -> refuse" gate;
        # with that gate removed (spec 2026-07-15 §4, "never refuse a start"),
        # even an unassigned pat-direwolf start is no longer pre-emptively
        # blocked. Pat's separate telnet transport is unaffected either way; RF
        # contention with GrayWolf is resolved client-side at start-click time,
        # not by a server 409. (systemctl runs unmocked here and fails in this
        # test env — a 500, never a gate 409.)
        empty_inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=empty_inv), \
             mock.patch("sys.platform", "linux"):
            r = self.c.post("/api/service", json={"unit": "pat-direwolf", "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        self.assertNotEqual(r.status_code, 409)

    def test_starting_unassigned_adsb_never_refused(self):
        # adsb is migrated off the hard gate
        # (specs/2026-07-15-hardware-conflict-resolution-v2-design.md §4) — an
        # unassigned dump1090-fa start is no longer pre-emptively blocked.
        empty_inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=empty_inv), \
             mock.patch("sys.platform", "linux"):
            r = self.c.post("/api/service", json={"unit": "dump1090-fa", "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        self.assertNotEqual(r.status_code, 409)

    def test_starting_unassigned_aprs_feed_never_refused(self):
        # aprs is migrated off the hard gate too (shared-dongle model, resolved
        # at start-click time) — an unassigned aprs-sdr-feed start is no longer
        # pre-emptively blocked. aprs-sdr-feed maps to the aprs service only
        # when aprs holds an sdr, so assign one to exercise the migrated path.
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={"aprs": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch("sys.platform", "linux"):
            r = self.c.post("/api/service", json={"unit": "aprs-sdr-feed", "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        self.assertNotEqual(r.status_code, 409)

    def test_starting_assigned_service_is_not_gate_refused(self):
        # With no hardware start-gate, an assigned+present service is of course
        # not refused either; whatever happens next (the actual systemctl call)
        # is unmocked here and reports its own outcome (sudo -n systemctl fails
        # in this test environment) — the point is ONLY that we never get a 409
        # "unassigned"/"device-not-attached" from a gate.
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch("sys.platform", "linux"):
            r = self.c.post("/api/service", json={"unit": "dump1090-fa", "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        self.assertNotEqual(r.status_code, 409)

    def test_non_claiming_unit_not_gated(self):
        # kiwix has no logical-service mapping and, with the hardware start-gate
        # gone entirely, nothing is gated anyway — a kiwix start never returns a
        # gate 409.
        empty_inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=empty_inv), \
             mock.patch("sys.platform", "linux"):
            r = self.c.post("/api/service", json={"unit": "kiwix", "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        self.assertNotEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
