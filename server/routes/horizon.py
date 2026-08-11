"""POST /api/satellites/horizon — the operator's azimuth-dependent skyline.

WHAT THIS MODULE DOES NOT DO: interpolate. The mask is evaluated entirely
client-side (common/js/horizon.js), because every pass already carries `peak_az`
and the server has no caller that needs the curve. Keeping the maths in one place
is deliberate — a Python copy would be a second implementation to keep in
agreement with the first, for nobody.

WHY IT LIVES HERE and not in services/satellites/: it writes station.json, whose
write surface is policed by tests/test_station_json_writes.py — that test asserts
no route opens the file for a truncating write, and it only stays meaningful
while every writer sits beside aprs_freq.py. The ROUTE is namespaced by its
consumer; the FILE follows who may write the config.
"""
import datetime
import os

from flask import Blueprint, jsonify, request

from common import atomic_json, config_paths
from common.web_guard import require_oasis_request

SUITE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

bp = Blueprint("horizon", __name__)

# The 16 sector names, duplicated from common/js/horizon.js on purpose: this
# module validates KEYS, it does not evaluate the mask, so the duplication is a
# vocabulary and not a second implementation of the curve.
SECTORS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

# Same bounds as _min_elev's: 90 and above would silently empty the pass list,
# which on screen is indistinguishable from a broken predictor.
_MIN_DEG, _MAX_DEG = 0.0, 89.0


def normalise(horizon):
    """A validated, float-valued copy of a horizon mask, or ValueError.

    A PARTIAL mask is legal — a missing sector falls back to min_elev
    client-side, so {"N": 25} is the complete statement "blocked to the north
    and nowhere else". An EMPTY one is legal too: clear all round.

    Unknown keys are rejected rather than stored. Storing them would let a typo
    ("NORTH": 25) sit in station.json looking like it was doing something."""
    if not isinstance(horizon, dict):
        raise ValueError("horizon must be an object")
    out = {}
    for key, value in horizon.items():
        if key not in SECTORS:
            raise ValueError(f"unknown sector {key!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key}: elevation must be a number")
        v = float(value)
        if not (_MIN_DEG <= v <= _MAX_DEG):
            raise ValueError(f"{key}: elevation must be between {_MIN_DEG} and {_MAX_DEG}")
        out[key] = v
    return out


def _persist(horizon):
    """Write `horizon` into station.json, preserving every other key.

    `strict` refuses when the file exists but will not parse, rather than
    replacing it with just a horizon: station.json holds the callsign, grid and
    position, and an offline station cannot re-fetch any of them.

    The mask REPLACES rather than merges — dragging a handle back down to clear
    has to be able to remove a sector."""
    path = config_paths.station_json(SUITE_ROOT)
    os.makedirs(config_paths.config_dir(SUITE_ROOT), exist_ok=True)
    body = atomic_json.read_json(path, strict=True)
    body["horizon"] = horizon
    body["updated"] = datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_json.write_json(path, body)


@bp.route("/api/satellites/horizon", methods=["POST"])
@require_oasis_request
def api_set_horizon():
    data = request.get_json(silent=True) or {}
    try:
        horizon = normalise(data.get("horizon"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        _persist(horizon)
    except ValueError:
        return jsonify({"ok": False, "error": (
            "configuration/station.json is unreadable — the horizon was NOT saved "
            "(refusing to overwrite it). Fix or remove the file and retry.")}), 500
    except OSError as exc:
        return jsonify({"ok": False, "error": f"could not save horizon: {exc}"}), 500

    return jsonify({"ok": True, "horizon": horizon})
