"""Load the AI assistant configuration (ai/config.json) with safe defaults.

Single source of truth for endpoints and loop limits. Never hard-code these
URLs elsewhere — read them through load().
"""
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
    auto_actions: list


def load(path: str | None = None) -> Config:
    data = dict(DEFAULTS)
    model = dict(DEFAULTS["model"])
    src = path or DEFAULT_PATH
    try:
        with open(src, encoding="utf-8") as fh:
            raw = json.load(fh)
        model.update(raw.get("model", {}))
        for key in ("oasis_api_base", "system_prompt", "max_tool_iterations",
                    "request_timeout_s", "auto_actions"):
            if key in raw:
                data[key] = raw[key]
    except (FileNotFoundError, ValueError):
        pass
    return Config(
        model_base_url=model["base_url"],
        model_name=model["name"],
        temperature=float(model["temperature"]),
        max_tokens=int(model["max_tokens"]),
        oasis_api_base=data["oasis_api_base"],
        system_prompt=data["system_prompt"],
        max_tool_iterations=int(data["max_tool_iterations"]),
        request_timeout_s=float(data["request_timeout_s"]),
        auto_actions=list(data["auto_actions"]),
    )
