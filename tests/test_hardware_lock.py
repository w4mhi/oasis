import os, sys, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import hardware


def _write(dir_, body):
    os.makedirs(os.path.join(dir_, "configuration"), exist_ok=True)
    with open(os.path.join(dir_, "configuration", "hardware.json"), "w") as f:
        f.write(body)


class DeviceLockTest(unittest.TestCase):
    """Per-device lock (design 2026-07-28): a locked device is protected from
    reassignment/auto-assign until the operator unlocks it. Slice 1 = the flag
    model + persistence; the reroute/auto-assign guards build on top."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_device_defaults_unlocked(self):
        # A device with no "locked" key in hardware.json reads as unlocked.
        _write(self.dir, '{"version":1,'
               '"devices":[{"id":"sdr-1","kind":"rtl-sdr","serial":"1"}],'
               '"assignments":{}}')
        inv = hardware.load(self.dir)
        self.assertFalse(hardware.is_locked(inv, "sdr-1"))

    def test_set_lock_persists_across_reload(self):
        _write(self.dir, '{"version":1,'
               '"devices":[{"id":"sdr-1","kind":"rtl-sdr","serial":"1"}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        hardware.set_lock(self.dir, inv, "sdr-1", True)
        self.assertTrue(hardware.is_locked(inv, "sdr-1"))          # in-memory
        self.assertTrue(hardware.is_locked(hardware.load(self.dir), "sdr-1"))  # persisted

    def test_set_lock_false_unlocks_and_persists(self):
        _write(self.dir, '{"version":1,'
               '"devices":[{"id":"sdr-1","kind":"rtl-sdr","serial":"1","locked":true}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        self.assertTrue(hardware.is_locked(inv, "sdr-1"))          # starts locked
        hardware.set_lock(self.dir, inv, "sdr-1", False)
        self.assertFalse(hardware.is_locked(hardware.load(self.dir), "sdr-1"))


class RerouteTest(unittest.TestCase):
    """Console-enforced exclusive reroute (design 2026-07-28): flipping a matrix
    toggle reassigns one service to a dongle + starts it, displacing whatever was
    there. Units start/stop via injected fns so the logic is testable off-Pi."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_reroute_unassigned_service_assigns_and_starts(self):
        _write(self.dir, '{"version":1,'
               '"devices":[{"id":"sdr-1","kind":"rtl-sdr","serial":"1"}],'
               '"assignments":{}}')
        inv = hardware.load(self.dir)
        started = []
        hardware.reroute(self.dir, inv, "adsb", "sdr-1", start_fn=started.append)
        self.assertEqual(inv.assignments["adsb"], "sdr-1")          # assigned
        self.assertIn("dump1090-fa", started)                       # its unit started
        self.assertEqual(hardware.load(self.dir).assignments["adsb"], "sdr-1")  # persisted

    def test_reroute_displaces_other_service_on_target_device(self):
        # sdr-1 is running ADS-B; routing APRS onto it must stop + unassign ADS-B
        # (console-enforced exclusive: one service per dongle).
        _write(self.dir, '{"version":1,'
               '"devices":[{"id":"sdr-1","kind":"rtl-sdr","serial":"1"}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        started, stopped = [], []
        hardware.reroute(self.dir, inv, "aprs", "sdr-1",
                         start_fn=started.append, stop_fn=stopped.append)
        self.assertEqual(inv.assignments.get("aprs"), "sdr-1")       # new service on
        self.assertNotIn("adsb", inv.assignments)                    # old one displaced
        self.assertIn("dump1090-fa", stopped)                        # its unit stopped
        self.assertIn("aprs-sdr-feed", started)                      # aprs' rtl_fm feed started
        self.assertNotIn("adsb", hardware.load(self.dir).assignments)  # persisted

    def test_reroute_moving_service_stops_it_on_old_device_first(self):
        # ADS-B on sdr-1; move it to sdr-2. It must be stopped on sdr-1 before it
        # starts on sdr-2, and sdr-1 is left free.
        _write(self.dir, '{"version":1,"devices":['
               '{"id":"sdr-1","kind":"rtl-sdr","serial":"1"},'
               '{"id":"sdr-2","kind":"rtl-sdr","serial":"2"}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        started, stopped = [], []
        hardware.reroute(self.dir, inv, "adsb", "sdr-2",
                         start_fn=started.append, stop_fn=stopped.append)
        self.assertEqual(inv.assignments["adsb"], "sdr-2")           # moved
        self.assertIn("dump1090-fa", stopped)                        # stopped on old
        self.assertIn("dump1090-fa", started)                        # started on new
        self.assertEqual(hardware.assignees(inv, "sdr-1"), [])       # old dongle freed

    def test_reroute_onto_satellites_starts_no_synthetic_unit(self):
        # satellites' only unit is SYNTHETIC (SYNTHETIC_UNITS): the recorder is
        # an ad-hoc rtl_fm subprocess the satellites page launches, and no
        # `satellites-listen` unit exists for systemd to start. This loop used
        # service_units(), so a console reroute onto satellites handed the token
        # to `systemctl start` and got a silent success. startable_units() is
        # the list a generic starter may act on, and it exists for this.
        _write(self.dir, '{"version":1,'
               '"devices":[{"id":"sdr-1","kind":"rtl-sdr","serial":"1"}],'
               '"assignments":{}}')
        inv = hardware.load(self.dir)
        started = []
        hardware.reroute(self.dir, inv, "satellites", "sdr-1", start_fn=started.append)
        self.assertEqual(inv.assignments["satellites"], "sdr-1")     # assignment IS the state
        self.assertEqual(started, [], "a synthetic unit was handed to the starter")

    def test_can_reroute_refused_when_target_device_locked(self):
        _write(self.dir, '{"version":1,"devices":['
               '{"id":"sdr-1","kind":"rtl-sdr","serial":"1","locked":true}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        ok, reason = hardware.can_reroute(inv, "aprs", "sdr-1")
        self.assertFalse(ok)
        self.assertEqual(reason, "target-locked")

    def test_can_reroute_refused_when_source_device_locked(self):
        # Moving a service OFF a locked dongle is also a displacement of that
        # locked dongle's assignment — refuse it.
        _write(self.dir, '{"version":1,"devices":['
               '{"id":"sdr-1","kind":"rtl-sdr","serial":"1","locked":true},'
               '{"id":"sdr-2","kind":"rtl-sdr","serial":"2"}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        ok, reason = hardware.can_reroute(inv, "adsb", "sdr-2")
        self.assertFalse(ok)
        self.assertEqual(reason, "source-locked")

    def test_can_reroute_allowed_when_unlocked(self):
        _write(self.dir, '{"version":1,"devices":['
               '{"id":"sdr-1","kind":"rtl-sdr","serial":"1"}],'
               '"assignments":{}}')
        inv = hardware.load(self.dir)
        self.assertEqual(hardware.can_reroute(inv, "adsb", "sdr-1"), (True, ""))

    def test_reroute_raises_when_locked(self):
        _write(self.dir, '{"version":1,"devices":['
               '{"id":"sdr-1","kind":"rtl-sdr","serial":"1","locked":true}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        with self.assertRaises(ValueError):
            hardware.reroute(self.dir, inv, "aprs", "sdr-1")
        self.assertEqual(inv.assignments.get("adsb"), "sdr-1")       # unchanged


class AutoAssignLockTest(unittest.TestCase):
    """Auto-assignment must never move onto a locked dongle (design 2026-07-28)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_default_assign_skips_locked_device(self):
        # sdr-1 (first) is locked protecting adsb; sdr-2 is free. A new service
        # must land on sdr-2, not pile onto the locked sdr-1.
        _write(self.dir, '{"version":1,"devices":['
               '{"id":"sdr-1","kind":"rtl-sdr","serial":"1","locked":true},'
               '{"id":"sdr-2","kind":"rtl-sdr","serial":"2"}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        hardware.default_assign(self.dir, inv, "satellites", {"rtl-sdr"})
        self.assertEqual(inv.assignments["satellites"], "sdr-2")


class WarningsTest(unittest.TestCase):
    """warnings(inv, is_active) — the shared health contract consumed by BOTH the
    console and the dashboard rail pill. Pure function of (inventory, unit state);
    returns a list of {kind, device, service, severity, message}. Empty == green."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_clean_inventory_has_no_warnings(self):
        _write(self.dir, '{"version":1,"devices":['
               '{"id":"sdr-1","kind":"rtl-sdr","serial":"1"}],'
               '"assignments":{"adsb":"sdr-1"}}')
        inv = hardware.load(self.dir)
        self.assertEqual(hardware.warnings(inv, is_active=lambda u: False), [])

    def test_warnings_flags_assignment_to_missing_device(self):
        # Dongle unplugged: adsb is assigned to a device no longer in inventory.
        _write(self.dir, '{"version":1,"devices":[],'
               '"assignments":{"adsb":"ghost"}}')
        inv = hardware.load(self.dir)
        w = hardware.warnings(inv, is_active=lambda u: False)
        self.assertTrue(any(x["kind"] == "device-missing" and x["service"] == "adsb"
                            and x["severity"] == "crit" for x in w))
