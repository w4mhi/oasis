"""Diagnostics route blueprint — GET /api/diagnostics.

Thin HTTP wrapper around common.diagnostics.run_all(): runs every registered
station-health check (server/routes/system.py & friends already expose the
signals it reads) and returns the aggregated summary/capabilities/fix_now/
groups payload the read-only Diagnostic page renders.

See common/diagnostics.py's run_all() docstring for the response shape, and
start-oasis.py / scripts/start-server.sh's gunicorn `--threads 4` comment for
why this route needs a threaded worker: run_all() makes localhost self-HTTP
calls (e.g. /api/system, /api/aprs/stations) from inside the same Flask
process that is serving this request.
"""

from flask import Blueprint, jsonify

import appconfig
from common.diagnostics import run_all

bp = Blueprint("diagnostics", __name__)


@bp.route("/api/diagnostics")
def api_diagnostics():
    """Run the full diagnostics sweep and return the aggregated result.

    §10: run_all()'s five keys are named here rather than splatted, so the
    response shape is readable at the return site. `ok` is the REQUEST — a
    sweep that finds failures still succeeded; the findings live in `summary`."""
    try:
        report = run_all("127.0.0.1", appconfig.PORT)
    except Exception:
        return jsonify({"ok": False, "error": "diagnostics failed",
                        "code": "DIAGNOSTICS_FAILED"}), 500
    return jsonify({
        "ok": True,
        "ran_at": report.get("ran_at"),
        "summary": report.get("summary") or {},
        "capabilities": report.get("capabilities") or [],
        "groups": report.get("groups") or [],
        "fix_now": report.get("fix_now"),
    })
