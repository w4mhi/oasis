import json, os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app
from common import hardware as HW


class HardwareGateTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_units_still_allowlisted(self):
        for unit in ("dump1090-fa", "graywolf", "pat-direwolf", "openwebrx", "aprs-sdr-feed"):
            self.assertIn(unit, oasis_app._OASIS_SERVICES)
            self.assertIn(unit, oasis_app._CONTROLLABLE_SERVICES)

    def test_service_for_unit_resolves_via_inventory(self):
        from common import hardware as HW
        empty = HW.Inventory(devices={}, assignments={})
        self.assertEqual(HW.service_for_unit(empty, "dump1090-fa"), "adsb")
        self.assertEqual(HW.service_for_unit(empty, "graywolf"), "aprs")
        self.assertEqual(HW.service_for_unit(empty, "pat-direwolf"), "winlink")
        self.assertIsNone(HW.service_for_unit(empty, "kiwix"))
        # feed maps to aprs only when aprs is assigned an sdr
        sdr = HW.Inventory(devices={"s": {"id": "s", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={"aprs": "s"})
        self.assertEqual(HW.service_for_unit(sdr, "aprs-sdr-feed"), "aprs")
        self.assertIsNone(HW.service_for_unit(empty, "aprs-sdr-feed"))

    def test_starting_unassigned_service_refused_409(self):
        # api_service() early-returns 200 supported:false on non-Linux, before
        # ever reaching the hardware gate — patch sys.platform so the test
        # exercises the gate itself regardless of the host OS running the suite.
        empty_inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=empty_inv), \
             mock.patch("sys.platform", "linux"):
            r = self.c.post("/api/service", json={"unit": "dump1090-fa", "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(json.loads(r.data)["reason"], "unassigned")

    def test_starting_assigned_service_is_not_gate_refused(self):
        # Assigned + present -> the hardware gate passes; whatever happens next
        # (the actual systemctl call) is unmocked here and will report its own
        # outcome (sudo -n systemctl will fail in this test environment) — the
        # point of this test is ONLY that we do not get a 409
        # "unassigned"/"device-not-attached" from the gate itself.
        inv = HW.Inventory(devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
                           assignments={"adsb": "a"})
        with mock.patch.object(HW, "load", return_value=inv), \
             mock.patch("sys.platform", "linux"):
            r = self.c.post("/api/service", json={"unit": "dump1090-fa", "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        self.assertNotEqual(r.status_code, 409)

    def test_non_claiming_unit_not_gated(self):
        # kiwix has no logical-service mapping. The gate now always loads the
        # inventory first (aprs-sdr-feed's mapping is assignment-dependent, so
        # resolution can't skip loading), but resolution to None means kiwix is
        # still never actually gated (no 409).
        empty_inv = HW.Inventory(devices={}, assignments={})
        with mock.patch.object(HW, "load", return_value=empty_inv), \
             mock.patch("sys.platform", "linux"):
            r = self.c.post("/api/service", json={"unit": "kiwix", "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        self.assertNotEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
