"""NOAA Weather Radio route blueprint.

Flask owns no capture. The watch is services/nwr/common/daemon.py -- its own
systemd unit, serving 127.0.0.1:8089 -- because a capture that dies with a
web-server restart, or does not come back after a reboot, is not a watch. What
is left here is a reader and a byte relay: preconditions and config are local
files Flask owns, everything about the capture is asked of the daemon, and the
alert store is READ-ONLY here (the daemon is its single writer; see its module
docstring for why two writers would drop alerts).

Every route is on the API contract (docs/api-contract.md): `ok` means the
request succeeded, not that the news is good. "The watch is not running" and
"multimon-ng is not installed" are ANSWERS on /status, not failures.

/listen/stream returns audio, which contract §10 puts out of scope on the
success path; its refusal paths stay fully conformant.
"""
import json
import os
import time

from flask import Blueprint, Response, jsonify, request, send_from_directory

from common import hardware as HW
from common.api_shape import clamp_limit
from common.web_guard import require_oasis_request
from services.nwr.common import alerts, counties, daemon, listener, settings

bp = Blueprint("nwr", __name__)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_ALERTS_DEFAULT_LIMIT = 50
_ALERTS_MAX_LIMIT = 500

WATCH_BASE = f"http://127.0.0.1:{daemon.API_PORT}"


def _root():
    from app import SUITE_ROOT
    return SUITE_ROOT


def _inventory():
    try:
        return HW.load(_root())
    except Exception:                      # noqa: BLE001
        return None


def _watch_json(path, timeout=5):
    """(payload, error) from the watch daemon. Exactly one is non-None.

    The daemon ships as its own unit and is NOT restarted in lockstep with
    Flask, so every read is treated as potentially unavailable -- the same
    reasoning services/adsb/routes.py records for adsb-api.
    """
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(WATCH_BASE + path, timeout=timeout) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, f"watch returned HTTP {e.code}"
    except Exception as e:                      # noqa: BLE001
        return None, f"watch unavailable ({getattr(e, 'reason', e)})"


def _watch_error_text(err):
    """The daemon's own human-readable reason for a refusal, or None.

    Its body is a real socket: read it and close it on every path, or a refused
    stream leaves the connection open.
    """
    try:
        body = err.read()
    except Exception:                           # noqa: BLE001
        body = b""
    finally:
        try:
            err.close()
        except Exception:                       # noqa: BLE001
            pass
    try:
        return (json.loads(body) or {}).get("error")
    except Exception:                           # noqa: BLE001
        return None


@bp.route("/server/nwr/")
@bp.route("/server/nwr/<path:filename>")
def nwr_static(filename="weather.html"):
    return send_from_directory(_STATIC, filename)


@bp.route("/api/nwr/status")
def api_nwr_status():
    """Local preconditions and config, plus the watch's own account of itself.

    A watch that is down is an ANSWER (contract §2), not a failure: `reachable:
    false` with a `detail` saying why, and every other field at its empty value
    so the shape never changes (§5). A station whose daemon has crashed must
    still render a dashboard that says so, rather than a 500.
    """
    inv = _inventory()
    w, err = _watch_json("/status")
    w = w or {}
    return jsonify({
        "ok": True,
        "preconditions": listener.preconditions(inv=inv),
        "watch": {
            "reachable": err is None,
            "detail": err,
            "phase": w.get("phase"),
            "listening": bool(w.get("listening")),
            "channel": w.get("channel"),
            "channel_hz": w.get("channel_hz"),
            "alerts_seen": w.get("alerts_seen") or 0,
            "last_decode": w.get("last_decode"),
            "last_error": w.get("last_error"),
            "scan": w.get("scan"),
            "scan_weak": bool(w.get("scan_weak")),
            "retry_in_s": w.get("retry_in_s") or 0,
            "streams": w.get("streams") or 0,
            "elapsed_s": w.get("elapsed_s") or 0,
        },
        "config": settings.load(_root()),
        "channels": [{"label": n, "hz": h} for n, h in listener.CHANNELS],
    })


@bp.route("/api/nwr/listen/stream")
def api_nwr_stream():
    """Relay the watch daemon's audio.

    Flask holds no encoder and no subscriber queue -- both live in the daemon,
    which is what keeps the v1 deadlock impossible here by construction. This is
    a byte relay and nothing more.

    Contract note (do not re-litigate this): the success path here is an audio
    stream, not JSON, so unlike a status probe it has no `ok: True` shape to
    carry an answer in. A refusal is therefore a request FAILURE -- `ok: False`
    with 409/503 -- not an answer dressed as `ok: True`. /api/nwr/status is the
    probe for "is the watch running", and ITS success path has room to report
    that as an answer.
    """
    import urllib.error
    import urllib.request
    try:
        upstream = urllib.request.urlopen(WATCH_BASE + "/stream", timeout=10)
    except urllib.error.HTTPError as e:
        # urlopen RAISES on every non-2xx, so the daemon's own refusals -- "the
        # watch is not running" (409), "no audio encoder" and "too many audio
        # streams" (503) -- all arrive here, never as a response object with a
        # status to test. Forward its reason so the page shows the real one.
        detail = _watch_error_text(e)
        if e.code == 409:
            return jsonify({"ok": False,
                            "error": detail or "the watch is not running",
                            "code": "NWR_NOT_LISTENING"}), 409
        return jsonify({"ok": False,
                        "error": detail or f"the watch refused the stream (HTTP {e.code})",
                        "code": "NWR_STREAM_UNAVAILABLE"}), 503
    except Exception as e:                      # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"the weather watch is not reachable ({getattr(e, 'reason', e)})",
                        "code": "NWR_WATCH_UNAVAILABLE"}), 503

    mime = upstream.headers.get("Content-Type", "audio/mpeg")

    def _relay():
        try:
            while True:
                chunk = upstream.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()                    # a client that hangs up must not
                                                # leave a subscriber on the daemon
    return Response(_relay(), mimetype=mime)


@bp.route("/api/nwr/alerts")
def api_nwr_alerts():
    limit = clamp_limit(request.args.get("limit"), _ALERTS_DEFAULT_LIMIT,
                        _ALERTS_MAX_LIMIT)
    now = time.time()
    recs = alerts.load(_root())
    return jsonify({
        "ok": True,
        "alerts": recs[:limit],
        "active": [r["id"] for r in alerts.active(recs, now)],
        "count": len(recs),
    })


@bp.route("/api/nwr/config", methods=["GET"])
def api_nwr_config_get():
    return jsonify({"ok": True, "config": settings.load(_root())})


@bp.route("/api/nwr/config", methods=["POST"])
@require_oasis_request
def api_nwr_config_set():
    try:
        cfg = settings.save(_root(), request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e),
                        "code": "NWR_BAD_CONFIG"}), 400
    return jsonify({"ok": True, "config": cfg})


@bp.route("/api/nwr/counties")
def api_nwr_counties():
    return jsonify({"ok": True,
                    "counties": counties.all_counties(counties.load(_root()))})
