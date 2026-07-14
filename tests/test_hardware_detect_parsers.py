import os, sys, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import hardware_detect as HD

RTL_TEST_TWO_DEVICES = """\
Found 2 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00000001
  1:  Realtek, RTL2838UHIDIR, SN: 1090

Using device 0: Generic RTL2832U OEM
"""

RTL_TEST_NONE = """\
No supported devices found.
"""

APLAY_TWO_CARDS = """\
**** List of PLAYBACK Hardware Devices ****
card 0: audioinjectorpi [AudioInjector Pi], device 0: WM8731 HiFi wm8731-hifi-0 []
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""

class ParseRtlTestTest(unittest.TestCase):
    def test_two_devices(self):
        devs = HD.parse_rtl_test_devices(RTL_TEST_TWO_DEVICES)
        self.assertEqual(devs, [
            {"index": 0, "serial": "00000001"},
            {"index": 1, "serial": "1090"},
        ])

    def test_no_devices(self):
        self.assertEqual(HD.parse_rtl_test_devices(RTL_TEST_NONE), [])

    def test_empty_input(self):
        self.assertEqual(HD.parse_rtl_test_devices(""), [])

class ParseAplayTest(unittest.TestCase):
    def test_two_cards(self):
        cards = HD.parse_aplay_cards(APLAY_TWO_CARDS)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["card"], 0)
        self.assertEqual(cards[0]["id"], "audioinjectorpi")
        self.assertIn("AudioInjector Pi", cards[0]["description"])
        self.assertEqual(cards[1]["card"], 1)

    def test_empty_input(self):
        self.assertEqual(HD.parse_aplay_cards(""), [])

class ListSerialByIdTest(unittest.TestCase):
    def test_lists_files_sorted(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "usb-Silicon_Labs_CP2102-if00-port0"), "w").close()
        open(os.path.join(d, "usb-Another_Device-if00"), "w").close()
        result = HD.list_serial_by_id(d)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["path"], os.path.join(d, "usb-Another_Device-if00"))
        self.assertTrue(all("label" in r for r in result))

    def test_missing_directory_returns_empty(self):
        self.assertEqual(HD.list_serial_by_id("/tmp/does-not-exist-xyz"), [])

if __name__ == "__main__":
    unittest.main()
