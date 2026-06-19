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
import hashlib
import os
import re
import socket
import threading
import time
import webbrowser

from flask import Flask, jsonify, render_template, request, send_from_directory, Response

import lookup

# Absolute path to the suite root (one level up from this file).
SUITE_ROOT  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAP_ASSETS  = os.path.join(os.path.dirname(__file__), "map-assets")
MAPS_DIR    = os.path.join(SUITE_ROOT, "maps")

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
    """Serve the main suite index page."""
    return app.send_static_file("index.html")


@app.route("/map-assets/<path:filename>")
def map_assets(filename):
    """Serve MapLibre GL + PMTiles libraries from server/map-assets/."""
    return send_from_directory(MAP_ASSETS, filename)


@app.route("/maps/<filename>")
def serve_map(filename):
    """
    Serve files from the maps/ directory.
    HTML/text files are passed through Flask's static handler (correct MIME type).
    PMTiles files use Range request streaming for efficient tile fetching.
    """
    filepath = os.path.join(MAPS_DIR, filename)
    if not os.path.isfile(filepath):
        from flask import abort
        abort(404)

    # Non-PMTiles files (HTML, GeoJSON, etc.) — serve with correct MIME type
    if not filename.endswith(".pmtiles"):
        return send_from_directory(MAPS_DIR, filename)

    file_size = os.path.getsize(filepath)
    range_header = request.headers.get("Range")

    # ETag based on file size + mtime — cheap and stable across restarts.
    mtime = os.path.getmtime(filepath)
    etag = hashlib.md5(f"{filepath}:{file_size}:{mtime}".encode()).hexdigest()
    last_modified = __import__('email.utils').utils.formatdate(mtime, usegmt=True)

    # 304 Not Modified shortcut
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)

    base_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
        "Content-Type": "application/octet-stream",
        "ETag": etag,
        "Last-Modified": last_modified,
    }

    def stream(start, end):
        with open(filepath, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not m:
            from flask import abort
            abort(416)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else file_size - 1
        if start >= file_size:
            from flask import abort
            abort(416)
        end = min(end, file_size - 1)
        headers = {**base_headers,
                   "Content-Range": f"bytes {start}-{end}/{file_size}",
                   "Content-Length": str(end - start + 1)}
        return Response(stream(start, end), status=206, headers=headers)

    headers = {**base_headers, "Content-Length": str(file_size)}
    return Response(stream(0, file_size - 1), status=200, headers=headers)


@app.route("/lookup")
def lookup_page():
    """Serve the FCC call-sign lookup page."""
    return render_template("lookup.html")


@app.route("/api/lookup")
def api_lookup():
    """
    JSON lookup endpoint. Query string: ?callsign=X1ABC
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


@app.route("/health")
def health():
    """Simple health check; also reports whether the index is present."""
    return jsonify({
        "ok": True,
        "index_present": os.path.exists(lookup.INDEX_PATH),
        "zip_entries": len(ZIP_TABLE),
    })


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
        },
    }
    return jsonify(payload)


@app.route("/server-ports.json")
def server_ports():
    """Alias for /api/config — canonical service-discovery endpoint.
    HTML pages fetch this on load so no port numbers need to be hardcoded."""
    return api_config()


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

    # CPU
    cpu_pct   = psutil.cpu_percent(interval=1)
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
        "cpu_pct":     cpu_pct,
        "cpu_count":   cpu_count,
        "cpu_temp_c":  temp,
        "ram":         ram_info,
        "disk":        disk_info,
        "load":        load_info,
        "uptime_sec":  uptime_sec,
        "boot_str":    boot_str,
        "fcc_db_date": fcc_db_mtime,
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

    return jsonify({"ok": True, "supported": True, "cards": cards})


PORT = 8083

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
    args = parser.parse_args()

    PORT = find_free_port()
    url = f"http://localhost:{PORT}/"
    print(f"\n  OASIS — {url}\n")
    if not args.no_browser:
        # Open the browser shortly after the server starts.
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # host=0.0.0.0 so other devices on the off-grid network can reach it.
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
