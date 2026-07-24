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


class TestAssistantConfirm(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(ai_routes.bp)
        self.client = app.test_client()

    def test_confirm_executes_pending_action(self):
        pid = ai_routes._pending.add("service_control", {"unit": "graywolf", "action": "stop"})

        class FakeRuntimeExec:
            def call_tool(self, name, args):
                return '{"ok": true, "did": "%s"}' % name
        with mock.patch.object(ai_routes, "_get_runtime", return_value=FakeRuntimeExec()):
            r = self.client.post("/api/assistant/confirm", json={"id": pid})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["name"], "service_control")

    def test_confirm_decline_does_not_execute(self):
        pid = ai_routes._pending.add("service_control", {"unit": "graywolf", "action": "stop"})
        called = []

        class FR:
            def call_tool(self, name, args):
                called.append(name); return "{}"
        with mock.patch.object(ai_routes, "_get_runtime", return_value=FR()):
            r = self.client.post("/api/assistant/confirm", json={"id": pid, "decision": "decline"})
        self.assertTrue(r.get_json()["declined"])
        self.assertEqual(called, [])
        # the id is consumed even on decline
        self.assertIsNone(ai_routes._pending.take(pid))

    def test_confirm_unknown_id_410(self):
        r = self.client.post("/api/assistant/confirm", json={"id": "nope"})
        self.assertEqual(r.status_code, 410)

    def test_confirm_missing_id_400(self):
        self.assertEqual(self.client.post("/api/assistant/confirm", json={}).status_code, 400)

    def test_confirm_non_string_id_does_not_500(self):
        r = self.client.post("/api/assistant/confirm", json={"id": 123})
        self.assertIn(r.status_code, (400, 410))   # clean, not a 500 crash


class TestAssistantPrompts(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(ai_routes.bp)
        self.client = app.test_client()

    def test_prompts_lists_with_text(self):
        class FakeRuntimePrompts:
            def list_prompts(self):
                return [{"name": "net-briefing", "title": "Net status briefing"}]
            def get_prompt(self, name):
                return "Give me a briefing."
        with mock.patch.object(ai_routes, "_get_runtime", return_value=FakeRuntimePrompts()):
            r = self.client.get("/api/assistant/prompts")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["prompts"][0]["name"], "net-briefing")
        self.assertEqual(body["prompts"][0]["title"], "Net status briefing")
        self.assertEqual(body["prompts"][0]["text"], "Give me a briefing.")

    def test_prompts_degrades_when_runtime_down(self):
        with mock.patch.object(ai_routes, "_get_runtime",
                               side_effect=RuntimeError("mcp down")):
            r = self.client.get("/api/assistant/prompts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["prompts"], [])
