"""
APRS route blueprint — same-origin proxies to the graywolf-api (port 8085)
plus the operator-placed map warnings store. Extracted verbatim from
server/app.py in the blueprint split; URLs unchanged.
"""

import json
import os
import threading
import threading as _threading
import time
import uuid

from flask import Blueprint, Response, jsonify, request

import appconfig
from common import config_paths
from services.aprs.common import warning_catalog
from services.aprs.common.graywolf_client import GraywolfClient
from services.aprs.common.warning_broadcast import WarningBroadcaster

SUITE_ROOT = appconfig.SUITE_ROOT

bp = Blueprint("aprs", __name__)

# Operator-placed map warnings (flood/fire/etc.) shared across every device that
# views the APRS map. Small JSON list on disk; serialized writes via a lock.
# Runtime state, not repo content — gitignored.
WARNINGS_FILE = os.path.join(SUITE_ROOT, "aprs-warnings.json")
_warnings_lock = threading.Lock()

_TEST_BROADCASTER = None          # tests inject a fake here
_broadcaster_cache = None
_broadcaster_lock = _threading.Lock()
_reconcile_lock = _threading.Lock()
_last_reconcile = [0.0]
_RECONCILE_MIN_INTERVAL = 120     # seconds


def _get_broadcaster():
    """Return a cached WarningBroadcaster, or None when unconfigured/unavailable."""
    if _TEST_BROADCASTER is not None:
        return _TEST_BROADCASTER
    global _broadcaster_cache
    with _broadcaster_lock:
        if _broadcaster_cache is not None:
            return _broadcaster_cache
        cfg_path = config_paths.graywolf_api_json(SUITE_ROOT)
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            return None
        base = cfg.get("base_url") or "http://127.0.0.1:8080"
        user = cfg.get("username")
        pw = cfg.get("password")
        if not user or not pw:
            return None
        send_path = cfg.get("send_path") or "both"
        client = GraywolfClient(base, user, pw)
        symbols = warning_catalog.load_symbol_map(SUITE_ROOT)
        _broadcaster_cache = WarningBroadcaster(client, symbols, send_path=send_path)
        return _broadcaster_cache

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


def _maybe_reconcile():
    """Fire a throttled, non-blocking reconcile when broadcast warnings exist."""
    b = _get_broadcaster()
    if b is None:
        return
    now = time.time()
    with _reconcile_lock:
        if now - _last_reconcile[0] < _RECONCILE_MIN_INTERVAL:
            return
        _last_reconcile[0] = now
    warnings = _load_warnings()
    if not any(w.get("broadcast") for w in warnings):
        return

    def _run():
        try:
            b.reconcile(warnings)
        except Exception:  # noqa: BLE001
            pass
    _threading.Thread(target=_run, daemon=True).start()


@bp.route("/api/aprs/warnings", methods=["GET"])
def api_aprs_warnings_list():
    _maybe_reconcile()
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
            "broadcast": bool(body.get("broadcast")),
            "gw_beacon_id": None,
        }
        from services.aprs.common.warning_broadcast import object_name as _obj_name
        item["aprs_name"] = _obj_name(item["id"]).strip()
        if item["broadcast"]:
            b = _get_broadcaster()
            if b is not None:
                item["gw_beacon_id"] = b.advertise(item)
        warnings.append(item)
        _save_warnings(warnings)
    return jsonify({"ok": True, "warning": item})


@bp.route("/api/aprs/warnings/<wid>", methods=["PATCH"])
def api_aprs_warnings_update(wid):
    body = request.get_json(silent=True) or {}
    has_note = "note" in body
    has_bcast = "broadcast" in body
    note = _clean_note(body.get("note")) if has_note else None
    with _warnings_lock:
        warnings = _load_warnings()
        found = None
        for w in warnings:
            if w.get("id") == wid:
                found = w
                break
        if found is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        if has_note:
            found["note"] = note
        b = _get_broadcaster()
        transitioned = False
        if has_bcast:
            want = bool(body.get("broadcast"))
            if want and not found.get("broadcast"):
                found["broadcast"] = True
                found["gw_beacon_id"] = b.advertise(found) if b else None
                transitioned = True
            elif not want and found.get("broadcast"):
                if b:
                    b.unadvertise(found)
                found["broadcast"] = False
                found["gw_beacon_id"] = None
                transitioned = True
        # Push a note edit to the live beacon comment when the warning stays
        # broadcast (advertise already carried the note, so skip if we just
        # transitioned).
        if (has_note and not transitioned and found.get("broadcast")
                and found.get("gw_beacon_id") and b):
            try:
                b.c.update_beacon(found["gw_beacon_id"], {"comment": found["note"]})
            except Exception:  # noqa: BLE001
                pass
        _save_warnings(warnings)
    return jsonify({"ok": True, "warning": found})


@bp.route("/api/aprs/warnings/<wid>", methods=["DELETE"])
def api_aprs_warnings_delete(wid):
    with _warnings_lock:
        warnings = _load_warnings()
        victim = next((w for w in warnings if w.get("id") == wid), None)
        if victim is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        if victim.get("broadcast"):
            b = _get_broadcaster()
            if b:
                b.unadvertise(victim)
        kept = [w for w in warnings if w.get("id") != wid]
        _save_warnings(kept)
    return jsonify({"ok": True})


