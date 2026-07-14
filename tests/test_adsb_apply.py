import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from services.adsb.common import adsb

class Dump1090DeviceArgsTest(unittest.TestCase):
    def test_none_device_yields_no_args(self):
        self.assertEqual(adsb.dump1090_device_args(None), [])

    def test_device_without_serial_yields_no_args(self):
        self.assertEqual(adsb.dump1090_device_args({"id": "a", "kind": "rtl-sdr"}), [])

    def test_device_with_serial_yields_device_flag(self):
        self.assertEqual(
            adsb.dump1090_device_args({"id": "a", "kind": "rtl-sdr", "serial": "1090"}),
            ["--device", "1090"])

    def test_apply_is_noop_on_non_linux(self):
        # On this macOS dev box apply() must return without attempting any
        # root/systemd side effects (mirrors the Linux guard used across OASIS
        # install code). It must not raise.
        adsb.apply("/tmp/does-not-matter", {"id": "a", "kind": "rtl-sdr", "serial": "1090"})

if __name__ == "__main__":
    unittest.main()
