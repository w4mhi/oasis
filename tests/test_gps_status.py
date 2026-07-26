import os, sys, unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
from routes import system as system_routes


class GpsInterfaceTest(unittest.TestCase):
    def test_usb_ttyacm(self):
        self.assertEqual(system_routes._gps_interface("/dev/ttyACM0"), "usb")

    def test_usb_ttyusb(self):
        self.assertEqual(system_routes._gps_interface("/dev/ttyUSB0"), "usb")

    def test_uart_ttys(self):
        self.assertEqual(system_routes._gps_interface("/dev/ttyS0"), "uart")

    def test_uart_serial0(self):
        self.assertEqual(system_routes._gps_interface("/dev/serial0"), "uart")

    def test_uart_ttyama(self):
        self.assertEqual(system_routes._gps_interface("/dev/ttyAMA0"), "uart")

    def test_none_for_empty(self):
        self.assertIsNone(system_routes._gps_interface(None))
        self.assertIsNone(system_routes._gps_interface(""))

    def test_none_for_unrecognized(self):
        self.assertIsNone(system_routes._gps_interface("/dev/ttyXYZ0"))


class GpsPresenceStatusTest(unittest.TestCase):
    def test_no_configured_no_candidates_present(self):
        with mock.patch.object(system_routes.gpsd_chrony, "configured_device", return_value=None), \
             mock.patch.object(system_routes.os.path, "exists", return_value=False):
            status = system_routes._gps_presence_status()
        self.assertEqual(status, {"device": None, "interface": None, "otherDetected": []})

    def test_configured_usb_no_others_present(self):
        def fake_exists(p):
            return p == "/dev/ttyACM0"
        with mock.patch.object(system_routes.gpsd_chrony, "configured_device", return_value="/dev/ttyACM0"), \
             mock.patch.object(system_routes.os.path, "exists", side_effect=fake_exists):
            status = system_routes._gps_presence_status()
        self.assertEqual(status["device"], "/dev/ttyACM0")
        self.assertEqual(status["interface"], "usb")
        self.assertEqual(status["otherDetected"], [])

    def test_configured_uart_plus_other_usb_detected(self):
        def fake_exists(p):
            return p in ("/dev/serial0", "/dev/ttyACM0")
        with mock.patch.object(system_routes.gpsd_chrony, "configured_device", return_value="/dev/serial0"), \
             mock.patch.object(system_routes.os.path, "exists", side_effect=fake_exists):
            status = system_routes._gps_presence_status()
        self.assertEqual(status["device"], "/dev/serial0")
        self.assertEqual(status["interface"], "uart")
        self.assertEqual(status["otherDetected"], [{"path": "/dev/ttyACM0", "interface": "usb"}])

    def test_configured_device_not_a_recognized_path_degrades_gracefully(self):
        with mock.patch.object(system_routes.gpsd_chrony, "configured_device", return_value="/dev/weird0"), \
             mock.patch.object(system_routes.os.path, "exists", return_value=False):
            status = system_routes._gps_presence_status()
        self.assertEqual(status["device"], "/dev/weird0")
        self.assertIsNone(status["interface"])
        self.assertEqual(status["otherDetected"], [])

    def test_multiple_other_candidates_sorted(self):
        def fake_exists(p):
            return p in ("/dev/ttyUSB0", "/dev/ttyS0", "/dev/ttyAMA0")
        with mock.patch.object(system_routes.gpsd_chrony, "configured_device", return_value=None), \
             mock.patch.object(system_routes.os.path, "exists", side_effect=fake_exists):
            status = system_routes._gps_presence_status()
        paths = [d["path"] for d in status["otherDetected"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(set(paths), {"/dev/ttyUSB0", "/dev/ttyS0", "/dev/ttyAMA0"})

    def test_configured_device_excluded_even_if_it_also_matches_candidates(self):
        # The configured device itself must never appear in otherDetected,
        # even though it's one of the candidate paths.
        with mock.patch.object(system_routes.gpsd_chrony, "configured_device", return_value="/dev/ttyS0"), \
             mock.patch.object(system_routes.os.path, "exists", return_value=True):
            status = system_routes._gps_presence_status()
        paths = [d["path"] for d in status["otherDetected"]]
        self.assertNotIn("/dev/ttyS0", paths)


class GpsInfoIntegrationTest(unittest.TestCase):
    def test_gps_info_returns_none_when_socket_unreachable(self):
        with mock.patch.object(system_routes.socket, "create_connection", side_effect=OSError):
            self.assertIsNone(system_routes._gps_info())

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
        with mock.patch.object(system_routes.socket, "create_connection", return_value=FakeSocket()), \
             mock.patch.object(system_routes.gpsd_chrony, "configured_device", return_value="/dev/ttyACM0"), \
             mock.patch.object(system_routes.os.path, "exists", return_value=False):
            info = system_routes._gps_info()
        self.assertEqual(info["mode"], 0)
        self.assertEqual(info["device"], "/dev/ttyACM0")
        self.assertEqual(info["interface"], "usb")
        self.assertEqual(info["otherDetected"], [])


class _ScriptedSocket:
    """Replays a queue of byte chunks over recv(), then EOF — one chunk per
    read, so a message that lands in a *later* chunk is only seen if the read
    loop keeps going."""
    def __init__(self, chunks):
        self._chunks = list(chunks)
    def sendall(self, data):
        pass
    def settimeout(self, t):
        pass
    def recv(self, n):
        return self._chunks.pop(0) if self._chunks else b""
    def close(self):
        pass


class GpsInfoSkyParsingTest(unittest.TestCase):
    """Regression: gpsd (u-blox on /dev/ttyACM0) emits several SKY messages per
    cycle, most of them DOP-only (no nSat/uSat/satellites). The read loop must
    not treat a DOP-only SKY as the satellite snapshot — doing so captured 0/0
    and exited before the real satellite-bearing SKY arrived."""

    def _run(self, chunks):
        with mock.patch.object(system_routes.socket, "create_connection",
                               return_value=_ScriptedSocket(chunks)), \
             mock.patch.object(system_routes.gpsd_chrony, "configured_device", return_value=None), \
             mock.patch.object(system_routes.os.path, "exists", return_value=False):
            return system_routes._gps_info()

    def test_dop_only_sky_then_full_sky_reports_real_counts(self):
        # First read: a DOP-only SKY + the TPV (fix mode). Second read: the real
        # satellite-bearing SKY. The buggy loop exited after read 1 with seen=0.
        dop_only = b'{"class":"SKY","device":"/dev/ttyACM0","hdop":46.48}\n'
        tpv = b'{"class":"TPV","mode":3,"lat":34.0,"lon":-84.0,"altMSL":300.0}\n'
        full_sky = (b'{"class":"SKY","device":"/dev/ttyACM0","hdop":46.85,'
                    b'"nSat":17,"uSat":3,"satellites":[{"PRN":15,"used":true},'
                    b'{"PRN":18,"used":true},{"PRN":23,"used":true},'
                    b'{"PRN":5,"used":false}]}\n')
        info = self._run([dop_only + tpv, full_sky])
        self.assertEqual(info["seen"], 17)
        self.assertEqual(info["used"], 3)
        self.assertEqual(info["mode"], 3)
        self.assertAlmostEqual(info["hdop"], 46.85)

    def test_fallback_counts_from_satellites_array_when_no_nsat(self):
        # Older gpsd without nSat/uSat: counts derive from the satellites list.
        tpv = b'{"class":"TPV","mode":3}\n'
        dop_only = b'{"class":"SKY","hdop":9.9}\n'
        sky = (b'{"class":"SKY","satellites":[{"PRN":1,"used":true},'
               b'{"PRN":2,"used":true},{"PRN":3,"used":false}]}\n')
        info = self._run([dop_only + tpv, sky])
        self.assertEqual(info["seen"], 3)
        self.assertEqual(info["used"], 2)


if __name__ == "__main__":
    unittest.main()
