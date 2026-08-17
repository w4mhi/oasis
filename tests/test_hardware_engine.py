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
                                   "eligible": ["aprs", "adsb", "openwebrx", "satellites", "nwr"],
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


class DrawsDeviceModelTest(unittest.TestCase):
    """The DRAWS HAT has TWO independent radio ports on one stereo codec, so it
    is declared as TWO device records rather than one. That is what lets APRS
    hold the left port while Winlink holds the right SIMULTANEOUSLY, under the
    existing per-device exclusivity rule — no special non-exclusive branch."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_declares_both_ports(self):
        inv = _inv()
        hardware.auto_declare_draws(self.dir, inv, True)
        self.assertEqual(inv.devices["draws-left"],
                         {"id": "draws-left", "kind": "draws", "ptt": "gpio12",
                          "alsa": "draws", "channel": 0, "label": "oasis-draws-winlink (left, ch0)"})
        self.assertEqual(inv.devices["draws-right"],
                         {"id": "draws-right", "kind": "draws", "ptt": "gpio23",
                          "alsa": "draws", "channel": 1, "label": "oasis-draws-aprs (right, ch1)"})

    def test_absent_declares_nothing(self):
        inv = _inv()
        hardware.auto_declare_draws(self.dir, inv, False)
        self.assertEqual(inv.devices, {})

    def test_declaration_is_idempotent(self):
        inv = _inv()
        hardware.auto_declare_draws(self.dir, inv, True)
        hardware.auto_declare_draws(self.dir, inv, True)
        self.assertEqual(len([d for d in inv.devices.values()
                              if d.get("kind") == "draws"]), 2)

    def test_reconcile_removes_both_ports_when_card_gone(self):
        inv = _inv()
        hardware.auto_declare_draws(self.dir, inv, True)
        inv.assignments["winlink"] = "draws-right"
        hardware.reconcile_draws(self.dir, inv, False)
        self.assertNotIn("draws-left", inv.devices)
        self.assertNotIn("draws-right", inv.devices)
        self.assertEqual(inv.assignments.get("winlink"), "draws-right")  # dangles

    def test_reconcile_keeps_ports_when_card_present(self):
        inv = _inv()
        hardware.auto_declare_draws(self.dir, inv, True)
        hardware.reconcile_draws(self.dir, inv, True)
        self.assertIn("draws-left", inv.devices)
        self.assertIn("draws-right", inv.devices)

    def test_aprs_and_winlink_hold_different_ports_at_once(self):
        """The whole point of the two-device model. Roles are fixed by the pat
        AGW-port-0 limitation: winlink on the LEFT (ch0), aprs on the RIGHT."""
        inv = _inv()
        hardware.auto_declare_draws(self.dir, inv, True)
        ok, _ = hardware.can_assign(inv, "winlink", "draws-left")
        self.assertTrue(ok)
        inv.assignments["winlink"] = "draws-left"
        ok, holder = hardware.can_assign(inv, "aprs", "draws-right")
        self.assertTrue(ok, "aprs must be able to hold the right port while winlink holds the left")
        self.assertIsNone(holder)

    def test_a_single_port_is_still_exclusive(self):
        inv = _inv()
        hardware.auto_declare_draws(self.dir, inv, True)
        inv.assignments["aprs"] = "draws-left"
        ok, holder = hardware.can_assign(inv, "winlink", "draws-left")
        self.assertFalse(ok)
        self.assertEqual(holder, "aprs")

    def test_winlink_accepts_the_draws_kind(self):
        self.assertIn("draws", hardware.DEVICE_KIND_FOR_SERVICE["winlink"])

    def test_aprs_accepts_the_draws_kind(self):
        self.assertIn("draws", hardware.DEVICE_KIND_FOR_SERVICE["aprs"])

    def test_draws_is_a_valid_kind(self):
        self.assertIn("draws", hardware.VALID_KINDS)


class DrawsServiceUnitsTest(unittest.TestCase):
    """On a DRAWS box the shared 2-channel direwolf-draws IS the radio stack for
    winlink -- pat-direwolf must never run, because it would open the same card
    (and, templated for a DRA-Pi it cannot see, just crash-loops). service_units
    is already mode-dependent for aprs; winlink now works the same way."""

    def test_winlink_on_draws_uses_the_shared_tnc(self):
        inv = _inv(devices={"draws-right": _dev("draws-right", "draws", channel=1)},
                   assignments={"winlink": "draws-right"})
        self.assertEqual(hardware.service_units(inv, "winlink"),
                         [hardware.DRAWS_TNC_UNIT])

    def test_winlink_on_dra_pi_still_uses_pat_direwolf(self):
        inv = _inv(devices={"dra-pi": _dev("dra-pi", "dra-pi")},
                   assignments={"winlink": "dra-pi"})
        self.assertEqual(hardware.service_units(inv, "winlink"), ["pat-direwolf"])

    def test_winlink_unassigned_still_uses_pat_direwolf(self):
        self.assertEqual(hardware.service_units(_inv(), "winlink"), ["pat-direwolf"])

    def test_aprs_on_draws_is_soundcard_only(self):
        """A draws port is a radio port, not an SDR, so no rtl feed unit."""
        inv = _inv(devices={"draws-left": _dev("draws-left", "draws", channel=0)},
                   assignments={"aprs": "draws-left"})
        self.assertNotIn(hardware.APRS_FEED_UNIT, hardware.service_units(inv, "aprs"))


class SharedInfrastructureUnitTest(unittest.TestCase):
    """Regression 2026-08-06: making direwolf-draws winlink's unit let the
    service-control STOP path stop it -- which also killed APRS (both ports are
    channels of that one TNC) and left the Winlink card reading STOPPED. The
    unit must still report STATUS for winlink (so an up TNC reads ON AIR) but
    must never be stopped on a per-service action."""

    def _inv_draws(self):
        return _inv(devices={"draws-right": _dev("draws-right", "draws", channel=1),
                             "draws-left": _dev("draws-left", "draws", channel=0)},
                    assignments={"winlink": "draws-right"})

    def test_status_still_sees_the_tnc(self):
        self.assertEqual(hardware.service_units(self._inv_draws(), "winlink"),
                         [hardware.DRAWS_TNC_UNIT])

    def test_stoppable_units_excludes_the_shared_tnc(self):
        self.assertEqual(hardware.stoppable_units(self._inv_draws(), "winlink"), [])

    def test_stoppable_units_is_unchanged_for_ordinary_services(self):
        inv = _inv(devices={"dra-pi": _dev("dra-pi", "dra-pi")},
                   assignments={"winlink": "dra-pi"})
        self.assertEqual(hardware.stoppable_units(inv, "winlink"), ["pat-direwolf"])

    def test_release_does_not_stop_the_shared_tnc(self):
        stopped = []
        inv = self._inv_draws()
        hardware.release(self.dir, inv, "winlink", stop_fn=stopped.append)
        self.assertEqual(stopped, [],
                         "unassigning winlink must not stop the shared TNC")

    def test_reroute_away_does_not_stop_the_shared_tnc(self):
        stopped = []
        inv = self._inv_draws()
        inv.devices["dra-pi"] = _dev("dra-pi", "dra-pi")
        hardware.reroute(self.dir, inv, "winlink", "dra-pi",
                         start_fn=lambda u: None, stop_fn=stopped.append)
        self.assertNotIn(hardware.DRAWS_TNC_UNIT, stopped)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()


class DrawsLabelRefreshTest(unittest.TestCase):
    """auto_declare only fires when NO draws device exists, so a box declared
    before a label/field change keeps the stale record forever. Refresh the
    static fields in place, without disturbing assignments or locks."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_refreshes_stale_labels_in_place(self):
        inv = _inv(devices={
            "draws-left": {"id": "draws-left", "kind": "draws", "ptt": "gpio12",
                           "alsa": "draws", "channel": 0,
                           "label": "DRAWS left (APRS)"},
            "draws-right": {"id": "draws-right", "kind": "draws", "ptt": "gpio23",
                            "alsa": "draws", "channel": 1,
                            "label": "DRAWS right (Winlink)"}},
            assignments={"winlink": "draws-right"})
        hardware.auto_declare_draws(self.dir, inv, True)
        self.assertEqual(inv.devices["draws-left"]["label"],
                         "oasis-draws-winlink (left, ch0)")
        self.assertEqual(inv.devices["draws-right"]["label"],
                         "oasis-draws-aprs (right, ch1)")

    def test_refresh_preserves_assignments(self):
        inv = _inv(devices={"draws-right": {"id": "draws-right", "kind": "draws",
                                            "label": "old"}},
                   assignments={"winlink": "draws-right"})
        hardware.auto_declare_draws(self.dir, inv, True)
        self.assertEqual(inv.assignments.get("winlink"), "draws-right")

    def test_refresh_preserves_a_lock(self):
        """A refresh must not silently unlock a device the operator pinned."""
        inv = _inv(devices={"draws-right": {"id": "draws-right", "kind": "draws",
                                            "label": "old", "locked": True}},
                   assignments={})
        hardware.auto_declare_draws(self.dir, inv, True)
        self.assertTrue(inv.devices["draws-right"].get("locked"))


class WinlinkDrawsChannelGuardTest(unittest.TestCase):
    """pat (wl2k-go v1.0.1) PANICS on any AGW port but 0, so Winlink is only
    ever valid on the DRAWS channel-0 port. Offering the other one lets an
    operator pick a combination that can only fail — refuse it in the engine and
    hide it in the console."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.inv = _inv()
        hardware.auto_declare_draws(self.dir, self.inv, True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_winlink_may_take_the_channel0_port(self):
        ok, _ = hardware.can_assign(self.inv, "winlink", "draws-left")
        self.assertTrue(ok)

    def test_winlink_is_refused_the_channel1_port(self):
        ok, holder = hardware.can_assign(self.inv, "winlink", "draws-right")
        self.assertFalse(ok, "pat cannot use AGW port 1 — must not be offered")
        self.assertIsNone(holder)

    def test_aprs_may_still_take_either_port(self):
        for did in ("draws-left", "draws-right"):
            ok, _ = hardware.can_assign(self.inv, "aprs", did)
            self.assertTrue(ok, did)

    def test_assign_raises_for_the_channel1_port(self):
        self.assertRaises(ValueError, hardware.assign,
                          self.dir, self.inv, "winlink", "draws-right")

    def test_other_kinds_are_unaffected(self):
        inv = _inv(devices={"dg": _dev("dg", "digirig"), "dp": _dev("dp", "dra-pi")})
        for did in ("dg", "dp"):
            ok, _ = hardware.can_assign(inv, "winlink", did)
            self.assertTrue(ok, did)

    def test_eligible_services_exclude_winlink_on_channel1(self):
        self.assertIn("winlink", hardware.eligible_services(self.inv, "draws-left"))
        self.assertNotIn("winlink", hardware.eligible_services(self.inv, "draws-right"))
        self.assertIn("aprs", hardware.eligible_services(self.inv, "draws-right"))


class SatellitesDrawsChannelTest(unittest.TestCase):
    """Satellite RX rides the DRAWS channel-1 port: channel 0 is Winlink's (pat
    can only use AGW port 0), so the two never contend for the same connector."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.inv = _inv()
        hardware.auto_declare_draws(self.dir, self.inv, True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_satellites_may_take_the_channel1_port(self):
        ok, _ = hardware.can_assign(self.inv, "satellites", "draws-right")
        self.assertTrue(ok)

    def test_satellites_is_refused_the_channel0_port(self):
        ok, _ = hardware.can_assign(self.inv, "satellites", "draws-left")
        self.assertFalse(ok, "channel 0 belongs to Winlink")

    def test_the_two_ports_carry_different_services(self):
        self.assertIn("winlink", hardware.eligible_services(self.inv, "draws-left"))
        self.assertNotIn("satellites", hardware.eligible_services(self.inv, "draws-left"))
        self.assertIn("satellites", hardware.eligible_services(self.inv, "draws-right"))
        self.assertNotIn("winlink", hardware.eligible_services(self.inv, "draws-right"))

    def test_satellites_still_takes_an_sdr(self):
        inv = _inv(devices={"d": _dev("d", "rtl-sdr")})
        ok, _ = hardware.can_assign(inv, "satellites", "d")
        self.assertTrue(ok)

    def test_winlink_and_satellites_hold_both_ports_at_once(self):
        self.inv.assignments["winlink"] = "draws-left"
        ok, holder = hardware.can_assign(self.inv, "satellites", "draws-right")
        self.assertTrue(ok)
        self.assertIsNone(holder)
