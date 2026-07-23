import json
import unittest
from unittest import mock

from ai.orchestrator import routes as ai_routes
from ai.orchestrator.types import AssistantMessage, ToolCall


class FakeRuntime:
    def list_tools(self):
        return [{
            "type": "function",
            "function": {
                "name": "aprs_stations",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    def call_tool(self, name, args):
        return '{"stations": 2}'


def _sse_events(raw):
    out = []
    for block in raw.decode().split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            out.append(json.loads(block[len("data:"):].strip()))
    return out


class TestAssistantRoutes(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(ai_routes.bp)
        self.client = app.test_client()

    def test_health_reports_mcp_ready(self):
        with mock.patch.object(ai_routes, "_get_runtime", return_value=FakeRuntime()):
            r = self.client.get("/api/assistant/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["mcp_ready"])

    def test_chat_streams_tool_then_final(self):
        script = [
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="c1", name="aprs_stations", arguments={})]),
            AssistantMessage(content="2 stations."),
        ]

        def fake_model(cfg):
            calls = iter(script)
            model = mock.Mock()
            model.complete.side_effect = lambda messages, tools: next(calls)
            return model

        with mock.patch.object(ai_routes, "_get_runtime", return_value=FakeRuntime()), \
             mock.patch.object(ai_routes, "_make_model", side_effect=fake_model):
            r = self.client.post("/api/assistant/chat",
                                 json={"message": "how many stations?"})
            events = _sse_events(r.data)
        types = [e["type"] for e in events]
        self.assertIn("tool", types)
        self.assertEqual(types[-1], "final")
        self.assertEqual(events[-1]["content"], "2 stations.")

    def test_chat_requires_message(self):
        r = self.client.post("/api/assistant/chat", json={})
        self.assertEqual(r.status_code, 400)

    def test_chat_runtime_failure_becomes_error_event_not_500(self):
        with mock.patch.object(ai_routes, "_get_runtime",
                               side_effect=RuntimeError("mcp subprocess failed to spawn")):
            r = self.client.post("/api/assistant/chat", json={"message": "hi"})
            events = _sse_events(r.data)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(events)
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("mcp subprocess failed", events[-1]["content"])

    def test_chat_real_model_client_through_loop(self):
        # Regression: the loop calls complete(...); ensure the REAL
        # OpenAIChatModel is wired via model.complete, not passed as a
        # non-callable instance. With the bug present this yields an error event.
        payload = {"choices": [{"message": {"role": "assistant",
                                            "content": "2 stations active."}}]}
        resp = mock.Mock(status_code=200)
        resp.raise_for_status = mock.Mock()
        resp.json = mock.Mock(return_value=payload)
        with mock.patch.object(ai_routes, "_get_runtime", return_value=FakeRuntime()), \
             mock.patch("ai.orchestrator.model_client.httpx.post", return_value=resp):
            r = self.client.post("/api/assistant/chat", json={"message": "how many?"})
            events = _sse_events(r.data)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["content"], "2 stations active.")

    def test_chat_ignores_non_list_history(self):
        script = [AssistantMessage(content="ok")]

        def fake_model(cfg):
            calls = iter(script)
            model = mock.Mock()
            model.complete.side_effect = lambda messages, tools: next(calls)
            return model

        with mock.patch.object(ai_routes, "_get_runtime", return_value=FakeRuntime()), \
             mock.patch.object(ai_routes, "_make_model", side_effect=fake_model):
            r = self.client.post("/api/assistant/chat",
                                 json={"message": "hi", "history": "not-a-list"})
            events = _sse_events(r.data)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(events[-1]["type"], "final")
