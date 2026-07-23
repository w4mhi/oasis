import json
import unittest
from unittest import mock

from ai import config
from ai.server.tools import actions, http


class TestOasisPost(unittest.TestCase):
    def test_posts_json_and_returns_body(self):
        resp = mock.Mock(status_code=200, text='{"ok": true}')
        resp.raise_for_status = mock.Mock()
        with mock.patch("ai.server.tools.http.httpx.post", return_value=resp) as p:
            out = http.oasis_post("/api/service", {"unit": "graywolf", "action": "stop"},
                                  base="http://x", timeout=5, headers={"X-OASIS-Request": "1"})
        self.assertEqual(json.loads(out)["ok"], True)
        _, kwargs = p.call_args
        self.assertEqual(kwargs["json"], {"unit": "graywolf", "action": "stop"})
        self.assertEqual(kwargs["headers"]["X-OASIS-Request"], "1")

    def test_never_raises(self):
        with mock.patch("ai.server.tools.http.httpx.post", side_effect=RuntimeError("boom")):
            out = http.oasis_post("/api/service", {}, base="http://x", timeout=5)
        self.assertFalse(json.loads(out)["ok"])


class TestActionTools(unittest.TestCase):
    def setUp(self):
        actions._CFG = config.load("/nonexistent")

    def test_service_control_sends_header_and_body(self):
        with mock.patch("ai.server.tools.actions.oasis_post", return_value='{"ok":true}') as p:
            actions.service_control("graywolf", "stop")
        _, kwargs = p.call_args
        self.assertEqual(kwargs["headers"]["X-OASIS-Request"], "1")
        self.assertEqual(p.call_args.args[0], "/api/service")
        self.assertEqual(p.call_args.args[1], {"unit": "graywolf", "action": "stop"})

    def test_aprs_post_warning_maps_type(self):
        with mock.patch("ai.server.tools.actions.oasis_post", return_value='{"ok":true}') as p:
            actions.aprs_post_warning(40.0, -80.0, "hazard", "flooding")
        body = p.call_args.args[1]
        self.assertEqual(body, {"lat": 40.0, "lon": -80.0, "type": "hazard", "note": "flooding"})
        self.assertEqual(p.call_args.args[0], "/api/aprs/warnings")

    def test_satellite_monitor_maps_selected(self):
        with mock.patch("ai.server.tools.actions.oasis_post", return_value='{"ok":true}') as p:
            actions.satellite_monitor(25544, True)
        self.assertEqual(p.call_args.args[0], "/api/satellites/select")
        self.assertEqual(p.call_args.args[1], {"norad": 25544, "selected": True})

    def test_register_adds_all_action_tools(self):
        added = []
        fake = mock.Mock()
        fake.add_tool = lambda fn, name=None, description=None: added.append(name)
        actions.register(fake, config.load("/nonexistent"))
        self.assertEqual(sorted(added), sorted(actions.ACTION_TOOL_NAMES))
