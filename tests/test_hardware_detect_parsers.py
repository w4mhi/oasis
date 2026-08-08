import os, sys, tempfile, unittest
from unittest import mock
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


LSUSB_TWO_DEVICES = """\
Bus 001 Device 004: ID 10c4:ea60 Silicon Labs CP210x UART Bridge
Bus 001 Device 002: ID 0403:6001 Future Technology Devices International, Ltd FT232 Serial (UART) IC
"""

class ParseLsusbTest(unittest.TestCase):
    def test_two_devices(self):
        devices = HD.parse_lsusb(LSUSB_TWO_DEVICES)
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0], {
            "bus": "001", "device": "004", "vendor_id": "10c4", "product_id": "ea60",
            "description": "Silicon Labs CP210x UART Bridge",
        })
        self.assertEqual(devices[1]["vendor_id"], "0403")

    def test_empty_input(self):
        self.assertEqual(HD.parse_lsusb(""), [])


class ListTtySerialDevicesTest(unittest.TestCase):
    def test_lists_usb_and_onboard_kinds(self):
        d = tempfile.mkdtemp()
        for name in ["ttyUSB0", "ttyACM0", "ttyAMA0", "serial0", "ttyS0", "not-a-tty"]:
            open(os.path.join(d, name), "w").close()
        result = HD.list_tty_serial_devices(d)
        by_label = {r["label"]: r["kind"] for r in result}
        self.assertEqual(by_label, {
            "ttyUSB0": "usb", "ttyACM0": "usb",
            "ttyAMA0": "onboard", "serial0": "onboard", "ttyS0": "onboard",
        })
        self.assertNotIn("not-a-tty", by_label)

    def test_missing_directory_returns_empty(self):
        self.assertEqual(HD.list_tty_serial_devices("/tmp/does-not-exist-xyz"), [])

class DetectDigirigTest(unittest.TestCase):
    _CP ="/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_3e54e82ac39eec119daf9579a29c855c-if00-port0"

    def test_lone_digirig_returns_ptt_and_chip_serial(self):
        with mock.patch.object(HD.sys, "platform", "linux"), \
             mock.patch.object(HD.glob, "glob", side_effect=lambda p: [self._CP]):
            got = HD.detect_digirig()
        self.assertEqual(got, {"ptt": self._CP, "serial": "3e54e82ac39eec119daf9579a29c855c"})

    def test_ambiguous_two_cp210x_returns_none(self):
        with mock.patch.object(HD.sys, "platform", "linux"), \
             mock.patch.object(HD.glob, "glob", side_effect=lambda p: [self._CP, self._CP + "2"]):
            self.assertIsNone(HD.detect_digirig())

    def test_none_present_returns_none(self):
        with mock.patch.object(HD.sys, "platform", "linux"), \
             mock.patch.object(HD.glob, "glob", side_effect=lambda p: []):
            self.assertIsNone(HD.detect_digirig())

    def test_non_linux_returns_none(self):
        with mock.patch.object(HD.sys, "platform", "darwin"):
            self.assertIsNone(HD.detect_digirig())

class DetectDraPiTest(unittest.TestCase):
    _APLAY_DRA = "card 0: audioinjectorpi [AudioInjector Pi], device 0: WM8731 HiFi wm8731-hifi-0 []\n"
    _APLAY_USB = "card 0: Device [USB Audio Device], device 0: USB Audio [USB Audio]\n"

    def test_true_when_audioinjector_card_present(self):
        with mock.patch.object(HD.sys, "platform", "linux"), \
             mock.patch.object(HD, "_run_text", return_value=self._APLAY_DRA):
            self.assertTrue(HD.detect_dra_pi())

    def test_false_when_only_a_usb_card(self):
        with mock.patch.object(HD.sys, "platform", "linux"), \
             mock.patch.object(HD, "_run_text", return_value=self._APLAY_USB):
            self.assertFalse(HD.detect_dra_pi())

    def test_false_on_non_linux(self):
        with mock.patch.object(HD.sys, "platform", "darwin"):
            self.assertFalse(HD.detect_dra_pi())

if __name__ == "__main__":
    unittest.main()


class DrawsPresentTest(unittest.TestCase):
    def test_true_for_the_draws_card(self):
        self.assertTrue(HD.draws_present(
            [{"id": "draws", "description": "simple-card"}]))

    def test_false_without_it(self):
        self.assertFalse(HD.draws_present(
            [{"id": "vc4hdmi0", "description": "vc4-hdmi-0"},
             {"id": "Headphones", "description": "bcm2835 Headphones"}]))

    def test_dra_pi_card_is_not_draws(self):
        """The two HATs are different features and must never cross-detect."""
        self.assertFalse(HD.draws_present(
            [{"id": "audioinjectorpi", "description": "AudioInjector Pi"}]))

    def test_draws_card_is_not_dra_pi(self):
        self.assertFalse(HD.dra_pi_present(
            [{"id": "draws", "description": "simple-card"}]))

    def test_unrelated_card_merely_mentioning_draws_is_ignored(self):
        self.assertFalse(HD.draws_present(
            [{"id": "USB", "description": "a draws-compatible thing"}]))
