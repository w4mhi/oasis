"""
FCC callsign-lookup route blueprint — the lookup page, the /api/lookup*
binary-search endpoints, and the /health index report. Extracted verbatim
from server/app.py in the blueprint split; URLs unchanged.
"""

import os

from flask import Blueprint, jsonify, request, send_from_directory

import appconfig
from common import lookup

FCC_DIR = os.path.join(appconfig.SUITE_ROOT, "services", "fcc_database", "static")

bp = Blueprint("fcc", __name__)

# Load the small ZIP -> lat/long table once at startup and reuse it for every
# request. It is only a few MB, well within the Pi's memory budget.
ZIP_TABLE = lookup.load_zip_table()

@bp.route("/lookup")
def lookup_page():
    """Serve the FCC call-sign lookup page from the service-owned FCC package."""
    return send_from_directory(FCC_DIR, "lookup.html")


@bp.route("/api/lookup")
def api_lookup():
    """
    JSON lookup endpoint. Query string: ?callsign=N0CALL
    Exact match.  A trailing '*' triggers prefix/wildcard search instead
    (e.g. ?callsign=W4* returns up to 50 active licenses starting with W4).
    """
    callsign = (request.args.get("callsign") or "").strip()
    if not callsign:
        return jsonify({"ok": False, "error": "Please enter a call sign."}), 400

    # Wildcard / prefix search when the query ends with '*'.
    if callsign.endswith("*"):
        prefix = callsign.rstrip("*").strip()
        if not prefix:
            return jsonify({"ok": False, "error": "Please enter a call sign prefix."}), 400
        if len(prefix) < 2:
            return jsonify({"ok": False, "error": "Prefix must be at least 2 characters."}), 400
        try:
            results = lookup.lookup_prefix(prefix, ZIP_TABLE)
        except FileNotFoundError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        return jsonify({"ok": True, "prefix": True, "query": prefix.upper(),
                        "count": len(results), "results": results})

    # Exact match.
    try:
        result = lookup.lookup(callsign, ZIP_TABLE)
    except FileNotFoundError as exc:
        # Index/data not present yet.
        return jsonify({"ok": False, "error": str(exc)}), 503

    if result is None:
        return jsonify({
            "ok": True,
            "found": False,
            "callsign": callsign.upper(),
        })

    return jsonify({"ok": True, "found": True, "result": result})


@bp.route("/api/lookup/prefix")
def api_lookup_prefix():
    """
    Prefix / wildcard callsign search.  Query: ?callsign=W7*  or  ?callsign=W7
    Returns up to 50 matching active-license records sorted by call sign.
    A trailing '*' in the query string is optional but accepted.
    """
    raw_qs = (request.args.get("callsign") or "").strip()
    prefix = raw_qs.rstrip("*").strip()
    if not prefix:
        return jsonify({"ok": False, "error": "Please enter a call sign prefix."}), 400
    if len(prefix) < 2:
        return jsonify({"ok": False, "error": "Prefix must be at least 2 characters."}), 400

    try:
        results = lookup.lookup_prefix(prefix, ZIP_TABLE)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    return jsonify({"ok": True, "prefix": prefix.upper(), "count": len(results), "results": results})


@bp.route("/api/lookup/name")
def api_lookup_name():
    """
    Search FCC licenses by last name (required) and optional first name prefix.
    Query: ?last=SMITH  or  ?last=SMITH&first=JOHN
    Returns up to 50 active-license records sorted by last name then first name.
    Requires EN_name.idx (built by services/fcc_database/install.py).
    """
    last  = (request.args.get("last")  or "").strip()
    first = (request.args.get("first") or "").strip()
    if not last:
        return jsonify({"ok": False, "error": "Please enter a last name."}), 400
    if len(last) < 2:
        return jsonify({"ok": False, "error": "Last name must be at least 2 characters."}), 400
    try:
        results = lookup.lookup_by_name(last, first or None, zip_table=ZIP_TABLE)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True, "count": len(results), "results": results,
                    "query": {"last": last.upper(), "first": first.upper() or None}})


@bp.route("/api/lookup/grid")
def api_lookup_grid():
    """
    Search FCC licenses by Maidenhead grid square prefix.
    Query: ?grid=CN87  (2, 4, or 6 characters)
    Returns up to 100 active-license records for that grid area.
    Requires EN_grid.idx (built by services/fcc_database/install.py after zipcodes.csv exists).
    """
    grid = (request.args.get("grid") or "").strip()
    if not grid:
        return jsonify({"ok": False, "error": "Please enter a grid square (e.g. CN87)."}), 400
    if len(grid) < 2:
        return jsonify({"ok": False, "error": "Grid prefix must be at least 2 characters."}), 400
    try:
        results = lookup.lookup_by_grid(grid, zip_table=ZIP_TABLE)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True, "count": len(results), "results": results,
                    "query": {"grid": grid.upper()}})


@bp.route("/health")
def health():
    """Simple health check; also reports whether the index is present."""
    index_present = os.path.exists(lookup.INDEX_PATH)
    callsign_count = 0
    if index_present:
        try:
            with open(lookup.INDEX_PATH, "rb") as f:
                callsign_count = sum(1 for _ in f)
        except OSError:
            callsign_count = 0
    return jsonify({
        "ok": True,
        "index_present": index_present,
        "zip_entries": len(ZIP_TABLE),
        "callsign_count": callsign_count,
        "name_index_present": os.path.exists(lookup.NAME_IDX_PATH),
        "grid_index_present": os.path.exists(lookup.GRID_IDX_PATH),
    })


