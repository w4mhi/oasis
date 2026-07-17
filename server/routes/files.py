"""
Files/forms route blueprint — the suite-root directory browser plus CHIRP CSV
and ICS-205 save/list endpoints (written under static/). Extracted verbatim
from server/app.py in the blueprint split; URLs unchanged.
"""

import os

from flask import Blueprint, jsonify, request

import appconfig

SUITE_ROOT = appconfig.SUITE_ROOT

bp = Blueprint("files", __name__)


@bp.route("/api/browse")
def api_browse():
    """
    Directory browser endpoint.
    Query string: ?path=relative/path
    Returns a JSON listing of files and sub-folders within SUITE_ROOT.
    Path traversal outside SUITE_ROOT is rejected with 403.
    """
    rel = (request.args.get("path") or "").strip().lstrip("/")
    root   = os.path.realpath(SUITE_ROOT)
    target = os.path.realpath(os.path.join(SUITE_ROOT, rel))

    # Security: reject anything that escapes the suite root. commonpath avoids
    # the prefix-match pitfall where a sibling dir (e.g. "oasis-emcomm-evil")
    # would pass a naive startswith() check.
    if target != root and os.path.commonpath([root, target]) != root:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    if not os.path.isdir(target):
        return jsonify({"ok": False, "error": "Not a directory"}), 404

    # Hidden entries and well-known internal directories to suppress.
    _HIDDEN = frozenset({".git", ".venv", ".env", "__pycache__", "node_modules", "wheels"})

    def _visible(name: str) -> bool:
        return not name.startswith(".") and name not in _HIDDEN

    entries = []
    try:
        for name in sorted(
            (n for n in os.listdir(target) if _visible(n)),
            key=lambda n: (not os.path.isdir(os.path.join(target, n)), n.lower()),
        ):
            full = os.path.join(target, name)
            stat = os.stat(full)
            entries.append({
                "name": name,
                "type": "dir" if os.path.isdir(full) else "file",
                "size": stat.st_size if os.path.isfile(full) else None,
                "modified": int(stat.st_mtime),
            })
    except PermissionError:
        return jsonify({"ok": False, "error": "Permission denied"}), 403

    return jsonify({"ok": True, "path": rel, "entries": entries})


@bp.route("/api/save-chirp", methods=["POST"])
def api_save_chirp():
    """Save a CHIRP CSV file directly into the suite's static/chirp/ folder.

    Expects JSON body: { "filename": "<datetime>_repeaters.csv", "content": "<csv text>" }
    Only filenames ending in .csv and containing no path separators are accepted.
    """
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    content  = data.get("content", "")

    # Validate filename — no path traversal, .csv only
    if not filename or os.sep in filename or "/" in filename or not filename.endswith(".csv"):
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    chirp_dir = os.path.join(SUITE_ROOT, "static", "chirp")
    os.makedirs(chirp_dir, exist_ok=True)
    dest = os.path.realpath(os.path.join(chirp_dir, filename))
    if not dest.startswith(os.path.realpath(chirp_dir) + os.sep):
        return jsonify({"ok": False, "error": "Path traversal rejected"}), 403

    try:
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "saved": os.path.join("static", "chirp", filename)})


@bp.route("/api/save-ics205", methods=["POST"])
def api_save_ics205():
    """Save an ICS-205 plan as JSON into static/ics-205/saved/.

    Expects JSON body: { "filename": "ics-205-<...>.json", "content": "<json text>" }
    Only filenames ending in .json and containing no path separators are accepted.
    """
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    content  = data.get("content", "")

    # Validate filename — no path traversal, .json only
    if not filename or os.sep in filename or "/" in filename or not filename.endswith(".json"):
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    saved_dir = os.path.join(SUITE_ROOT, "static", "ics-205", "saved")
    os.makedirs(saved_dir, exist_ok=True)
    dest = os.path.realpath(os.path.join(saved_dir, filename))
    if not dest.startswith(os.path.realpath(saved_dir) + os.sep):
        return jsonify({"ok": False, "error": "Path traversal rejected"}), 403

    try:
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "saved": os.path.join("static", "ics-205", "saved", filename)})


@bp.route("/api/list-ics205")
def api_list_ics205():
    """List saved ICS-205 plans in static/ics-205/saved/, newest first.

    The files themselves are fetched directly as static assets
    (/static/ics-205/saved/<name>); this only enumerates them.
    """
    saved_dir = os.path.join(SUITE_ROOT, "static", "ics-205", "saved")
    files = []
    if os.path.isdir(saved_dir):
        for name in os.listdir(saved_dir):
            if not name.endswith(".json"):
                continue
            try:
                st = os.stat(os.path.join(saved_dir, name))
            except OSError:
                continue
            files.append({"name": name, "size": st.st_size, "mtime": int(st.st_mtime)})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify({"ok": True, "files": files})


@bp.route("/api/list-chirp")
def api_list_chirp():
    """List CHIRP frequency-plan CSVs in static/chirp/, newest first.

    The files themselves are fetched directly as static assets
    (/static/chirp/<name>); this only enumerates them.
    """
    chirp_dir = os.path.join(SUITE_ROOT, "static", "chirp")
    files = []
    if os.path.isdir(chirp_dir):
        for name in os.listdir(chirp_dir):
            if not name.endswith(".csv"):
                continue
            try:
                st = os.stat(os.path.join(chirp_dir, name))
            except OSError:
                continue
            files.append({"name": name, "size": st.st_size, "mtime": int(st.st_mtime)})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify({"ok": True, "files": files})


