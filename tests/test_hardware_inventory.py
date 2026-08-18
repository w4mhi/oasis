import os, sys, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import hardware

class InventoryLoadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_absent_file_returns_empty_inventory(self):
        inv = hardware.load(self.dir)
        self.assertEqual(inv.devices, {})
        self.assertEqual(inv.assignments, {})

    def test_load_valid_inventory(self):
        os.makedirs(os.path.join(self.dir, "configuration"))
        with open(os.path.join(self.dir, "configuration", "hardware.json"), "w") as f:
            f.write('''{
                "version": 1,
                "devices": [
                    {"id": "sdr-adsb", "kind": "rtl-sdr", "serial": "1090", "label": "ADS-B"}
                ],
                "assignments": {"adsb": "sdr-adsb"}
            }''')
        inv = hardware.load(self.dir)
        self.assertIn("sdr-adsb", inv.devices)
        self.assertEqual(inv.assignments["adsb"], "sdr-adsb")
        self.assertEqual(inv.errors, [])

    def test_clear_assignments_keeps_inventory(self):
        # Factory-reset finalizer: assignments wiped, detected devices kept.
        os.makedirs(os.path.join(self.dir, "configuration"))
        with open(os.path.join(self.dir, "configuration", "hardware.json"), "w") as f:
            f.write('''{"version": 1,
                "devices": [{"id": "sdr-1", "kind": "rtl-sdr", "serial": "1"}],
                "assignments": {"adsb": "sdr-1", "satellites": "sdr-1"}}''')
        self.assertTrue(hardware.clear_assignments(self.dir))    # something cleared
        after = hardware.load(self.dir)
        self.assertEqual(after.assignments, {})                  # wiring gone
        self.assertIn("sdr-1", after.devices)                    # inventory kept
        self.assertFalse(hardware.clear_assignments(self.dir))   # idempotent no-op

    def test_duplicate_device_id_skipped_with_error(self):
        os.makedirs(os.path.join(self.dir, "configuration"))
        with open(os.path.join(self.dir, "configuration", "hardware.json"), "w") as f:
            f.write('''{"devices": [
                {"id": "a", "kind": "rtl-sdr", "serial": "1"},
                {"id": "a", "kind": "rtl-sdr", "serial": "2"}
            ], "assignments": {}}''')
        inv = hardware.load(self.dir)
        self.assertEqual(len(inv.devices), 1)
        self.assertTrue(any("duplicate" in e for e in inv.errors))

    def test_unknown_kind_skipped_with_error(self):
        os.makedirs(os.path.join(self.dir, "configuration"))
        with open(os.path.join(self.dir, "configuration", "hardware.json"), "w") as f:
            f.write('{"devices": [{"id": "a", "kind": "bogus"}], "assignments": {}}')
        inv = hardware.load(self.dir)
        self.assertEqual(inv.devices, {})
        self.assertTrue(any("unknown kind" in e for e in inv.errors))

    def test_assignment_to_missing_device_preserved_with_error(self):
        # The assignment is KEPT (not dropped) so a later can_start() (built in
        # the next task) can distinguish "unassigned" from "assigned but
        # device missing".
        os.makedirs(os.path.join(self.dir, "configuration"))
        with open(os.path.join(self.dir, "configuration", "hardware.json"), "w") as f:
            f.write('{"devices": [], "assignments": {"adsb": "ghost-device"}}')
        inv = hardware.load(self.dir)
        self.assertEqual(inv.assignments["adsb"], "ghost-device")
        self.assertTrue(any("unknown device" in e for e in inv.errors))

    def test_assignment_wrong_kind_for_service_dropped_with_error(self):
        os.makedirs(os.path.join(self.dir, "configuration"))
        with open(os.path.join(self.dir, "configuration", "hardware.json"), "w") as f:
            f.write('''{"devices": [{"id": "radio-hf", "kind": "dra-pi", "ptt": "gpio12"}],
                        "assignments": {"adsb": "radio-hf"}}''')
        inv = hardware.load(self.dir)
        self.assertNotIn("adsb", inv.assignments)
        self.assertTrue(any("not in" in e for e in inv.errors))

    def test_assignment_to_an_unmanaged_service_dropped_with_error(self):
        # The upgrade path for retiring a service (openwebrx, 3.92.0). Its key
        # survives in every field station's hardware.json, and nothing else
        # would remove it: with no DEVICE_KIND_FOR_SERVICE entry left, the kind
        # check above sees allowed_kinds=None and preserves the row. Kept, it
        # would make the dongle read as claimed by something no surface can
        # show, start or release.
        os.makedirs(os.path.join(self.dir, "configuration"))
        with open(os.path.join(self.dir, "configuration", "hardware.json"), "w") as f:
            f.write('''{"devices": [{"id": "sdr-1", "kind": "rtl-sdr", "serial": "1"}],
                        "assignments": {"adsb": "sdr-1", "openwebrx": "sdr-1"}}''')
        inv = hardware.load(self.dir)
        self.assertEqual(inv.assignments, {"adsb": "sdr-1"})
        self.assertEqual(hardware.assignees(inv, "sdr-1"), ["adsb"])
        self.assertTrue(any("unknown service" in e for e in inv.errors))

    def test_dropping_an_unmanaged_service_survives_a_save(self):
        # load() drops it in memory; the next save is what clears the file.
        os.makedirs(os.path.join(self.dir, "configuration"))
        path = os.path.join(self.dir, "configuration", "hardware.json")
        with open(path, "w") as f:
            f.write('''{"devices": [{"id": "sdr-1", "kind": "rtl-sdr", "serial": "1"}],
                        "assignments": {"openwebrx": "sdr-1"}}''')
        hardware.save(self.dir, hardware.load(self.dir))
        with open(path) as f:
            self.assertNotIn("openwebrx", f.read())

    def test_save_then_load_round_trips(self):
        inv = hardware.Inventory(
            devices={"sdr-adsb": {"id": "sdr-adsb", "kind": "rtl-sdr", "serial": "1090"}},
            assignments={"adsb": "sdr-adsb"})
        hardware.save(self.dir, inv)
        reloaded = hardware.load(self.dir)
        self.assertEqual(reloaded.devices, inv.devices)
        self.assertEqual(reloaded.assignments, inv.assignments)

if __name__ == "__main__":
    unittest.main()
