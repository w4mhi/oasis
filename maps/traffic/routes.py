"""
Map app blueprint — the allowlisted filesystem PMTiles browser (/api/fs/*) for
loading .pmtiles off USB sticks / external mounts at runtime.

Everything else the traffic map needs — the page (maps/traffic/map.html), its
warnings catalog, the render engine (maps/mapengine/), the sprite assets
(maps/traffic/assets/) and the bundled tiles (maps/tiles/) — is a plain file
under maps/ and is served by the root static handler (static_folder=SUITE_ROOT),
so no static route lives here anymore.
"""

import os

from flask import Blueprint, jsonify, request, send_file

import appconfig

SUITE_ROOT = appconfig.SUITE_ROOT
MAPS_DIR   = os.path.join(SUITE_ROOT, "maps")

bp = Blueprint("map", __name__)

# Roots the filesystem map browser (/api/fs/*) may read .pmtiles archives from.
# Lets an operator load maps off a USB stick or other mount at runtime without
# staging them into the repo. Override with OASIS_MAP_ROOTS (os.pathsep-separated).
# Defaults cover removable-media mounts on Pi OS, macOS volumes, the GrayWolf
# offline-tiles directory, and always the suite's own maps/ directory.
_map_roots_env = os.environ.get("OASIS_MAP_ROOTS")
MAP_ROOTS = [
    os.path.realpath(p)
    for p in (_map_roots_env.split(os.pathsep) if _map_roots_env
              else ["/media", "/mnt", "/run/media", "/Volumes",
                    "/var/lib/graywolf/tiles", MAPS_DIR])
    if p.strip()
]


# ── Filesystem map browser (PMTiles on USB / external mounts) ─────────────────

def _within_map_roots(abs_path):
    """True if abs_path resolves inside one of the allowlisted MAP_ROOTS."""
    rp = os.path.realpath(abs_path)
    for root in MAP_ROOTS:
        try:
            if rp == root or os.path.commonpath([root, rp]) == root:
                return True
        except ValueError:
            continue  # different drive / un-comparable paths
    return False


@bp.route("/api/fs/browse")
def api_fs_browse():
    """
    Browse the filesystem for .pmtiles archives, restricted to MAP_ROOTS.
    Query string: ?path=<absolute-path>
    With no path, returns the configured roots that currently exist so the UI
    has starting points. With a path, lists sub-directories and *.pmtiles files.
    """
    raw = (request.args.get("path") or "").strip()

    # No path → offer the allowed roots that actually exist.
    if not raw:
        roots = [{"name": r, "path": r, "type": "dir"}
                 for r in MAP_ROOTS if os.path.isdir(r)]
        return jsonify({"ok": True, "path": "", "parent": None, "roots": True, "entries": roots})

    target = os.path.realpath(raw)
    if not _within_map_roots(target):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    if not os.path.isdir(target):
        return jsonify({"ok": False, "error": "Not a directory"}), 404

    # Offer a parent link, but never let it climb above an allowed root.
    parent = os.path.dirname(target)
    if parent == target or not _within_map_roots(parent):
        parent = None

    entries = []
    try:
        for name in sorted(
            (n for n in os.listdir(target) if not n.startswith(".")),
            key=lambda n: (not os.path.isdir(os.path.join(target, n)), n.lower()),
        ):
            full = os.path.join(target, name)
            if os.path.isdir(full):
                entries.append({"name": name, "path": full, "type": "dir"})
            elif name.endswith(".pmtiles"):
                entries.append({"name": name, "path": full, "type": "file",
                                "size": os.path.getsize(full)})
    except PermissionError:
        return jsonify({"ok": False, "error": "Permission denied"}), 403

    return jsonify({"ok": True, "path": target, "parent": parent, "roots": False, "entries": entries})


@bp.route("/api/fs/pmtiles")
def api_fs_pmtiles():
    """
    Stream a .pmtiles archive from an allowlisted absolute path, with HTTP Range
    support so the client-side PMTiles protocol can read it incrementally.
    Query string: ?path=<absolute-path>
    """
    from flask import abort
    raw = (request.args.get("path") or "").strip()
    target = os.path.realpath(raw) if raw else ""

    if not raw or not target.endswith(".pmtiles") or not _within_map_roots(target):
        abort(403)
    if not os.path.isfile(target):
        abort(404)

    # conditional=True → Werkzeug honours Range/If-Range and returns 206 with
    # Accept-Ranges, streaming the file rather than loading it into memory.
    # Same-origin map UI only — no Access-Control-Allow-Origin.
    return send_file(target, mimetype="application/octet-stream", conditional=True)
