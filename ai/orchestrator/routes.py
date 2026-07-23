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
from ai.orchestrator.gate import ActionGate
from ai.orchestrator.loop import run_turn
from ai.orchestrator.pending import PendingRegistry

bp = Blueprint("assistant", __name__)

_pending = PendingRegistry()

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
    history = body.get("history")
    if not isinstance(history, list):
        history = []
    messages = [{"role": "system", "content": cfg.system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    def stream():
        try:
            runtime = _get_runtime()
            model = _make_model(cfg)
            gate = ActionGate(cfg, _pending)
            for event in run_turn(messages, runtime, model.complete,
                                  max_iterations=cfg.max_tool_iterations, gate=gate):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001 - report, don't drop the stream
            err = {"type": "error", "content": f"{type(exc).__name__}: {exc}"}
            yield f"data: {json.dumps(err)}\n\n"

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/api/assistant/confirm", methods=["POST"])
def confirm():
    body = request.get_json(silent=True) or {}
    pid = str(body.get("id") or "").strip()
    if not pid:
        return jsonify({"ok": False, "error": "id required"}), 400
    decision = str(body.get("decision") or "approve").strip().lower()
    action = _pending.take(pid)
    if decision == "decline":
        return jsonify({"ok": True, "declined": True})
    if action is None:
        return jsonify({"ok": False, "error": "unknown or expired confirmation"}), 410
    name, args = action
    try:
        result = _get_runtime().call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({"ok": True, "name": name, "result": result})


@bp.route("/assistant")
def page():
    return send_file(os.path.join(_WEB_DIR, "assistant.html"))


@bp.route("/assistant/<path:asset>")
def page_asset(asset):
    if asset not in ("assistant.js", "assistant.css"):
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_file(os.path.join(_WEB_DIR, asset))
