import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import hardware as HW

def _inv(devices=None, assignments=None):
    return HW.Inventory(devices=devices or {}, assignments=assignments or {})

def _dev(id_, kind, **x): return {"id": id_, "kind": kind, **x}

class ServiceUnitsTest(unittest.TestCase):
    def test_static_services_unchanged(self):
        inv = _inv()
        self.assertEqual(HW.service_units(inv, "adsb"), ["dump1090-fa"])
        self.assertEqual(HW.service_units(inv, "winlink"), ["pat-direwolf"])
        # A service OASIS does not manage resolves to no units rather than
        # raising — openwebrx is the live case (retired in 3.92.0), and a stale
        # hardware.json out in the field is what would reach this path.
        self.assertEqual(HW.service_units(inv, "openwebrx"), [])
        self.assertNotIn("openwebrx", HW.SERVICE_UNITS)

    def test_aprs_on_sdr_adds_feed_unit(self):
        inv = _inv(devices={"s": _dev("s", "rtl-sdr", serial="1")}, assignments={"aprs": "s"})
        self.assertEqual(HW.service_units(inv, "aprs"), ["aprs-sdr-feed"])

    def test_aprs_on_radio_port_is_empty(self):
        # GrayWolf is web-admin-configured; OASIS's hardware gate never claims
        # the graywolf unit itself, only the optional SDR feed.
        inv = _inv(devices={"r": _dev("r", "digirig")}, assignments={"aprs": "r"})
        self.assertEqual(HW.service_units(inv, "aprs"), [])

    def test_aprs_unassigned_is_empty(self):
        self.assertEqual(HW.service_units(_inv(), "aprs"), [])

class ServiceForUnitTest(unittest.TestCase):
    def test_static_reverse(self):
        inv = _inv()
        self.assertEqual(HW.service_for_unit(inv, "dump1090-fa"), "adsb")
        # graywolf is never hardware-gated — GrayWolf manages its own device
        # assignment inside its own UI.
        self.assertIsNone(HW.service_for_unit(inv, "graywolf"))
        self.assertEqual(HW.service_for_unit(inv, "pat-direwolf"), "winlink")
        self.assertIsNone(HW.service_for_unit(inv, "kiwix"))

    def test_feed_maps_to_aprs_only_when_aprs_on_sdr(self):
        sdr = _inv(devices={"s": _dev("s", "rtl-sdr", serial="1")}, assignments={"aprs": "s"})
        self.assertEqual(HW.service_for_unit(sdr, "aprs-sdr-feed"), "aprs")
        # when aprs is NOT on an sdr, the feed isn't part of aprs -> ungated
        self.assertIsNone(HW.service_for_unit(_inv(), "aprs-sdr-feed"))

class DeviceStatesResolverTest(unittest.TestCase):
    def test_aprs_sdr_busy_when_feed_active(self):
        inv = _inv(devices={"s": _dev("s", "rtl-sdr", serial="1", label="APRS dongle")},
                   assignments={"aprs": "s"})
        # graywolf idle but the feed is active -> device still shows busy
        states = HW.device_states(inv, is_active=lambda u: u == "aprs-sdr-feed")
        self.assertEqual(states[0]["assignee"], "aprs")
        self.assertTrue(states[0]["running"])

if __name__ == "__main__":
    unittest.main()
