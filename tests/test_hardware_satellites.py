import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import hardware


def _inv(devices=None, assignments=None):
    return hardware.Inventory(devices=devices or {}, assignments=assignments or {})


def _dev(id_, kind, **extra):
    return {"id": id_, "kind": kind, **extra}


class SatellitesServiceTest(unittest.TestCase):
    """satellites is the 4th SDR-consuming logical service (spec §4)."""

    def test_assignable_to_rtl_sdr(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")})
        ok, holder = hardware.can_assign(inv, "satellites", "a")
        self.assertTrue(ok)
        self.assertIsNone(holder)

    def test_shares_dongle_with_aprs(self):
        # rtl-sdr is shared — assigning satellites to a dongle already held by
        # aprs must NOT be refused on holder grounds.
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"aprs": "a"})
        ok, _ = hardware.can_assign(inv, "satellites", "a")
        self.assertTrue(ok)

    def test_refused_on_soundcard_kind(self):
        inv = _inv(devices={"d": _dev("d", "digirig")})
        ok, _ = hardware.can_assign(inv, "satellites", "d")
        self.assertFalse(ok)

    def test_synthetic_unit(self):
        inv = _inv()
        self.assertEqual(hardware.service_units(inv, "satellites"), ["satellites-listen"])

    def test_device_state_running_from_wrapped_is_active(self):
        # The recorder's live state reaches device_states via a wrapped is_active
        # answering the synthetic "satellites-listen" unit (see listen bridge).
        inv = _inv(devices={"a": _dev("a", "rtl-sdr", label="shared dongle")},
                   assignments={"satellites": "a"})
        states = hardware.device_states(inv, is_active=lambda u: u == "satellites-listen")
        self.assertEqual(states[0]["assignee"], "satellites")
        self.assertTrue(states[0]["running"])

    def test_device_state_idle_when_not_recording(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"satellites": "a"})
        states = hardware.device_states(inv, is_active=lambda u: False)
        self.assertFalse(states[0]["running"])


if __name__ == "__main__":
    unittest.main()
