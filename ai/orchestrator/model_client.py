"""OpenAI-compatible chat client for the local model (llama-server, etc.).

Only the surface the loop needs: complete(messages, tools) -> AssistantMessage.
Tests stub this out entirely; nothing here requires a model to be running until
.complete() is actually called.
"""
import json

import httpx

from ai.orchestrator.types import AssistantMessage, ToolCall


class OpenAIChatModel:
    def __init__(self, cfg):
        self._cfg = cfg

    def complete(self, messages, tools):
        body = {
            "model": self._cfg.model_name,
            "messages": messages,
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        # Long read timeout (generation is slow on a Pi 5 CPU), short connect —
        # a scalar timeout would apply 60s to the read and ReadTimeout mid-answer.
        timeout = httpx.Timeout(self._cfg.model_timeout_s, connect=10.0)
        resp = httpx.post(self._cfg.model_base_url.rstrip("/") + "/chat/completions",
                          json=body, timeout=timeout)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""),
                                  arguments=args))
        return AssistantMessage(content=msg.get("content") or "", tool_calls=calls)
