"""
Files/forms route blueprint — the suite-root directory browser, CHIRP CSV
save/list, and the client-data backup store (ICS forms + net log) written under
static/<kind>/saved/. Extracted from server/app.py in the blueprint split; the
ICS-205 URLs are unchanged (they now delegate to the shared form store).
"""

import os

from flask import Blueprint, jsonify, request

import appconfig
from common.api_shape import clamp_limit
from common.web_guard import require_oasis_request

SUITE_ROOT = appconfig.SUITE_ROOT

bp = Blueprint("files", __name__)

# §4 bound for saved-snapshot listings. These are operator files and DO grow,
# unlike a hardware list — this is a real bound, not a formality.
_FILE_LIMIT = 500


# ── Client-data backup ("save to server") ────────────────────────────────────
# The ICS forms and the net-check-in log otherwise live only in browser
# localStorage — a cleared cache, a swapped tablet, or a dead client device
# loses operational records mid-incident, the exact scenario OASIS exists for.
# Each kind persists JSON under static/<kind>/saved/ (served back as a static
# asset for restore). The kind is whitelisted so the endpoint can never be
# pointed at an arbitrary directory. No DB — same flat-file model as
# station.json / warnings.json.
FORM_KINDS = frozenset({"ics-205", "ics-213", "ics-214", "ics-309", "net-log"})

# Extensions the form store will write and enumerate. .json is the snapshot
# format (save/restore a whole form); .csv is the interchange export, which now
# lands in the same designated folder instead of the operator's own machine.
FORM_EXTS = (".json", ".csv")


def _form_saved_dir(kind):
    """Absolute path to static/<kind>/saved/ for a whitelisted kind, else None."""
    if kind not in FORM_KINDS:
        return None
    return os.path.join(SUITE_ROOT, "static", kind, "saved")


def _save_form_json(kind, filename, content):
    """Validate kind + filename and write the snapshot under the kind's saved/
    dir. Returns (payload, http_status)."""
    saved_dir = _form_saved_dir(kind)
    if saved_dir is None:
        return {"ok": False, "error": "Unknown form kind", "code": "UNKNOWN_FORM_KIND"}, 400
    filename = (filename or "").strip()
    # No path traversal, and only the extensions we own — same rule as the
    # original save-ics205, widened from .json-only to cover the CSV export.
    if (not filename or os.sep in filename or "/" in filename
            or not filename.endswith(FORM_EXTS)):
        return {"ok": False, "error": "Invalid filename", "code": "INVALID_FILENAME"}, 400
    os.makedirs(saved_dir, exist_ok=True)
    dest = os.path.realpath(os.path.join(saved_dir, filename))
    if not dest.startswith(os.path.realpath(saved_dir) + os.sep):
        return {"ok": False, "error": "Path traversal rejected", "code": "PATH_TRAVERSAL"}, 403
    try:
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else "")
    except OSError as exc:
        return {"ok": False, "error": str(exc), "code": "FORM_WRITE_FAILED"}, 500
    return {"ok": True, "saved": os.path.join("static", kind, "saved", filename)}, 200


def _list_form_json(kind, ext=None):
    """Newest-first listing of saved files under the kind's saved/ dir.

    ext filters to a single extension ("json" or "csv") so one designated folder
    can back two pickers — Restore lists snapshots, Import CSV lists exports.
    It defaults to .json, which is what every existing caller means by "saved
    snapshot"; an unknown ext is rejected rather than silently listing
    everything. Returns (payload, http_status). Files are fetched directly as
    static assets (/static/<kind>/saved/<name>); this only enumerates them."""
    saved_dir = _form_saved_dir(kind)
    if saved_dir is None:
        return {"ok": False, "error": "Unknown form kind", "code": "UNKNOWN_FORM_KIND"}, 400
    wanted = ("." + str(ext or "json").strip().lstrip(".").lower(),)
    if wanted[0] not in FORM_EXTS:
        return {"ok": False, "error": "Unknown extension",
                "code": "UNKNOWN_FORM_EXT"}, 400
    files = []
    if os.path.isdir(saved_dir):
        for name in os.listdir(saved_dir):
            if not name.endswith(wanted):
                continue
            try:
                st = os.stat(os.path.join(saved_dir, name))
            except OSError:
                continue
            files.append({"name": name, "size": st.st_size, "mtime": int(st.st_mtime)})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return {"ok": True, "files": files}, 200


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
@require_oasis_request
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


@bp.route("/api/forms/save", methods=["POST"])
@require_oasis_request
def api_forms_save():
    """Save a client form/log snapshot as JSON under static/<kind>/saved/ so it
    survives a cleared browser cache or a swapped device.

    Body: { "kind": "<whitelisted kind>", "filename": "<...>.json",
            "content": "<json text>" }.
    """
    data = request.get_json(silent=True) or {}
    result, status = _save_form_json(
        data.get("kind"), data.get("filename"), data.get("content", ""))
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["error"],
                        "code": result["code"]}), status
    return jsonify({"ok": True, "saved": result["saved"]}), status


@bp.route("/api/forms/list")
def api_forms_list():
    """List saved files for ?kind=<kind>, newest first. ?ext=json (default) lists
    form snapshots; ?ext=csv lists CSV exports from the same designated folder.
    The files themselves are fetched directly as static assets
    (/static/<kind>/saved/<name>)."""
    result, status = _list_form_json(request.args.get("kind"),
                                     request.args.get("ext"))
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["error"],
                        "code": result["code"]}), status
    files = result["files"]
    limit = clamp_limit(request.args.get("limit"), _FILE_LIMIT, 2000)
    shown = files[:limit]
    return jsonify({"ok": True, "files": shown, "total": len(files),
                    "count": len(shown),
                    "truncated": len(files) > len(shown), "limit": limit}), status


@bp.route("/api/save-ics205", methods=["POST"])
@require_oasis_request
def api_save_ics205():
    """Back-compat alias for the ICS-205 page — delegates to the shared form
    store (static/ics-205/saved/). Body: { filename, content }."""
    data = request.get_json(silent=True) or {}
    result, status = _save_form_json("ics-205", data.get("filename"),
                                     data.get("content", ""))
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["error"],
                        "code": result["code"]}), status
    return jsonify({"ok": True, "saved": result["saved"]}), status


@bp.route("/api/list-ics205")
def api_list_ics205():
    """Back-compat alias for the ICS-205 page — delegates to the shared form
    store. Files are fetched as static assets (/static/ics-205/saved/<name>)."""
    result, status = _list_form_json("ics-205")
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["error"],
                        "code": result["code"]}), status
    files = result["files"]
    limit = clamp_limit(request.args.get("limit"), _FILE_LIMIT, 2000)
    shown = files[:limit]
    return jsonify({"ok": True, "files": shown, "total": len(files),
                    "count": len(shown),
                    "truncated": len(files) > len(shown), "limit": limit}), status


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
    limit = clamp_limit(request.args.get("limit"), _FILE_LIMIT, 2000)
    shown = files[:limit]
    return jsonify({"ok": True, "files": shown, "total": len(files),
                    "count": len(shown),
                    "truncated": len(files) > len(shown), "limit": limit})


