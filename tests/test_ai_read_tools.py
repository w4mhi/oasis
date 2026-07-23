import json
import unittest
from unittest import mock

from ai.server.tools import http, read_tools


class TestOasisGet(unittest.TestCase):
    def test_returns_body_text_on_success(self):
        resp = mock.Mock(status_code=200, text='{"ok": true, "stations": []}')
        resp.raise_for_status = mock.Mock()
        with mock.patch("ai.server.tools.http.httpx.get", return_value=resp) as g:
            out = http.oasis_get("/api/aprs/stations", base="http://x", timeout=5)
        self.assertEqual(json.loads(out)["ok"], True)
        g.assert_called_once()

    def test_wraps_errors_as_json_never_raises(self):
        with mock.patch("ai.server.tools.http.httpx.get",
                        side_effect=RuntimeError("connection refused")):
            out = http.oasis_get("/api/aprs/stations", base="http://x", timeout=5)
        parsed = json.loads(out)
        self.assertFalse(parsed["ok"])
        self.assertIn("connection refused", parsed["error"])


class TestRegistry(unittest.TestCase):
    def test_registry_has_core_read_tools(self):
        names = {t.name for t in read_tools.READ_TOOLS}
        for expected in ("aprs_stations", "adsb_aircraft", "adsb_alerts",
                         "satellite_passes", "system_health", "fcc_lookup"):
            self.assertIn(expected, names)

    def test_fcc_lookup_declares_callsign_param(self):
        fcc = next(t for t in read_tools.READ_TOOLS if t.name == "fcc_lookup")
        self.assertIn("callsign", fcc.params)

    def test_register_adds_every_tool_to_fastmcp(self):
        added = []
        fake_mcp = mock.Mock()
        fake_mcp.add_tool = lambda fn, name=None, description=None: added.append(name)
        from ai import config
        read_tools.register(fake_mcp, config.load("/nonexistent"))
        self.assertEqual(sorted(added), sorted(t.name for t in read_tools.READ_TOOLS))
