"""Load the AI assistant configuration (ai/config.json) with safe defaults.

Single source of truth for endpoints and loop limits. Never hard-code these
URLs elsewhere — read them through load().
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(_HERE, "config.json")

DEFAULTS = {
    "model": {
        "base_url": "http://127.0.0.1:8087/v1",
        "name": "qwen2.5-3b-instruct",
        "temperature": 0.2,
        "max_tokens": 1024,
    },
    "oasis_api_base": "http://127.0.0.1:8083",
    "system_prompt": (
        "You are the OASIS station assistant on an offline emergency-comms box. "
        "Answer ONLY using the provided tools and their results. If a question "
        "cannot be answered from the tools, say so plainly — never guess or use "
        "outside knowledge. Be concise; report call signs, times, and units exactly."
    ),
    "max_tool_iterations": 5,
    "request_timeout_s": 60.0,
    "auto_actions": [],
    "action_tools": ["service_control", "aprs_post_warning", "satellite_monitor"],
    "actions_enabled": True,
}


@dataclass(frozen=True)
class Config:
    model_base_url: str
    model_name: str
    temperature: float
    max_tokens: int
    oasis_api_base: str
    system_prompt: str
    max_tool_iterations: int
    request_timeout_s: float
    auto_actions: list[str]
    action_tools: list[str]
    actions_enabled: bool


def _build(model, data) -> Config:
    return Config(
        model_base_url=str(model["base_url"]),
        model_name=str(model["name"]),
        temperature=float(model["temperature"]),
        max_tokens=int(model["max_tokens"]),
        oasis_api_base=str(data["oasis_api_base"]),
        system_prompt=str(data["system_prompt"]),
        max_tool_iterations=int(data["max_tool_iterations"]),
        request_timeout_s=float(data["request_timeout_s"]),
        auto_actions=list(data["auto_actions"]),
        action_tools=list(data["action_tools"]),
        actions_enabled=bool(data["actions_enabled"]),
    )


def load(path: str | None = None) -> Config:
    model = dict(DEFAULTS["model"])
    data = dict(DEFAULTS)
    src = path or DEFAULT_PATH
    try:
        with open(src, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            if isinstance(raw.get("model"), dict):
                model.update(raw["model"])
            for key in ("oasis_api_base", "system_prompt", "max_tool_iterations",
                        "request_timeout_s", "auto_actions", "action_tools", "actions_enabled"):
                if key in raw:
                    data[key] = raw[key]
    except (OSError, ValueError):
        pass
    try:
        return _build(model, data)
    except (TypeError, ValueError, KeyError):
        # A bad-typed or missing field in config.json → fall back to defaults.
        return _build(dict(DEFAULTS["model"]), dict(DEFAULTS))
