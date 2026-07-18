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
