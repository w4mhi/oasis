"""
ADS-B route blueprint — same-origin proxies to the adsb-api recorder
(port 8086, installed by services/adsb/install.py). Extracted verbatim from
server/app.py in the blueprint split; URLs unchanged.
"""

from flask import Blueprint, Response, jsonify, request

bp = Blueprint("adsb", __name__)


def _adsb_proxy(path, timeout=10):
    import urllib.request, urllib.error
    url = f"http://127.0.0.1:8086{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return Response(resp.read(), status=200, content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"ADS-B API unavailable ({reason}). "
                                 "Is the adsb-api service running?"}), 503
    except TimeoutError:
        return jsonify({"ok": False, "error": "ADS-B API timed out."}), 503


@bp.route("/api/adsb/health")
def api_adsb_health_proxy():
    return _adsb_proxy("/health", timeout=3)


@bp.route("/api/adsb/aircraft")
def api_adsb_aircraft_proxy():
    return _adsb_proxy("/aircraft")


@bp.route("/api/adsb/history")
def api_adsb_history_proxy():
    import urllib.parse
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    return _adsb_proxy(f"/history?{qs}")


@bp.route("/api/adsb/recent")
def api_adsb_recent_proxy():
    import urllib.parse
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    return _adsb_proxy(f"/recent?{qs}")


@bp.route("/api/adsb/alerts")
def api_adsb_alerts_proxy():
    return _adsb_proxy("/alerts")


