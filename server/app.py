#!/usr/bin/env python3
"""
app.py
------
Off-grid Flask web server for OASIS - Off-grid Amateur Station Information Suite.

Serves the main index.html and all suite static files at the root.
The FCC amateur-radio call-sign lookup is available at /lookup.

Designed to run on a Raspberry Pi Zero 2 W with no internet connection.
All FCC data is served from local flat files (EN.dat + EN.idx + zipcodes.csv);
no database engine is involved.

Run (development):   python3 app.py
Run (recommended):   see fcc-offline-database/README.md for the gunicorn + systemd setup.
"""

import argparse
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory, Response

import lookup

# Absolute path to the suite root (one level up from this file).
SUITE_ROOT  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAP_ASSETS  = os.path.join(os.path.dirname(__file__), "map-assets")
MAPS_DIR    = os.path.join(SUITE_ROOT, "maps")

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

# Serve the suite root as the static folder so all existing relative links
# in index.html (antenna-calc.html, ics-205/, etc.) keep working as-is.
app = Flask(__name__, static_folder=SUITE_ROOT, static_url_path="")

# Load the small ZIP -> lat/long table once at startup and reuse it for every
# request. It is only a few MB, well within the Pi's memory budget.
ZIP_TABLE = lookup.load_zip_table()

# CORS is intentionally NOT applied globally.  All HTML is served from this
# same Flask instance (same origin), so cross-origin headers are unnecessary.
# Individual routes that legitimately need cross-origin access add them below.

@app.route("/")
def index():
    """Smart home: JS reads localStorage and redirects to index7.html or index.html."""
    return '''<!doctype html><meta charset="utf-8">
<script>
window.location.replace(
  localStorage.getItem("oasis_layout") === "7inch" ? "/index7.html" : "/index.html"
);
</script>
<noscript><meta http-equiv="refresh" content="0;url=/index.html"></noscript>''', 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route("/map-assets/<path:filename>")
def map_assets(filename):
    """Serve MapLibre GL libraries from server/map-assets/."""
    return send_from_directory(MAP_ASSETS, filename)


@app.route("/maps/<filename>")
def serve_map(filename):
    """
    Serve static files from the maps/ directory (HTML, GeoJSON, .pmtiles, etc.).
    PMTiles archives are read client-side via HTTP range requests; send_file
    (conditional) is used so those Range requests are honoured.
    """
    filepath = os.path.join(MAPS_DIR, filename)
    if not os.path.isfile(filepath):
        from flask import abort
        abort(404)
    if filename.endswith(".pmtiles"):
        resp = send_file(filepath, mimetype="application/octet-stream", conditional=True)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    return send_from_directory(MAPS_DIR, filename)


@app.route("/lookup")
def lookup_page():
    """Serve the FCC call-sign lookup page."""
    return render_template("lookup.html")


@app.route("/api/lookup")
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


@app.route("/api/lookup/prefix")
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


@app.route("/api/browse")
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


@app.route("/api/fs/browse")
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


@app.route("/api/fs/pmtiles")
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
    resp = send_file(target, mimetype="application/octet-stream", conditional=True)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/save-chirp", methods=["POST"])
def api_save_chirp():
    """Save a CHIRP CSV file directly into the suite's chirp/ folder.

    Expects JSON body: { "filename": "<datetime>_repeaters.csv", "content": "<csv text>" }
    Only filenames ending in .csv and containing no path separators are accepted.
    """
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    content  = data.get("content", "")

    # Validate filename — no path traversal, .csv only
    if not filename or os.sep in filename or "/" in filename or not filename.endswith(".csv"):
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    chirp_dir = os.path.join(SUITE_ROOT, "chirp")
    os.makedirs(chirp_dir, exist_ok=True)
    dest = os.path.realpath(os.path.join(chirp_dir, filename))
    if not dest.startswith(os.path.realpath(chirp_dir) + os.sep):
        return jsonify({"ok": False, "error": "Path traversal rejected"}), 403

    try:
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "saved": os.path.join("chirp", filename)})


@app.route("/health")
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
    })


@app.route("/api/health/probe")
def api_health_probe():
    """Proxy health check for external services.
    Accepts ?service=graywolf|kiwix|webssh&port=N.
    Makes a real server-side HTTP GET so the browser receives an actual
    200/non-200 distinction rather than an opaque no-cors response."""
    import urllib.request
    import urllib.error

    service = request.args.get("service", "")
    try:
        port = int(request.args.get("port", "0"))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid port"}), 400

    ALLOWED = {"graywolf", "kiwix", "webssh", "aprs_api", "winlink"}
    if service not in ALLOWED:
        return jsonify({"ok": False, "error": "unknown service"}), 400
    if not (1 <= port <= 65535):
        return jsonify({"ok": False, "error": "port out of range"}), 400

    url = f"http://127.0.0.1:{port}/"
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "OASIS-HealthProbe/1.0")
        with urllib.request.urlopen(req, timeout=3) as resp:
            status = resp.status
        return jsonify({"ok": True, "service": service, "port": port, "status": status})
    except urllib.error.HTTPError as e:
        # Service replied with an HTTP error — it IS running, just returning
        # an error code (e.g. 404 on / is still "up" for our purposes).
        return jsonify({"ok": True, "service": service, "port": port, "status": e.code})
    except Exception as e:
        return jsonify({"ok": False, "service": service, "port": port, "error": str(e)})


@app.route("/api/health/binary")
def api_health_binary():
    """Check whether a named system binary is installed.
    Accepts ?name=rtl_test|rtl_fm|socat|tcpdump|pat|ttyd|kiwix-serve|...
    Only alphanumeric names with hyphens/underscores are accepted.

    Looks on PATH first, then falls back to the standard bin dirs — the WSGI
    server can run under a trimmed systemd PATH that omits /usr/bin, which made
    installed tools (e.g. rtl_test) look missing."""
    import shutil
    name = request.args.get("name", "")
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$', name):
        return jsonify({"ok": False, "error": "invalid binary name"}), 400
    path = shutil.which(name)
    if not path:
        for d in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
                  "/usr/bin", "/sbin", "/bin"):
            cand = os.path.join(d, name)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                path = cand
                break
    return jsonify({"ok": bool(path), "binary": name, "path": path})


# Known OASIS systemd units (install/enable scripts create these). Allowlisted
# so the status check can never run systemctl against an arbitrary unit name.
_OASIS_SERVICES = {
    "graywolf", "graywolf-api", "pat", "kiwix", "webssh", "aprs-sdr-feed",
    "openwebrx", "oasis",
}

# Units the dashboard may start/stop/restart. Everything in _OASIS_SERVICES
# EXCEPT "oasis" itself — stopping the web server would kill the dashboard with
# no way to bring it back from the browser.
_CONTROLLABLE_SERVICES = _OASIS_SERVICES - {"oasis"}
_SERVICE_ACTIONS = {"start", "stop", "restart"}

# Hardware-exclusivity: starting the key stops the listed services first, since
# they can't share the radio at once. OpenWebRX grabs the RTL-SDR, which the APRS
# SDR feed pipes into GrayWolf — so bringing up OpenWebRX takes both down.
_SERVICE_CONFLICTS = {
    "openwebrx": ["aprs-sdr-feed", "graywolf"],
}

# Units whose boot state tracks their running state: starting also `enable`s them
# (comes back after reboot), stopping also `disable`s them (stays off). Everything
# else is transient (plain start/stop; boot state left untouched). restart never
# changes boot state.
_PERSIST_BOOT_STATE = {"openwebrx", "kiwix"}


def _systemctl_seq(unit, verbs):
    """Best-effort `sudo -n systemctl <verb> <unit>.service` for each verb in
    order (used for conflict services — failures are tolerated/ignored)."""
    for verb in verbs:
        try:
            subprocess.run(["sudo", "-n", "systemctl", verb, f"{unit}.service"],
                           capture_output=True, text=True, timeout=30)
        except Exception:
            pass


@app.route("/api/health/service")
def api_health_service():
    """Report systemd status for a known OASIS service (Linux only).
    Accepts ?name=graywolf|graywolf-api|pat|kiwix|webssh|aprs-sdr-feed|oasis."""
    import subprocess
    import sys as _sys

    name = request.args.get("name", "")
    if name not in _OASIS_SERVICES:
        return jsonify({"ok": False, "error": "unknown service"}), 400
    if _sys.platform != "linux":
        return jsonify({"ok": False, "supported": False,
                        "service": name, "error": "systemd not available"})

    def _q(verb):
        try:
            r = subprocess.run(["systemctl", verb, f"{name}.service"],
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip()
        except Exception:
            return ""

    active  = _q("is-active")     # active | inactive | failed | activating | ""
    enabled = _q("is-enabled")    # enabled | disabled | static | "" (not installed)
    return jsonify({
        "ok":        active == "active",
        "service":   name,
        "active":    active or "unknown",
        "enabled":   enabled or "not-found",
        "installed": bool(enabled),     # is-enabled prints nothing for absent units
    })


@app.route("/api/service", methods=["POST"])
def api_service():
    """Start / stop / restart a known OASIS service (Linux only).

    Body (JSON): {"unit": "<name>", "action": "start|stop|restart"}.

    Authorization is OS-side: scripts/enable-service-controls.py installs a narrow
    sudoers NOPASSWD rule scoped to exactly these units + actions, so no credential
    ever touches this layer. CSRF is blocked by requiring a custom header that a
    cross-origin page cannot set without a preflight this endpoint never grants.
    """
    import subprocess
    import sys as _sys

    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403

    data   = request.get_json(silent=True) or {}
    unit   = (data.get("unit") or "").strip()
    action = (data.get("action") or "").strip()

    if unit not in _CONTROLLABLE_SERVICES:
        return jsonify({"ok": False, "error": "unknown or protected service"}), 403
    if action not in _SERVICE_ACTIONS:
        return jsonify({"ok": False, "error": "invalid action"}), 400
    if _sys.platform != "linux":
        return jsonify({"ok": False, "supported": False,
                        "error": "systemd not available"}), 200

    # Hardware-exclusivity (symmetric): a unit's conflicts are taken fully DOWN
    # (stop + disable) when it starts, and fully RESTORED (enable + start) when it
    # stops — so exactly one consumer of the radio survives a reboot. `affected`
    # tells the UI which other cards to refresh. (restart leaves conflicts alone.)
    affected = []
    for other in _SERVICE_CONFLICTS.get(unit, []):
        if action == "start":
            _systemctl_seq(other, ["stop", "disable"])
            affected.append(other)
        elif action == "stop":
            _systemctl_seq(other, ["enable", "start"])
            affected.append(other)

    # Build the systemctl step(s). Boot-state-tracking units enable-on-start /
    # disable-on-stop; the `action` verb is the one whose success we report on.
    persist = unit in _PERSIST_BOOT_STATE
    if action == "start":
        steps = ["enable", "start"] if persist else ["start"]
    elif action == "stop":
        steps = ["stop", "disable"] if persist else ["stop"]
    else:
        steps = ["restart"]

    result = None
    try:
        for verb in steps:
            r = subprocess.run(["sudo", "-n", "systemctl", verb, f"{unit}.service"],
                               capture_output=True, text=True, timeout=30)
            if verb == action:        # the primary verb (start/stop/restart)
                result = r
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "affected": affected}), 500

    # Re-query state regardless of rc so the UI can refresh from the truth.
    try:
        active = subprocess.run(["systemctl", "is-active", f"{unit}.service"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        active = "unknown"

    if result is None or result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() if result else "no command run"
        low = err.lower()
        if "password" in low or "a terminal is required" in low or "not allowed" in low:
            err += " — run: python3 scripts/enable-service-controls.py (grants permission)"
        return jsonify({"ok": False, "service": unit, "action": action,
                        "active": active or "unknown", "affected": affected,
                        "error": err or "systemctl failed"}), 500

    return jsonify({"ok": True, "service": unit, "action": action,
                    "active": active or "unknown", "affected": affected})


@app.route("/api/health/zim")
def api_health_zim():
    """Offline Wikipedia/ZIM content presence for the dashboard Wikipedia card.
    Kiwix content lives OUTSIDE the suite root (~/oasis-offline/zim by default,
    or an SSD), so /api/browse can't see it — scan the standard locations here."""
    candidates = [
        os.path.expanduser("~/oasis-offline/zim"),
        "/mnt/ssd/zim",
        "/mnt/ssd/Documents/reference/zim",
    ]
    for d in candidates:
        try:
            zims = [f for f in os.listdir(d) if f.endswith(".zim")]
        except OSError:
            continue
        if zims:
            total = 0
            for f in zims:
                try:
                    total += os.path.getsize(os.path.join(d, f))
                except OSError:
                    pass
            return jsonify({"ok": True, "count": len(zims), "dir": d,
                            "total_gb": round(total / 1e9, 1),
                            "names": [os.path.splitext(f)[0] for f in sorted(zims)]})
    return jsonify({"ok": True, "count": 0, "dir": candidates[0],
                    "total_gb": 0, "names": []})


# Fixed config artifacts written by the install/enable scripts. Keys map to
# server-side absolute paths — no arbitrary path input is accepted.
def _health_paths():
    return {
        "rtl_blacklist": "/etc/modprobe.d/rtlsdr-blacklist.conf",
        "pat_config":    os.path.join(os.path.expanduser("~"), ".config", "pat", "config.json"),
    }


@app.route("/api/health/file")
def api_health_file():
    """Existence check for a known OASIS config artifact (no arbitrary paths).
    Accepts ?key=rtl_blacklist|pat_config. For pat_config also reports whether
    the callsign and Winlink password are set (booleans only — never values)."""
    key  = request.args.get("key", "")
    path = _health_paths().get(key)
    if not path:
        return jsonify({"ok": False, "error": "unknown key"}), 400

    exists = os.path.isfile(path)
    info = {"ok": exists, "key": key, "exists": exists}
    if key == "pat_config" and exists:
        try:
            import json as _json
            with open(path) as fh:
                cfg = _json.load(fh)
            info["callsign_set"] = bool(cfg.get("mycall"))
            info["password_set"] = bool(cfg.get("secure_login_password"))
        except Exception:
            pass
    return jsonify(info)


@app.route("/api/aprs/health")
def api_aprs_health_proxy():
    """Proxy health check for the graywolf-api (port 8085).
    Same-origin so the browser doesn’t need to make a cross-origin request."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:8085/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False, "graywolf_reachable": False,
                        "error": str(reason)}), 503


@app.route("/api/aprs/stations")
def api_aprs_stations_proxy():
    """Proxy APRS station list from the graywolf-api (port 8085).
    Keeps the browser on the same origin — no cross-origin fetch needed.
    Timeout must exceed graywolf-api's own inner timeout (3 s) + overhead."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:8085/api/aprs/stations"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        # graywolf-api is running but returned an error (e.g. DB not found).
        # Pass through its JSON body verbatim so the UI gets the real message.
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"APRS API unavailable ({reason}). "
                                 "Is the graywolf-api service running?"}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "APRS API timed out — graywolf-api slow or GrayWolf unreachable."}), 503


@app.route("/api/aprs/track")
def api_aprs_track_proxy():
    """Proxy station position history from the graywolf-api (port 8085).
    Forwards ?callsign= and ?minutes= query params verbatim."""
    import urllib.request
    import urllib.error
    import urllib.parse
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    url = f"http://127.0.0.1:8085/api/aprs/track?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"APRS API unavailable ({reason})."}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "APRS API timed out."}), 503


@app.route("/api/server-info")
def server_info():
    """Report which WSGI server is running (gunicorn vs Flask dev server)."""
    import sys
    from importlib.metadata import version as pkg_version, PackageNotFoundError

    def get_ver(pkg):
        try:
            return pkg_version(pkg)
        except PackageNotFoundError:
            return "unknown"

    if "gunicorn" in sys.modules:
        wsgi = "gunicorn"
        wsgi_version = get_ver("gunicorn")
    else:
        wsgi = "werkzeug"
        wsgi_version = get_ver("werkzeug")

    return jsonify({
        "ok":            True,
        "wsgi":          wsgi,
        "wsgi_version":  wsgi_version,
        "flask_version": get_ver("flask"),
        "port":          PORT,
    })


@app.route("/api/config")
def api_config():
    """Return runtime configuration (port, feature flags) so HTML pages need
    not hardcode any values.  The PORT global is updated by __main__ before
    the server starts, so this always reflects the actual listening port."""
    payload = {
        "ok": True,
        "port": PORT,
        "ports": {
            "flask": PORT,
            "graywolf": 8080,
            "kiwix": 8081,
            "aprs_api": 8085,
            "webssh": 7681,
            "winlink": 8082,
            "openwebrx": 8073,
        },
    }
    return jsonify(payload)


@app.route("/server-ports.json")
def server_ports():
    """Alias for /api/config — canonical service-discovery endpoint.
    HTML pages fetch this on load so no port numbers need to be hardcoded."""
    return api_config()


# ── Raspberry Pi power/thermal + Wi-Fi helpers ────────────────────────────────
def _pi_throttled():
    """Pi power/thermal throttling via `vcgencmd get_throttled`. Returns None on
    non-Pi hosts (vcgencmd absent). Bitmask: bits 0-3 = under-voltage / freq
    capped / throttled / soft-temp-limit happening *now*; bits 16-19 = the same
    having *occurred since boot*."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"],
                             capture_output=True, text=True, timeout=2)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or "throttled=" not in out.stdout:
        return None
    try:
        val = int(out.stdout.strip().split("throttled=")[1], 16)
    except (ValueError, IndexError):
        return None
    now  = {"under_voltage": bool(val & 0x1), "freq_capped": bool(val & 0x2),
            "throttled": bool(val & 0x4), "soft_temp": bool(val & 0x8)}
    ever = {"under_voltage": bool(val & 0x10000), "freq_capped": bool(val & 0x20000),
            "throttled": bool(val & 0x40000), "soft_temp": bool(val & 0x80000)}
    return {"raw": hex(val), "now": now, "ever": ever,
            "now_any": any(now.values()), "ever_any": any(ever.values())}


def _wifi_info():
    """Best-effort Wi-Fi SSID + associated-station count (for Pi access-point
    use). Returns None when the tools/interface are absent (e.g. on a Mac)."""
    info = {}
    try:
        out = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            info["ssid"] = out.stdout.strip()
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass
    for iface in ("wlan0", "wlan1"):
        try:
            out = subprocess.run(["iw", "dev", iface, "station", "dump"],
                                 capture_output=True, text=True, timeout=2)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            break  # `iw` not installed — stop trying
        if out.returncode == 0 and "Station " in out.stdout:
            info["clients"] = out.stdout.count("Station ")
            break
    return info or None


# ── Background sampler ─────────────────────────────────────────────────────────
# CPU% over a rolling 2s window, plus slower-changing Pi facts (throttle state,
# Wi-Fi), are measured on a daemon thread and cached. /api/system then never
# spawns a subprocess or blocks in the request path — stable, Pi-friendly, and
# values agree across repeated polls. (Under gunicorn each worker samples its
# own; they track closely since both average the same window.)
_CPU_PCT  = None  # most recent rolling CPU%; None until the first sample lands
_THROTTLE = None  # cached _pi_throttled(); None on non-Pi
_NET      = None  # cached _wifi_info();    None when unavailable

def _sampler():
    try:
        import psutil
    except ImportError:
        psutil = None
    global _CPU_PCT, _THROTTLE, _NET
    if psutil:
        psutil.cpu_percent(interval=None)          # prime the baseline
    i = 0
    while True:
        if psutil:
            _CPU_PCT = psutil.cpu_percent(interval=2.0)  # blocks ~2s
        else:
            time.sleep(2.0)
        if i % 5 == 0:                              # refresh ~every 10s
            _THROTTLE = _pi_throttled()
            _NET      = _wifi_info()
        i += 1

threading.Thread(target=_sampler, name="oasis-sampler", daemon=True).start()


def _lan_ip():
    """Best-effort primary LAN IP. Uses a UDP socket to pick the outbound
    interface — no packets are actually sent and no internet is required."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@app.route("/api/system")
def api_system():
    """System resource stats (CPU, RAM, disk, temp, load, uptime).
    Gracefully degrades on platforms where some metrics are unavailable."""
    try:
        import psutil
    except ImportError:
        return jsonify({"ok": False, "error": "psutil not installed"}), 503

    # Disk — auto-detect: SSD → eMMC → system root
    disk_info = None
    for mount, label in [("/mnt/ssd", "SSD"), ("/mnt/emmc", "eMMC"),
                         ("/", "System"), ("C:\\", "System")]:
        try:
            d = psutil.disk_usage(mount)
            disk_info = {
                "label":    label,
                "total_gb": round(d.total / 1e9, 1),
                "used_gb":  round(d.used  / 1e9, 1),
                "free_gb":  round(d.free  / 1e9, 1),
                "pct":      d.percent,
            }
            break
        except Exception:
            continue
    if disk_info is None:
        disk_info = {"error": "unavailable"}

    # CPU — use the cached rolling sample; fall back to a quick snapshot only
    # during the brief window before the background sampler produces its first.
    cpu_pct   = _CPU_PCT if _CPU_PCT is not None else psutil.cpu_percent(interval=0.1)
    cpu_count = psutil.cpu_count(logical=True) or 1

    # RAM
    ram = psutil.virtual_memory()
    ram_info = {
        "total_mb": round(ram.total     / 1e6, 1),
        "used_mb":  round(ram.used      / 1e6, 1),
        "free_mb":  round(ram.available / 1e6, 1),
        "pct":      ram.percent,
    }

    # Load average (Linux/macOS only)
    load_info = None
    try:
        load1, _, _ = psutil.getloadavg()
        load_info = {
            "avg1":  round(load1, 2),
            "cores": cpu_count,
            "pct":   round((load1 / cpu_count) * 100, 1),
        }
    except AttributeError:
        pass

    # Boot time / uptime
    boot_ts    = psutil.boot_time()
    uptime_sec = int(time.time() - boot_ts)
    boot_str   = time.strftime("%a %b %d %H:%M", time.localtime(boot_ts))

    # CPU temperature (Pi-specific; absent on macOS/Windows)
    temp = None
    try:
        temps = psutil.sensors_temperatures()
        for key in ("cpu_thermal", "cpu-thermal", "soc_thermal", "coretemp"):
            if key in temps and temps[key]:
                temp = round(temps[key][0].current, 1)
                break
    except Exception:
        pass

    # FCC DB last modified
    fcc_db_mtime = None
    fcc_db_candidates = [
        os.path.join(SUITE_ROOT, "fcc-offline-database", "data", "EN.dat"),
        "/mnt/ssd/Documents/reference/fcc-offline-database/data/EN.dat",
    ]
    for path in fcc_db_candidates:
        try:
            fcc_db_mtime = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
            break
        except Exception:
            continue

    return jsonify({
        "ok":          True,
        "hostname":    socket.gethostname(),
        "ip":          _lan_ip(),
        "cpu_pct":     cpu_pct,
        "cpu_count":   cpu_count,
        "cpu_temp_c":  temp,
        "ram":         ram_info,
        "disk":        disk_info,
        "load":        load_info,
        "uptime_sec":  uptime_sec,
        "boot_str":    boot_str,
        "fcc_db_date": fcc_db_mtime,
        "throttle":    _THROTTLE,
        "net":         _NET,
    })


@app.route("/api/audio")
def api_audio():
    """List ALSA sound cards (for choosing a GrayWolf audio device).

    Reads /proc/asound — no extra Python deps. Linux/ALSA only; returns
    supported=False on macOS/Windows so the UI can show 'n/a'.
    Each card reports capture (RX) / playback (TX) capability and the ALSA
    address (hw:N,0) to plug straight into GrayWolf's config.
    """
    import re
    import sys

    # Audio enumeration is Linux/ALSA only (reads /proc/asound). Windows and
    # macOS have no procfs sound nodes, so report unsupported rather than
    # erroring — the cross-platform bundle keeps working and the UI shows "n/a".
    if not sys.platform.startswith("linux") or not os.path.exists("/proc/asound/cards"):
        return jsonify({"ok": True, "supported": False, "cards": []})

    try:
        with open("/proc/asound/cards") as fh:
            text = fh.read()
    except OSError as exc:
        return jsonify({"ok": False, "supported": True, "error": str(exc)}), 503

    def pcm_kinds(n):
        """Return (capture, playback) by inspecting /proc/asound/cardN/pcm*."""
        cap = play = False
        try:
            for entry in os.listdir(f"/proc/asound/card{n}"):
                if re.match(r"pcm\d+c$", entry):
                    cap = True
                elif re.match(r"pcm\d+p$", entry):
                    play = True
        except OSError:
            pass
        return cap, play

    cards = []
    # Line format: " N [id   ]: driver - full name"
    for m in re.finditer(r'^\s*(\d+)\s*\[([^\]]+)\]\s*:\s*(\S+)\s*-\s*(.+?)\s*$',
                         text, re.M):
        idx, cid, driver, name = m.groups()
        n = int(idx)
        cap, play = pcm_kinds(n)
        cards.append({
            "index":    n,
            "id":       cid.strip(),
            "name":     name.strip(),
            "driver":   driver.strip(),
            "capture":  cap,
            "playback": play,
            "usb":      "usb" in (driver + name).lower(),
            "alsa":     f"hw:{n},0",
        })

    # ── RTL-SDR feed service (not an ALSA card; check systemd) ──────────
    # aprs-sdr-feed.service pipes rtl_fm audio to GrayWolf via sdr_udp UDP.
    # If it is active we expose it as a synthetic capture-only card so the UI
    # can show green even when zero ALSA sound cards are attached.
    import subprocess as _sp
    SDR_SERVICE = "aprs-sdr-feed.service"
    try:
        rc = _sp.run(
            ["systemctl", "is-active", "--quiet", SDR_SERVICE],
            timeout=2,
        ).returncode
        if rc == 0:
            cards.append({
                "index":    -1,
                "id":       "sdr",
                "name":     "RTL-SDR (aprs-sdr-feed)",
                "driver":   "rtlsdr",
                "capture":  True,
                "playback": False,
                "usb":      True,
                "alsa":     "sdr_udp",
                "sdr":      True,
            })
    except Exception:
        pass

    return jsonify({"ok": True, "supported": True, "cards": cards})


PORT = int(os.environ.get("OASIS_PORT", "8083"))

def find_free_port(start=8083, end=8093):
    """Return the first TCP port in [start, end] not already in use."""
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"No free port found between {start} and {end}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OASIS Flask server")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a web browser on startup (useful for headless Pi deployments).",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Force the Flask development server even when gunicorn is installed.",
    )
    args = parser.parse_args()

    PORT = find_free_port()
    os.environ["OASIS_PORT"] = str(PORT)   # so the gunicorn-loaded app reports this port
    url = f"http://localhost:{PORT}/"

    # Prefer the gunicorn production server when it's installed — re-exec into it.
    # gunicorn imports this module as 'app', so __main__ never runs under gunicorn
    # (no recursion). Falls back to the Flask dev server if gunicorn is absent or
    # --dev is given. This is why a plain `python app.py` now serves via gunicorn.
    if not args.dev:
        try:
            import gunicorn  # noqa: F401
            _have_gunicorn = True
        except ImportError:
            _have_gunicorn = False
        if _have_gunicorn:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            print(f"\n  OASIS (gunicorn) — {url}\n")
            os.execv(sys.executable, [
                sys.executable, "-m", "gunicorn",
                "--chdir", app_dir,
                "--bind", f"0.0.0.0:{PORT}",
                "--workers", "2",
                "--access-logfile", "-",
                "app:app",
            ])
            # os.execv replaces this process; nothing below runs on success.

    print(f"\n  OASIS (dev server) — {url}\n")
    if not args.no_browser:
        # Open the browser shortly after the server starts.
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # host=0.0.0.0 so other devices on the off-grid network can reach it.
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
