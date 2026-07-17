import os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app
from common import hardware_detect as HD

class BurnSerialRouteTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_requires_oasis_header(self):
        r = self.c.post("/api/hardware/burn-serial", json={"serial": "1090"})
        self.assertEqual(r.status_code, 403)

    def test_rejects_invalid_serial_format(self):
        r = self.c.post("/api/hardware/burn-serial", json={"serial": "1090; rm -rf /"},
                        headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 400)

    def test_rejects_trailing_newline_serial(self):
        # Same \Z-vs-$ concern as the wrapper script's own validator — the
        # route's own check must reject this too, independently.
        r = self.c.post("/api/hardware/burn-serial", json={"serial": "1090\n"},
                        headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 400)

    def test_refuses_when_guard_blocks(self):
        with mock.patch.object(HD, "scan", return_value={"rtl_sdr": [], "alsa": [], "serial": []}):
            r = self.c.post("/api/hardware/burn-serial", json={"serial": "1090"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 409)

    def test_runs_wrapper_when_guard_clears(self):
        one = {"rtl_sdr": [{"index": 0, "serial": "00000001"}], "alsa": [], "serial": []}
        with mock.patch.object(HD, "scan", return_value=one), \
             mock.patch.object(oasis_app, "subprocess") as mocked_subprocess:
            mocked_subprocess.run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            r = self.c.post("/api/hardware/burn-serial", json={"serial": "1090"},
                            headers={"X-OASIS-Request": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocked_subprocess.run.called)
        call_args = mocked_subprocess.run.call_args[0][0]
        self.assertIn("burn_dongle_serial.py", " ".join(call_args))
        self.assertIn("1090", call_args)

if __name__ == "__main__":
    unittest.main()
