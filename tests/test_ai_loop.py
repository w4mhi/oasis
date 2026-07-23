import unittest

from ai.orchestrator import loop
from ai.orchestrator.types import AssistantMessage, ToolCall


class FakeMcp:
    def __init__(self, result='{"stations": 3}'):
        self.result = result
        self.calls = []

    def list_tools(self):
        return [{"type": "function", "function": {"name": "aprs_stations"}}]

    def call_tool(self, name, args):
        self.calls.append((name, args))
        return self.result


class ScriptedModel:
    """Returns queued AssistantMessages in order, one per complete() call."""
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def __call__(self, messages, tools):
        self.seen.append([m for m in messages])
        return self.script.pop(0)


class TestRunTurn(unittest.TestCase):
    def test_tool_then_final(self):
        model = ScriptedModel([
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="c1", name="aprs_stations", arguments={})]),
            AssistantMessage(content="There are 3 stations."),
        ])
        mcp = FakeMcp()
        messages = [{"role": "user", "content": "how many stations?"}]
        events = list(loop.run_turn(messages, mcp, model))
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool", "tool_result", "final"])
        self.assertEqual(events[-1]["content"], "There are 3 stations.")
        self.assertEqual(mcp.calls, [("aprs_stations", {})])
        # a tool message was appended so the model saw the result on turn 2
        self.assertTrue(any(m.get("role") == "tool" for m in messages))

    def test_immediate_final_no_tools(self):
        model = ScriptedModel([AssistantMessage(content="73!")])
        events = list(loop.run_turn([{"role": "user", "content": "hi"}], FakeMcp(), model))
        self.assertEqual([e["type"] for e in events], ["final"])

    def test_tool_error_is_surfaced_not_raised(self):
        class Boom(FakeMcp):
            def call_tool(self, name, args):
                raise RuntimeError("mcp down")
        model = ScriptedModel([
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="c1", name="aprs_stations", arguments={})]),
            AssistantMessage(content="Sorry, that failed."),
        ])
        events = list(loop.run_turn([{"role": "user", "content": "x"}], Boom(), model))
        result_event = next(e for e in events if e["type"] == "tool_result")
        self.assertIn("mcp down", result_event["content"])

    def test_iteration_cap_emits_error(self):
        # model always asks for a tool, never finalises
        class Loopy:
            def __call__(self, messages, tools):
                return AssistantMessage(content="", tool_calls=[
                    ToolCall(id="c", name="aprs_stations", arguments={})])
        events = list(loop.run_turn([{"role": "user", "content": "x"}],
                                    FakeMcp(), Loopy(), max_iterations=3))
        self.assertEqual(events[-1]["type"], "error")
