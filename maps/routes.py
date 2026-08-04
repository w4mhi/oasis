"""
Map-data HTTP layer — base-map inventory.

OASIS no longer ships or downloads its own tiles. Maps come from GrayWolf's
offline store at /var/lib/graywolf/tiles/state (downloaded inside GrayWolf under a
registered callsign); this blueprint just reports which GrayWolf archives are
present so the traffic-map dropdown can list them. The archives themselves are
streamed by maps/traffic/routes.py's allowlisted /api/fs/pmtiles route.
"""

import os

from flask import Blueprint, jsonify

bp = Blueprint("mapdata", __name__)

# GrayWolf's offline-tiles directory (per-region <name>.pmtiles). OASIS reads it
# directly; overridable via env for tests / non-standard installs.
GW_STATE_DIR = os.environ.get("OASIS_GRAYWOLF_TILES", "/var/lib/graywolf/tiles/state")


def _present():
    """Sorted archive names (sans .pmtiles) present in GrayWolf's tiles dir."""
    try:
        names = os.listdir(GW_STATE_DIR)
    except OSError:
        return []
    out = [fn[: -len(".pmtiles")] for fn in names if fn.endswith(".pmtiles")]
    out.sort()
    return out


@bp.get("/api/maps")
def maps_inventory():
    present = _present()
    return jsonify({
        "present": present,          # GrayWolf archives available to the dropdown
        "source": "graywolf",
        "graywolf_dir": GW_STATE_DIR,
        "have_maps": bool(present),  # false -> front-end shows the "download in GrayWolf" state
    })
