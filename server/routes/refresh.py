"""Auto-update routes plus the background refresh thread.

The thread starts at module import with a bare Thread().start(), exactly like
the resource guardian in routes/hardware.py.

  !!! THIS IS ONLY SAFE BECAUSE start-oasis.py RUNS GUNICORN WITH --workers 1.
  !!! Raising the worker count would start one thread PER WORKER PROCESS and
  !!! run N concurrent 160 MB FCC downloads. If --workers ever changes, this
  !!! thread needs a cross-process guard FIRST. The pass lock bounds the damage
  !!! but is not a licence to multiply workers.

There is deliberately no "am I online" probe: the fetch attempt IS the probe. A
DNS failure costs milliseconds, so an offline pass is a handful of instant
failures — which is exactly the required behaviour for a station in the field.
"""

import os
import threading
import time

from flask import Blueprint, jsonify, request

from common import refresh as R
from common.web_guard import require_oasis_request

bp = Blueprint("refresh", __name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

_TICK_SEC = 1800          # 30 min: cheap offline, catches short online windows
_FIRST_DELAY_SEC = 120    # let the box finish booting before any network work

# Yield well before the guardian would actually fire, not at the same instant.
_GUARD_HEADROOM = 0.85


@bp.route("/api/refresh/status")
def api_refresh_status():
    """Report freshness. NEVER fetches — this is the read path every UI polls.

    `ok` means the request succeeded, not that the data is current: an offline
    station returns ok:true with stale rows. Freshness is the payload.
    """
    result = R.run_pass(_REPO_ROOT, now=time.time(), metered=R.is_metered(),
                        dry_run=True)
    return jsonify({"ok": True, "checked_at": result["checked_at"],
                    "metered": result["metered"], "busy": False,
                    "sources": result["sources"]})


@bp.route("/api/refresh/run", methods=["POST"])
@require_oasis_request
def api_refresh_run():
    """Operator-triggered pass: the "Update now" tap for deferred sources."""
    body = request.get_json(silent=True) or {}
    only = body.get("source")
    only = [only] if isinstance(only, str) else only
    with R.pass_lock(_REPO_ROOT) as got:
        if not got:
            # The request succeeded; the news is "already running".
            return jsonify({"ok": True, "checked_at": time.time(),
                            "metered": None, "busy": True, "sources": []})
        result = R.run_pass(_REPO_ROOT, now=time.time(),
                            metered=R.is_metered(), only=only,
                            force=bool(body.get("force")))
    return jsonify({"ok": True, "checked_at": result["checked_at"],
                    "metered": result["metered"], "busy": False,
                    "sources": result["sources"]})


def guardian_busy():
    """True when the resource guardian is near a threshold.

    The refresher is the lowest-priority activity on the box and must never be
    the thing that trips a STOP ALL. An FCC index rebuild is a sustained CPU and
    memory load on a 2 GB Pi 3, so large sources yield and retry next pass.

    routes.hardware is imported lazily and read THROUGH THE MODULE: _GUARD_STATS
    is a global the guardian thread reassigns, so `from ... import _GUARD_STATS`
    would freeze a snapshot at import time and never see an update.

    import_module rather than `import routes.hardware as HW` because the latter
    resolves through the package attribute, which cannot be substituted in tests.
    """
    try:
        import importlib
        HW = importlib.import_module("routes.hardware")
        from common import guardian as GUARD
        cfg = HW._load_guardian_config()
        if not cfg.get("enabled"):
            return False
        stats = HW._GUARD_STATS or {}
        thresholds = {}
        for key, limit in (cfg.get("thresholds") or {}).items():
            try:
                thresholds[key] = limit * _GUARD_HEADROOM
            except TypeError:
                thresholds[key] = limit
        return GUARD.over_threshold(stats, thresholds) is not None
    except Exception:
        # Unknowable headroom must not block updates forever.
        return False


def _refresh_runner():
    time.sleep(_FIRST_DELAY_SEC)
    while True:
        try:
            with R.pass_lock(_REPO_ROOT) as got:
                if got:
                    registry = R.REGISTRY
                    if guardian_busy():
                        registry = [s for s in R.REGISTRY if s.tier != "large"]
                    R.run_pass(_REPO_ROOT, now=time.time(),
                               metered=R.is_metered(), registry=registry)
        except Exception:
            pass          # a refresher must never take the server down
        time.sleep(_TICK_SEC)


threading.Thread(target=_refresh_runner, name="oasis-refresh",
                 daemon=True).start()
