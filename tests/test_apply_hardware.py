import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "scripts"))
import apply_hardware
from common import hardware as HW

class ApplyAllTest(unittest.TestCase):
    def _inv(self, devices=None, assignments=None):
        return HW.Inventory(devices=devices or {}, assignments=assignments or {})

    def test_calls_hook_with_assigned_device(self):
        calls = []
        inv = self._inv(
            devices={"sdr-adsb": {"id": "sdr-adsb", "kind": "rtl-sdr", "serial": "1090"}},
            assignments={"adsb": "sdr-adsb"})
        reloads = []
        apply_hardware.apply_all(
            "/repo",
            hooks={"adsb": lambda root, dev: calls.append((root, dev))},
            load_fn=lambda root: inv,
            reload_fn=lambda: reloads.append(1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/repo")
        self.assertEqual(calls[0][1]["serial"], "1090")  # the resolved device dict
        self.assertEqual(reloads, [1])                    # exactly one daemon-reload

    def test_unassigned_service_hook_called_with_none(self):
        calls = []
        inv = self._inv()  # nothing assigned
        apply_hardware.apply_all(
            "/repo",
            hooks={"adsb": lambda root, dev: calls.append(dev)},
            load_fn=lambda root: inv,
            reload_fn=lambda: None)
        self.assertEqual(calls, [None])  # cleared, not skipped

    def test_assigned_to_missing_device_passes_none(self):
        # assignment points at a device not in inv.devices (detached) -> the hook
        # gets None so it clears the pinning rather than binding a ghost.
        calls = []
        inv = self._inv(assignments={"adsb": "ghost"})
        apply_hardware.apply_all(
            "/repo",
            hooks={"adsb": lambda root, dev: calls.append(dev)},
            load_fn=lambda root: inv,
            reload_fn=lambda: None)
        self.assertEqual(calls, [None])

    def test_returns_applied_service_names(self):
        inv = self._inv(
            devices={"a": {"id": "a", "kind": "rtl-sdr", "serial": "1"}},
            assignments={"adsb": "a"})
        applied = apply_hardware.apply_all(
            "/repo", hooks={"adsb": lambda root, dev: None},
            load_fn=lambda root: inv, reload_fn=lambda: None)
        self.assertEqual(applied, ["adsb"])

    def test_default_registry_has_adsb(self):
        # The real registry wires the ADS-B hook (import smoke — proves the
        # scripts module can import the service hook without a cycle).
        self.assertIn("adsb", apply_hardware.DEFAULT_HOOKS)

    def test_default_registry_has_aprs(self):
        self.assertIn("aprs", apply_hardware.DEFAULT_HOOKS)

if __name__ == "__main__":
    unittest.main()
