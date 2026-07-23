"""Flask blueprint: the AI assistant host surface.

/api/assistant/health  - is the MCP runtime up?
/api/assistant/chat    - SSE stream of one tool-calling turn
/assistant             - minimal standalone chat page

The MCP runtime and model client are built lazily via _get_runtime()/_make_model
so importing this blueprint (and registering it in app.py) never requires the
mcp SDK, a model, or a subprocess to be present — keeps CI green on non-Pi hosts.
"""
import json
import os

from flask import Blueprint, Response, jsonify, request, send_file

from ai import config
from ai.orchestrator.loop import run_turn

bp = Blueprint("assistant", __name__)

_WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))


def _get_runtime():
    from ai.orchestrator.mcp_runtime import default_runtime
    return default_runtime()


def _make_model(cfg):
    from ai.orchestrator.model_client import OpenAIChatModel
    return OpenAIChatModel(cfg)


@bp.route("/api/assistant/health")
def health():
    cfg = config.load()
    ready = False
    try:
        ready = bool(_get_runtime().list_tools())
    except Exception:  # noqa: BLE001 - health must never 500
        ready = False
    return jsonify({"ok": True, "mcp_ready": ready,
                    "model_base_url": cfg.model_base_url})


@bp.route("/api/assistant/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400
    cfg = config.load()
    history = body.get("history") or []
    messages = [{"role": "system", "content": cfg.system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    runtime = _get_runtime()
    model = _make_model(cfg)

    def stream():
        try:
            for event in run_turn(messages, runtime, model,
                                  max_iterations=cfg.max_tool_iterations):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001 - report, don't drop the stream
            err = {"type": "error", "content": f"{type(exc).__name__}: {exc}"}
            yield f"data: {json.dumps(err)}\n\n"

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/assistant")
def page():
    return send_file(os.path.join(_WEB_DIR, "assistant.html"))
