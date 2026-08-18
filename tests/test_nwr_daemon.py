import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import daemon  # noqa: E402


class PortTest(unittest.TestCase):
    def test_port_is_8089_not_8087(self):
        # 8087 is claimed by oasis-ai (llama-server) on branch ai_mcp, and 8088
        # is rtl_433's soft claim. See specs/PORT-MAP.md.
        self.assertEqual(daemon.API_PORT, 8089)


class ChooseChannelTest(unittest.TestCase):
    def test_a_pinned_channel_skips_the_scan(self):
        called = []
        hz, result = daemon.choose_channel(
            _ROOT, {"pinned_channel": 162400000}, "0001",
            scan_fn=lambda **kw: called.append(1) or {})
        self.assertEqual(hz, 162400000)
        self.assertIsNone(result)
        self.assertEqual(called, [], "a pinned channel must not sweep the band")

    def test_auto_scan_picks_the_strongest(self):
        def fake_scan(**kw):
            return {"ok": True, "powers": {162400000: -40.0, 162550000: -12.0},
                    "best_hz": 162550000, "best_dbm": -12.0, "error": None}
        hz, result = daemon.choose_channel(_ROOT, {}, "0001", scan_fn=fake_scan)
        self.assertEqual(hz, 162550000)
        self.assertEqual(result["best_dbm"], -12.0)

    def test_a_weak_best_still_starts(self):
        # Refusing to start would leave nothing running, and silence that means
        # "no transmitter" would look identical to silence that means "broken".
        def fake_scan(**kw):
            return {"ok": True, "powers": {162550000: -60.0},
                    "best_hz": 162550000, "best_dbm": -60.0, "error": None}
        hz, result = daemon.choose_channel(_ROOT, {}, "0001", scan_fn=fake_scan)
        self.assertEqual(hz, 162550000)
        self.assertTrue(result["weak"])

    def test_a_failed_scan_falls_back_to_the_configured_channel(self):
        def fake_scan(**kw):
            return {"ok": False, "error": "rtl_power is not installed",
                    "code": "NWR_NO_RTL_POWER", "powers": {},
                    "best_hz": None, "best_dbm": None}
        hz, result = daemon.choose_channel(
            _ROOT, {"channel_hz": 162475000}, "0001", scan_fn=fake_scan)
        self.assertEqual(hz, 162475000)
        self.assertFalse(result["ok"])


class RescanBackoffTest(unittest.TestCase):
    def test_starts_at_fifteen_minutes(self):
        self.assertEqual(daemon.rescan_delay(1), 15 * 60)

    def test_doubles(self):
        self.assertEqual(daemon.rescan_delay(2), 30 * 60)
        self.assertEqual(daemon.rescan_delay(3), 60 * 60)

    def test_caps_at_six_hours(self):
        self.assertEqual(daemon.rescan_delay(99), 6 * 3600)

    def test_a_healthy_scan_resets_it(self):
        self.assertEqual(daemon.rescan_delay(0), 0)


class StatusTest(unittest.TestCase):
    def test_reports_the_keys_the_card_and_flask_read(self):
        s = daemon.status()
        for k in ("phase", "channel_hz", "channel", "scan", "scan_weak",
                  "listening", "alerts_seen", "last_decode", "last_error",
                  "subscribers"):
            self.assertIn(k, s)


if __name__ == "__main__":
    unittest.main()
