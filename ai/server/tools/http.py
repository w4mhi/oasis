"""Self-loop HTTP helper: MCP tools read OASIS data over its own localhost API.

Returns text (usually JSON) so the tool result is passed straight to the model.
Never raises — failures come back as a JSON error object the model can read.
"""
import json

import httpx


def oasis_get(path, params=None, *, base, timeout):
    try:
        url = base.rstrip("/") + path
        resp = httpx.get(url, params=params or {}, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:  # noqa: BLE001 - deliberately total; model reads the error
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
