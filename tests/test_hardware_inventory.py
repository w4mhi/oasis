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
