import os, sys, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import hardware

def _inv(devices=None, assignments=None):
    return hardware.Inventory(devices=devices or {}, assignments=assignments or {})

def _dev(id_, kind, **extra):
    return {"id": id_, "kind": kind, **extra}

class AssigneeTest(unittest.TestCase):
    def test_returns_none_when_unassigned(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")})
        self.assertIsNone(hardware.assignee(inv, "a"))

    def test_returns_the_assigned_service(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        self.assertEqual(hardware.assignee(inv, "a"), "adsb")

class DeviceStatesTest(unittest.TestCase):
    def test_shape_and_running_state(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr", label="ADS-B dongle")},
                   assignments={"adsb": "a"})
        states = hardware.device_states(inv, is_active=lambda u: u == "dump1090-fa")
        self.assertEqual(states, [{"id": "a", "label": "ADS-B dongle", "kind": "rtl-sdr",
                                   "assignee": "adsb", "running": True}])

    def test_unassigned_device_never_running(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")})
        states = hardware.device_states(inv, is_active=lambda u: True)
        self.assertEqual(states[0]["assignee"], None)
        self.assertEqual(states[0]["running"], False)

class CanStartTest(unittest.TestCase):
    def test_unassigned_service_refused(self):
        inv = _inv()
        ok, reason = hardware.can_start(inv, "adsb")
        self.assertFalse(ok)
        self.assertEqual(reason, "unassigned")

    def test_device_not_attached_refused(self):
        inv = _inv(assignments={"adsb": "ghost"})  # device not in inv.devices
        ok, reason = hardware.can_start(inv, "adsb")
        self.assertFalse(ok)
        self.assertEqual(reason, "device-not-attached")

    def test_assigned_and_present_allowed(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        ok, reason = hardware.can_start(inv, "adsb")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

class CanAssignTest(unittest.TestCase):
    def test_free_device_allowed(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")})
        ok, holder = hardware.can_assign(inv, "adsb", "a")
        self.assertTrue(ok)
        self.assertIsNone(holder)

    def test_already_assigned_device_refused_names_holder(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        ok, holder = hardware.can_assign(inv, "openwebrx", "a")
        self.assertFalse(ok)
        self.assertEqual(holder, "adsb")

    def test_reassigning_same_service_to_same_device_allowed(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        ok, holder = hardware.can_assign(inv, "adsb", "a")
        self.assertTrue(ok)

    def test_wrong_kind_for_service_refused(self):
        inv = _inv(devices={"a": _dev("a", "dra-pi")})
        ok, holder = hardware.can_assign(inv, "adsb", "a")  # adsb needs rtl-sdr
        self.assertFalse(ok)

class AssignReleaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_assign_persists_to_disk(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")})
        hardware.assign(self.dir, inv, "adsb", "a")
        reloaded = hardware.load(self.dir)
        self.assertEqual(reloaded.assignments["adsb"], "a")

    def test_assign_raises_when_device_held(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        with self.assertRaises(ValueError):
            hardware.assign(self.dir, inv, "openwebrx", "a")

    def test_release_clears_assignment_and_persists(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        hardware.save(self.dir, inv)
        hardware.release(self.dir, inv, "adsb")
        reloaded = hardware.load(self.dir)
        self.assertNotIn("adsb", reloaded.assignments)

    def test_release_stops_running_service_first(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        stopped = []
        hardware.release(self.dir, inv, "adsb",
                         stop_fn=lambda unit: stopped.append(unit))
        self.assertEqual(stopped, ["dump1090-fa"])

if __name__ == "__main__":
    unittest.main()
