"""The MCP-host tool-calling loop.

run_turn drives one user turn: ask the model, run any tool calls through MCP,
feed results back, repeat until the model answers or the iteration cap trips.
Yields event dicts so the caller (SSE route) can stream progress. Never raises
on a tool failure — the error is fed back so the model can recover.
"""
import json


def _assistant_dict(msg):
    out = {"role": "assistant", "content": msg.content or None}
    if msg.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
            for tc in msg.tool_calls
        ]
    return out


def run_turn(messages, mcp, complete, *, max_iterations=5):
    tools = mcp.list_tools()
    for _ in range(max_iterations):
        msg = complete(messages, tools)
        if not msg.tool_calls:
            yield {"type": "final", "content": msg.content}
            return
        messages.append(_assistant_dict(msg))
        for tc in msg.tool_calls:
            yield {"type": "tool", "name": tc.name, "arguments": tc.arguments}
            try:
                result = mcp.call_tool(tc.name, tc.arguments)
            except Exception as exc:  # noqa: BLE001 - surfaced to the model, not fatal
                result = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            yield {"type": "tool_result", "name": tc.name, "content": result}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    yield {"type": "error",
           "content": "Reached the tool-call limit without a final answer."}
