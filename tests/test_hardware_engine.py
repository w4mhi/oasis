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
                                   "serial": "", "ptt": "", "assignee": "adsb", "running": True}])

    def test_includes_serial_for_usb_port_join(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr", serial="00001000")})
        states = hardware.device_states(inv, is_active=lambda u: False)
        self.assertEqual(states[0]["serial"], "00001000")

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

    def test_rtl_sdr_shared_across_services_allowed(self):
        # rtl-sdr is a shared resource: a dongle already held by adsb can still
        # be assigned to openwebrx/aprs (advisory — §2). No holder refusal.
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        ok, holder = hardware.can_assign(inv, "openwebrx", "a")
        self.assertTrue(ok)
        self.assertIsNone(holder)
        ok, holder = hardware.can_assign(inv, "aprs", "a")
        self.assertTrue(ok)
        self.assertIsNone(holder)

    def test_exclusive_kind_already_assigned_refused_names_holder(self):
        # digirig/dra-pi stay exclusive: a device held by winlink is refused.
        inv = _inv(devices={"d": _dev("d", "digirig", ptt="/dev/x", alsa="hw:0,0")},
                   assignments={"winlink": "d"})
        ok, holder = hardware.can_assign(inv, "aprs", "d")
        self.assertFalse(ok)
        self.assertEqual(holder, "winlink")

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

    def test_assign_shares_rtl_sdr_across_services(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"adsb": "a"})
        hardware.assign(self.dir, inv, "openwebrx", "a")
        reloaded = hardware.load(self.dir)
        self.assertEqual(reloaded.assignments, {"adsb": "a", "openwebrx": "a"})

    def test_assign_raises_when_exclusive_device_held(self):
        inv = _inv(devices={"d": _dev("d", "digirig", ptt="/dev/x", alsa="hw:0,0")},
                   assignments={"winlink": "d"})
        with self.assertRaises(ValueError):
            hardware.assign(self.dir, inv, "aprs", "d")

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

class DefaultAssignTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_assigns_the_only_free_compatible_device(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")})
        hardware.default_assign(self.dir, inv, "adsb", {"rtl-sdr"})
        self.assertEqual(inv.assignments["adsb"], "a")
        reloaded = hardware.load(self.dir)
        self.assertEqual(reloaded.assignments["adsb"], "a")

    def test_noop_when_already_assigned(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr"), "b": _dev("b", "rtl-sdr")},
                   assignments={"adsb": "a"})
        hardware.default_assign(self.dir, inv, "adsb", {"rtl-sdr"})
        self.assertEqual(inv.assignments["adsb"], "a")

    def test_shares_rtl_sdr_already_held_by_another_service(self):
        # rtl-sdr is shared: the lone dongle held by openwebrx is still a valid
        # default for adsb — all RTL consumers converge on it out of the box.
        inv = _inv(devices={"a": _dev("a", "rtl-sdr")}, assignments={"openwebrx": "a"})
        hardware.default_assign(self.dir, inv, "adsb", {"rtl-sdr"})
        self.assertEqual(inv.assignments["adsb"], "a")

    def test_skips_exclusive_device_already_held(self):
        inv = _inv(devices={"d": _dev("d", "digirig", ptt="/dev/x", alsa="hw:0,0")},
                   assignments={"winlink": "d"})
        hardware.default_assign(self.dir, inv, "aprs", {"digirig"})
        self.assertNotIn("aprs", inv.assignments)

    def test_skips_wrong_kind(self):
        inv = _inv(devices={"a": _dev("a", "digirig", ptt="/dev/x", alsa="hw:0,0")})
        hardware.default_assign(self.dir, inv, "adsb", {"rtl-sdr"})
        self.assertNotIn("adsb", inv.assignments)

    def test_noop_when_no_devices_at_all(self):
        inv = _inv()
        hardware.default_assign(self.dir, inv, "adsb", {"rtl-sdr"})
        self.assertEqual(inv.assignments, {})

    def test_picks_first_free_in_declaration_order(self):
        inv = _inv(devices={"a": _dev("a", "rtl-sdr"), "b": _dev("b", "rtl-sdr")})
        hardware.default_assign(self.dir, inv, "adsb", {"rtl-sdr"})
        self.assertEqual(inv.assignments["adsb"], "a")

class AutoDeclareRtlSdrsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_declares_the_lone_detected_serial(self):
        inv = _inv()
        hardware.auto_declare_rtl_sdrs(self.dir, inv, ["00001000"])
        self.assertEqual(inv.devices["rtl-sdr-00001000"],
                          {"id": "rtl-sdr-00001000", "kind": "rtl-sdr",
                           "serial": "00001000", "label": "RTL-SDR (00001000)"})
        reloaded = hardware.load(self.dir)
        self.assertIn("rtl-sdr-00001000", reloaded.devices)

    def test_declares_all_distinct_serials(self):
        inv = _inv()
        hardware.auto_declare_rtl_sdrs(self.dir, inv, ["00000001", "00001000"])
        self.assertIn("rtl-sdr-00000001", inv.devices)
        self.assertIn("rtl-sdr-00001000", inv.devices)
        reloaded = hardware.load(self.dir)
        self.assertEqual(len(reloaded.devices), 2)

    def test_declares_newly_detected_alongside_already_declared(self):
        # The 2nd-dongle case: one already declared, a new unique serial shows up.
        inv = _inv(devices={"rtl-sdr-00001000": _dev("rtl-sdr-00001000", "rtl-sdr", serial="00001000")})
        hardware.auto_declare_rtl_sdrs(self.dir, inv, ["00001000", "00000001"])
        self.assertIn("rtl-sdr-00000001", inv.devices)
        self.assertEqual(len(inv.devices), 2)

    def test_noop_when_no_serials_detected(self):
        inv = _inv()
        hardware.auto_declare_rtl_sdrs(self.dir, inv, [])
        self.assertEqual(inv.devices, {})

    def test_skips_duplicate_factory_serials(self):
        # Two dongles sharing the factory 00000001 are indistinguishable — both
        # skipped until one gets a unique serial burned.
        inv = _inv()
        hardware.auto_declare_rtl_sdrs(self.dir, inv, ["00000001", "00000001"])
        self.assertEqual(inv.devices, {})

    def test_declares_unique_but_skips_concurrent_duplicate(self):
        inv = _inv()
        hardware.auto_declare_rtl_sdrs(self.dir, inv, ["00000001", "00000001", "00001000"])
        self.assertIn("rtl-sdr-00001000", inv.devices)
        self.assertNotIn("rtl-sdr-00000001", inv.devices)
        self.assertEqual(len(inv.devices), 1)

class ReconcilePresentRtlSdrsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _two(self, **assignments):
        return _inv(devices={"rtl-sdr-A": _dev("rtl-sdr-A", "rtl-sdr", serial="A"),
                             "rtl-sdr-B": _dev("rtl-sdr-B", "rtl-sdr", serial="B")},
                    assignments=assignments)

    def test_undeclares_removed_idle_dongle(self):
        inv = self._two(adsb="rtl-sdr-A", aprs="rtl-sdr-B")
        # B unplugged: 1 present, rtl_test sees only A, nothing running.
        hardware.reconcile_present_rtl_sdrs(self.dir, inv, ["A"], 1, is_active=lambda u: False)
        self.assertIn("rtl-sdr-A", inv.devices)
        self.assertNotIn("rtl-sdr-B", inv.devices)
        # its assignment is cleared (default_assign then fails it over to A)
        self.assertNotIn("aprs", inv.assignments)
        self.assertEqual(inv.assignments.get("adsb"), "rtl-sdr-A")   # untouched

    def test_keeps_busy_dongle_hidden_from_rtl_test(self):
        inv = self._two(adsb="rtl-sdr-A")
        # A is busy (dump1090-fa active) so rtl_test can't see it; B unplugged.
        hardware.reconcile_present_rtl_sdrs(self.dir, inv, [], 1,
                                            is_active=lambda u: u == "dump1090-fa")
        self.assertIn("rtl-sdr-A", inv.devices)      # kept — running service proves present
        self.assertNotIn("rtl-sdr-B", inv.devices)

    def test_noop_when_detection_ambiguous(self):
        inv = self._two()
        # lsusb says 1 present but rtl_test empty + nothing running: can't tell
        # which one left → leave both rather than guess.
        hardware.reconcile_present_rtl_sdrs(self.dir, inv, [], 1, is_active=lambda u: False)
        self.assertEqual(len(inv.devices), 2)

    def test_undeclares_all_when_none_present(self):
        inv = _inv(devices={"rtl-sdr-A": _dev("rtl-sdr-A", "rtl-sdr", serial="A")},
                   assignments={"adsb": "rtl-sdr-A"})
        hardware.reconcile_present_rtl_sdrs(self.dir, inv, [], 0, is_active=lambda u: False)
        self.assertNotIn("rtl-sdr-A", inv.devices)
        self.assertNotIn("adsb", inv.assignments)   # no survivor → cleared → unassigned

    def test_noop_when_all_present(self):
        inv = _inv(devices={"rtl-sdr-A": _dev("rtl-sdr-A", "rtl-sdr", serial="A")})
        hardware.reconcile_present_rtl_sdrs(self.dir, inv, ["A"], 1, is_active=lambda u: False)
        self.assertIn("rtl-sdr-A", inv.devices)

class DigirigTest(unittest.TestCase):
    _PTT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_3e54e82a-if00-port0"

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_auto_declare_uses_chip_serial_for_stable_id(self):
        inv = _inv()
        hardware.auto_declare_digirig(self.dir, inv, {"ptt": self._PTT, "serial": "3e54e82a"})
        self.assertEqual(inv.devices["digirig-3e54e82a"],
                         {"id": "digirig-3e54e82a", "kind": "digirig",
                          "ptt": self._PTT, "alsa": "", "serial": "3e54e82a", "label": "DigiRig"})

    def test_auto_declare_noop_when_none_detected(self):
        inv = _inv()
        hardware.auto_declare_digirig(self.dir, inv, None)
        self.assertEqual(inv.devices, {})

    def test_auto_declare_noop_when_already_declared(self):
        inv = _inv(devices={"digirig-x": _dev("digirig-x", "digirig", ptt="/dev/x", alsa="")})
        hardware.auto_declare_digirig(self.dir, inv, {"ptt": self._PTT, "serial": "3e54e82a"})
        self.assertNotIn("digirig-3e54e82a", inv.devices)
        self.assertEqual(len(inv.devices), 1)

    def test_reconcile_undeclares_unplugged_digirig(self):
        inv = _inv(devices={"digirig-3e54e82a": _dev("digirig-3e54e82a", "digirig",
                                                     ptt=self._PTT, alsa="")},
                   assignments={"winlink": "digirig-3e54e82a"})
        hardware.reconcile_digirig(self.dir, inv, path_exists=lambda p: False)
        self.assertNotIn("digirig-3e54e82a", inv.devices)
        self.assertEqual(inv.assignments.get("winlink"), "digirig-3e54e82a")   # dangles

    def test_reconcile_keeps_present_digirig(self):
        inv = _inv(devices={"digirig-3e54e82a": _dev("digirig-3e54e82a", "digirig",
                                                     ptt=self._PTT, alsa="")})
        hardware.reconcile_digirig(self.dir, inv, path_exists=lambda p: True)
        self.assertIn("digirig-3e54e82a", inv.devices)

class DraPiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_auto_declare_when_present(self):
        inv = _inv()
        hardware.auto_declare_dra_pi(self.dir, inv, True)
        self.assertEqual(inv.devices["dra-pi"],
                         {"id": "dra-pi", "kind": "dra-pi", "ptt": "gpio12",
                          "alsa": "audioinjectorpi", "label": "DRA-Pi"})

    def test_auto_declare_noop_when_absent(self):
        inv = _inv()
        hardware.auto_declare_dra_pi(self.dir, inv, False)
        self.assertEqual(inv.devices, {})

    def test_auto_declare_noop_when_already_declared(self):
        inv = _inv(devices={"dra-pi": _dev("dra-pi", "dra-pi", ptt="gpio12", alsa="audioinjectorpi")})
        hardware.auto_declare_dra_pi(self.dir, inv, True)
        self.assertEqual(len(inv.devices), 1)

    def test_reconcile_undeclares_when_absent(self):
        inv = _inv(devices={"dra-pi": _dev("dra-pi", "dra-pi", ptt="gpio12", alsa="audioinjectorpi")},
                   assignments={"winlink": "dra-pi"})
        hardware.reconcile_dra_pi(self.dir, inv, False)
        self.assertNotIn("dra-pi", inv.devices)
        self.assertEqual(inv.assignments.get("winlink"), "dra-pi")   # dangles

    def test_reconcile_keeps_when_present(self):
        inv = _inv(devices={"dra-pi": _dev("dra-pi", "dra-pi", ptt="gpio12", alsa="audioinjectorpi")})
        hardware.reconcile_dra_pi(self.dir, inv, True)
        self.assertIn("dra-pi", inv.devices)

if __name__ == "__main__":
    unittest.main()
