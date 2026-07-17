"""
APRS route blueprint — same-origin proxies to the graywolf-api (port 8085)
plus the operator-placed map warnings store. Extracted verbatim from
server/app.py in the blueprint split; URLs unchanged.
"""

import json
import os
import threading
import time
import uuid

from flask import Blueprint, Response, jsonify, request

import appconfig

SUITE_ROOT = appconfig.SUITE_ROOT

bp = Blueprint("aprs", __name__)

# Operator-placed map warnings (flood/fire/etc.) shared across every device that
# views the APRS map. Small JSON list on disk; serialized writes via a lock.
# Runtime state, not repo content — gitignored.
WARNINGS_FILE = os.path.join(SUITE_ROOT, "aprs-warnings.json")
_warnings_lock = threading.Lock()

@bp.route("/api/aprs/health")
def api_aprs_health_proxy():
    """Proxy health check for the graywolf-api (port 8085).
    Same-origin so the browser doesn’t need to make a cross-origin request."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:8085/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False, "graywolf_reachable": False,
                        "error": str(reason)}), 503


@bp.route("/api/aprs/stations")
def api_aprs_stations_proxy():
    """Proxy APRS station list from the graywolf-api (port 8085).
    Keeps the browser on the same origin — no cross-origin fetch needed.
    Timeout must exceed graywolf-api's own inner timeout (3 s) + overhead."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:8085/api/aprs/stations"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        # graywolf-api is running but returned an error (e.g. DB not found).
        # Pass through its JSON body verbatim so the UI gets the real message.
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"APRS API unavailable ({reason}). "
                                 "Is the graywolf-api service running?"}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "APRS API timed out — graywolf-api slow or GrayWolf unreachable."}), 503


@bp.route("/api/aprs/track")
def api_aprs_track_proxy():
    """Proxy station position history from the graywolf-api (port 8085).
    Forwards ?callsign= and ?minutes= query params verbatim."""
    import urllib.request
    import urllib.error
    import urllib.parse
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    url = f"http://127.0.0.1:8085/api/aprs/track?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"APRS API unavailable ({reason})."}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "APRS API timed out."}), 503


@bp.route("/api/aprs/system")
def api_aprs_system_proxy():
    """Proxy system stats (CPU/RAM/temp) from the graywolf-api (port 8085).
    The value is server-cached (5 s sampler) so this is a cheap, fast read."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:8085/api/system"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"APRS API unavailable ({reason})."}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "APRS API timed out."}), 503


# ── Operator map warnings (shared, persisted) ─────────────────────────────────
# Flood/fire/etc. markers an operator drops on the APRS map. Stored server-side
# so every device viewing the map sees the same set. Small JSON list on disk,
# writes serialized through _warnings_lock. Owned by this server (not the APRS
# API), so warnings work even when the APRS chain is offline.
_WARN_TYPE_MAX = 64    # max length of a warning "type" id
_WARN_NOTE_MAX = 50    # note character cap (mirrors the UI maxlength)
_WARN_MAX      = 500   # hard cap on total stored warnings


def _load_warnings():
    """Return the warnings list from disk ([] if absent/unreadable)."""
    try:
        with open(WARNINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_warnings(warnings):
    """Write the list atomically (temp file + os.replace)."""
    tmp = WARNINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(warnings, fh)
    os.replace(tmp, WARNINGS_FILE)


def _clean_note(value):
    """Single-line, trimmed, length-capped note string."""
    return str(value or "").replace("\n", " ").replace("\r", " ").strip()[:_WARN_NOTE_MAX]


@bp.route("/api/aprs/warnings", methods=["GET"])
def api_aprs_warnings_list():
    return jsonify({"ok": True, "warnings": _load_warnings()})


@bp.route("/api/aprs/warnings", methods=["POST"])
def api_aprs_warnings_add():
    body = request.get_json(silent=True) or {}
    try:
        lon = float(body.get("lon"))
        lat = float(body.get("lat"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lon/lat required (numeric)"}), 400
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return jsonify({"ok": False, "error": "lon/lat out of range"}), 400
    wtype = str(body.get("type") or "").strip()[:_WARN_TYPE_MAX]
    if not wtype:
        return jsonify({"ok": False, "error": "type required"}), 400
    note = _clean_note(body.get("note"))
    with _warnings_lock:
        warnings = _load_warnings()
        if len(warnings) >= _WARN_MAX:
            return jsonify({"ok": False, "error": "warning limit reached"}), 409
        item = {
            "id":   uuid.uuid4().hex,
            "type": wtype,
            "lon":  lon,
            "lat":  lat,
            "note": note,
            "ts":   int(time.time()),
        }
        warnings.append(item)
        _save_warnings(warnings)
    return jsonify({"ok": True, "warning": item})


@bp.route("/api/aprs/warnings/<wid>", methods=["PATCH"])
def api_aprs_warnings_update(wid):
    body = request.get_json(silent=True) or {}
    note = _clean_note(body.get("note"))
    with _warnings_lock:
        warnings = _load_warnings()
        found = None
        for w in warnings:
            if w.get("id") == wid:
                w["note"] = note
                found = w
                break
        if found is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        _save_warnings(warnings)
    return jsonify({"ok": True, "warning": found})


@bp.route("/api/aprs/warnings/<wid>", methods=["DELETE"])
def api_aprs_warnings_delete(wid):
    with _warnings_lock:
        warnings = _load_warnings()
        kept = [w for w in warnings if w.get("id") != wid]
        if len(kept) == len(warnings):
            return jsonify({"ok": False, "error": "not found"}), 404
        _save_warnings(kept)
    return jsonify({"ok": True})


