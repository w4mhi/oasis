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
