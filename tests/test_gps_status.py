import os, sys, unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app


class GpsInterfaceTest(unittest.TestCase):
    def test_usb_ttyacm(self):
        self.assertEqual(oasis_app._gps_interface("/dev/ttyACM0"), "usb")

    def test_usb_ttyusb(self):
        self.assertEqual(oasis_app._gps_interface("/dev/ttyUSB0"), "usb")

    def test_uart_ttys(self):
        self.assertEqual(oasis_app._gps_interface("/dev/ttyS0"), "uart")

    def test_uart_serial0(self):
        self.assertEqual(oasis_app._gps_interface("/dev/serial0"), "uart")

    def test_uart_ttyama(self):
        self.assertEqual(oasis_app._gps_interface("/dev/ttyAMA0"), "uart")

    def test_none_for_empty(self):
        self.assertIsNone(oasis_app._gps_interface(None))
        self.assertIsNone(oasis_app._gps_interface(""))

    def test_none_for_unrecognized(self):
        self.assertIsNone(oasis_app._gps_interface("/dev/ttyXYZ0"))


class GpsPresenceStatusTest(unittest.TestCase):
    def test_no_configured_no_candidates_present(self):
        with mock.patch.object(oasis_app.gpsd_chrony, "configured_device", return_value=None), \
             mock.patch.object(oasis_app.os.path, "exists", return_value=False):
            status = oasis_app._gps_presence_status()
        self.assertEqual(status, {"device": None, "interface": None, "otherDetected": []})

    def test_configured_usb_no_others_present(self):
        def fake_exists(p):
            return p == "/dev/ttyACM0"
        with mock.patch.object(oasis_app.gpsd_chrony, "configured_device", return_value="/dev/ttyACM0"), \
             mock.patch.object(oasis_app.os.path, "exists", side_effect=fake_exists):
            status = oasis_app._gps_presence_status()
        self.assertEqual(status["device"], "/dev/ttyACM0")
        self.assertEqual(status["interface"], "usb")
        self.assertEqual(status["otherDetected"], [])

    def test_configured_uart_plus_other_usb_detected(self):
        def fake_exists(p):
            return p in ("/dev/serial0", "/dev/ttyACM0")
        with mock.patch.object(oasis_app.gpsd_chrony, "configured_device", return_value="/dev/serial0"), \
             mock.patch.object(oasis_app.os.path, "exists", side_effect=fake_exists):
            status = oasis_app._gps_presence_status()
        self.assertEqual(status["device"], "/dev/serial0")
        self.assertEqual(status["interface"], "uart")
        self.assertEqual(status["otherDetected"], [{"path": "/dev/ttyACM0", "interface": "usb"}])

    def test_configured_device_not_a_recognized_path_degrades_gracefully(self):
        with mock.patch.object(oasis_app.gpsd_chrony, "configured_device", return_value="/dev/weird0"), \
             mock.patch.object(oasis_app.os.path, "exists", return_value=False):
            status = oasis_app._gps_presence_status()
        self.assertEqual(status["device"], "/dev/weird0")
        self.assertIsNone(status["interface"])
        self.assertEqual(status["otherDetected"], [])

    def test_multiple_other_candidates_sorted(self):
        def fake_exists(p):
            return p in ("/dev/ttyUSB0", "/dev/ttyS0", "/dev/ttyAMA0")
        with mock.patch.object(oasis_app.gpsd_chrony, "configured_device", return_value=None), \
             mock.patch.object(oasis_app.os.path, "exists", side_effect=fake_exists):
            status = oasis_app._gps_presence_status()
        paths = [d["path"] for d in status["otherDetected"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(set(paths), {"/dev/ttyUSB0", "/dev/ttyS0", "/dev/ttyAMA0"})

    def test_configured_device_excluded_even_if_it_also_matches_candidates(self):
        # The configured device itself must never appear in otherDetected,
        # even though it's one of the candidate paths.
        with mock.patch.object(oasis_app.gpsd_chrony, "configured_device", return_value="/dev/ttyS0"), \
             mock.patch.object(oasis_app.os.path, "exists", return_value=True):
            status = oasis_app._gps_presence_status()
        paths = [d["path"] for d in status["otherDetected"]]
        self.assertNotIn("/dev/ttyS0", paths)


class GpsInfoIntegrationTest(unittest.TestCase):
    def test_gps_info_returns_none_when_socket_unreachable(self):
        with mock.patch.object(oasis_app.socket, "create_connection", side_effect=OSError):
            self.assertIsNone(oasis_app._gps_info())

    def test_gps_info_merges_presence_status_when_reachable(self):
        class FakeSocket:
            def sendall(self, data):
                pass
            def settimeout(self, t):
                pass
            def recv(self, n):
                return b""  # no TPV/SKY data — the read loop breaks immediately
            def close(self):
                pass
        with mock.patch.object(oasis_app.socket, "create_connection", return_value=FakeSocket()), \
             mock.patch.object(oasis_app.gpsd_chrony, "configured_device", return_value="/dev/ttyACM0"), \
             mock.patch.object(oasis_app.os.path, "exists", return_value=False):
            info = oasis_app._gps_info()
        self.assertEqual(info["mode"], 0)
        self.assertEqual(info["device"], "/dev/ttyACM0")
        self.assertEqual(info["interface"], "usb")
        self.assertEqual(info["otherDetected"], [])


if __name__ == "__main__":
    unittest.main()
