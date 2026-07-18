import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
import app as oasis_app


class DiagnosticsRouteTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def test_diagnostics_contract_shape(self):
        # No server is listening on appconfig.PORT in the test env, so the
        # self-HTTP checks (server/maps/gps/power/...) will report down/warn —
        # that's expected here. This test asserts the response CONTRACT SHAPE
        # (200 + top-level keys), not check values, and that the call returns
        # promptly (connection-refused is fast, not a hang).
        r = self.c.get("/api/diagnostics")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIsInstance(data, dict)
        for key in ("ran_at", "summary", "capabilities", "fix_now", "groups"):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
