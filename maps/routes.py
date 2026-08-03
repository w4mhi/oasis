"""
Map-data HTTP layer — US-state inventory + streaming region download (extract),
a thin web wrapper over maps/mapctl.py.

The traffic-map app (page, assets, /api/fs browser) lives in maps/traffic/; this
blueprint owns the *data* side: which states are downloaded, and cutting a new
state's Protomaps tiles out of a planet build with the go-pmtiles binary.
"""

import os
import threading

from flask import Blueprint, jsonify, request, Response, stream_with_context

import appconfig
from maps import mapctl

bp = Blueprint("mapdata", __name__)

SUITE_ROOT  = appconfig.SUITE_ROOT
STATE_DIR   = os.path.join(SUITE_ROOT, "maps", "tiles", "state")   # <State>.pmtiles + .bin/pmtiles
STATES_JSON = os.path.join(SUITE_ROOT, "maps", "us-states.geojson")

# One download at a time — a second request gets 409 rather than clobbering the
# shared output. _current_proc holds the running go-pmtiles Popen, for cancel.
_extract_lock = threading.Lock()
_current_proc = None


def _source():
    return mapctl.default_source()


@bp.get("/api/maps")
def maps_inventory():
    states = sorted(mapctl.load_states(STATE_DIR, states_geojson=STATES_JSON).keys())
    present = [m["name"] for m in mapctl.available_maps(STATE_DIR)]
    return jsonify({
        "states": states,
        "present": present,
        "source": _source(),
        "pmtiles_available": mapctl.resolve_pmtiles(STATE_DIR) is not None,
        "extracting": _extract_lock.locked(),
    })


@bp.post("/api/maps/extract")
def maps_extract():
    global _current_proc
    state = (request.get_json(silent=True) or {}).get("state")

    # Pre-checks so callers get a proper 400/409 (can't set status mid-stream).
    # First download self-provisions: if the go-pmtiles binary is missing we
    # install it in-stream (below) — unless there's no prebuilt for this platform,
    # in which case there's nothing to do and we fail up front.
    need_install = mapctl.resolve_pmtiles(STATE_DIR) is None
    if need_install and mapctl.pmtiles_asset() is None:
        return jsonify({"error": "map downloader not installed and no prebuilt "
                        "go-pmtiles for this platform — install it manually"}), 400
    if state not in mapctl.load_states(STATE_DIR, states_geojson=STATES_JSON):
        return jsonify({"error": "unknown state: %r" % state}), 400
    if not _extract_lock.acquire(blocking=False):
        return jsonify({"error": "a download is already running"}), 409

    os.makedirs(STATE_DIR, exist_ok=True)

    def _capture(proc):
        global _current_proc
        _current_proc = proc

    def run():
        global _current_proc
        try:
            if need_install:                    # first-ever download: fetch go-pmtiles first
                yield "[installing the map downloader (go-pmtiles)...]\n"
                for line in mapctl.install_pmtiles(STATE_DIR):
                    yield line
                if mapctl.resolve_pmtiles(STATE_DIR) is None:
                    yield "\n[extract failed] could not install the map downloader\n"
                    return
                yield "\n"
            for line in mapctl.extract(STATE_DIR, name=state, state=state,
                                       source=_source(), states_geojson=STATES_JSON,
                                       on_start=_capture):
                yield line
        except Exception as exc:                # install (urllib) or extract failure -> the log
            yield "\n[extract failed] %s\n" % exc
        finally:
            _current_proc = None
            _extract_lock.release()

    return Response(stream_with_context(run()), mimetype="text/plain")


@bp.post("/api/maps/extract/cancel")
def maps_extract_cancel():
    """Terminate the running go-pmtiles subprocess (the stream then ends with a
    non-zero '[extract failed]' and mapctl.extract drops the half-written
    archive as its run unwinds — no partial is left on disk)."""
    proc = _current_proc
    if proc is None:
        return jsonify({"cancelled": False, "reason": "nothing running"})
    try:
        proc.terminate()
    except Exception:
        pass
    return jsonify({"cancelled": True})
