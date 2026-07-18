"""Satellites blueprint: the /server/satellites/ page + /api/satellites/* JSON.
Hardware-free (Phase 1) — always available, no feature gate."""
import json
import os

from flask import Blueprint, jsonify, request, send_from_directory  # noqa: F401

import appconfig
from common import config_paths

_HERE = os.path.dirname(os.path.abspath(__file__))
import sys
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import roster        # noqa: E402
import tle           # noqa: E402
import predict       # noqa: E402, F401

SUITE_ROOT = appconfig.SUITE_ROOT
STATIC_DIR = os.path.join(_HERE, "static")

bp = Blueprint("satellites", __name__)


def _station():
    try:
        with open(config_paths.station_json(SUITE_ROOT), encoding="utf-8") as fh:
            s = json.load(fh)
        return {"lat": float(s["lat"]), "lon": float(s["lon"])}
    except (OSError, ValueError, KeyError):
        return {"lat": None, "lon": None}


@bp.route("/server/satellites/")
@bp.route("/server/satellites/<path:filename>")
def satellites_static(filename="satellites.html"):
    return send_from_directory(STATIC_DIR, filename)


@bp.route("/api/satellites")
def api_satellites():
    data = roster.load(config_paths.satellites_json(SUITE_ROOT))
    return jsonify({
        "satellites": data["satellites"],
        "tle_age_days": tle.cache_age_days(config_paths.tle_cache_dir(SUITE_ROOT)),
        "station": _station(),
    })


import datetime
import hashlib

_CACHE_TTL_S = 6 * 3600   # recompute at most every 6 h (TLEs change ~every 3 days)


def _cache_path(key):
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    d = os.path.join(config_paths.tle_cache_dir(SUITE_ROOT), "_passes")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ".json")


def _sats_by_norad():
    """{norad: EarthSatellite} for roster entries present in the TLE cache."""
    cache = tle.load_cache(config_paths.tle_cache_dir(SUITE_ROOT))
    data = roster.load(config_paths.satellites_json(SUITE_ROOT))
    out = {}
    for s in data["satellites"]:
        lines = cache.get(s["name"])
        if lines:
            try:
                out[s["norad"]] = predict.make_satellite(s["name"], lines[0], lines[1])
            except Exception:
                # A malformed/unparsable TLE must not take down the roster lookup —
                # just skip that satellite.
                continue
    return out


@bp.route("/api/satellites/passes")
def api_passes():
    try:
        window = int(request.args.get("window", 48))
    except (TypeError, ValueError):
        window = 48
    st = _station()
    if st["lat"] is None:
        return jsonify({"passes": {}, "error": "no station location"}), 200
    only = request.args.get("sat")
    tle_stamp = tle.cache_mtime(config_paths.tle_cache_dir(SUITE_ROOT))
    key = f"{st['lat']},{st['lon']},{window},{only},{int(tle_stamp) if tle_stamp else 0}"
    cp = _cache_path(key)
    if os.path.exists(cp) and (datetime.datetime.now().timestamp() - os.path.getmtime(cp)) < _CACHE_TTL_S:
        with open(cp, encoding="utf-8") as fh:
            return jsonify(json.load(fh))
    start = datetime.datetime.now(datetime.timezone.utc)
    result = {"passes": {}}
    for norad, sat in _sats_by_norad().items():
        if only and str(norad) != str(only):
            continue
        try:
            result["passes"][str(norad)] = predict.compute_passes(
                sat, st["lat"], st["lon"], start, hours=window, min_elev=10.0)
        except Exception:
            # A stale/decayed TLE can make SGP4 propagation blow up (e.g. far past
            # epoch). One bad satellite must never 500 the whole response — skip it.
            result["passes"][str(norad)] = []
    with open(cp, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return jsonify(result)


@bp.route("/api/satellites/track")
def api_track():
    st = _station()
    norad = request.args.get("sat")
    try:
        norad_i = int(norad) if norad else None
    except (TypeError, ValueError):
        norad_i = None
    sat = _sats_by_norad().get(norad_i) if norad_i is not None else None
    if sat is None or st["lat"] is None:
        return jsonify({"track": [], "error": "unknown sat or no station"}), 200
    frm = datetime.datetime.fromisoformat(request.args["from"])
    to = datetime.datetime.fromisoformat(request.args["to"])
    data = roster.load(config_paths.satellites_json(SUITE_ROOT))
    entry = next((s for s in data["satellites"] if s["norad"] == norad_i), None)
    dl = None
    if entry and entry["downlinks"]:
        dl = int(entry["downlinks"][0]["freq_mhz"] * 1_000_000)
    try:
        track = predict.compute_track(sat, st["lat"], st["lon"], frm, to,
                                       step_s=10, downlink_hz=dl)
    except Exception:
        # Same stale-TLE risk as passes — never 500, just return an empty track.
        track = []
    return jsonify({"track": track})


@bp.route("/api/satellites/select", methods=["POST"])
def api_select():
    body = request.get_json(force=True)
    data = roster.set_selected(config_paths.satellites_json(SUITE_ROOT),
                               int(body["norad"]), bool(body["selected"]))
    return jsonify(data)
