#!/usr/bin/env python3
"""
enable-graywolf-api.py
----------------------
The GrayWolf APRS History API — a small Flask service that reads graywolf's
history DB and exposes station positions + system stats as JSON for the OASIS
APRS map and dashboard (port 8085). This single script is both the **server**
and its **enabler**, the same idea as enable-rtl-sdr.py:

  enable-graywolf-api.py                 # write + enable the systemd service
  enable-graywolf-api.py serve [--port]  # run the API (what the service execs)

The systemd unit's ExecStart runs this script in `serve` mode under the repo's
.venv (Flask + psutil live there). Flask is imported lazily so the enable path
works under a plain system python3 too.

History: the API used to live at graywolf-api/graywolf_api.py (folder removed).
A Pi that still has an old systemd unit pointing at that path will fail until it
re-runs this script (or install-graywolf.py), which rewrites the unit here.

Usage:
  python3 scripts/enable-graywolf-api.py                 # enable + start service
  python3 scripts/enable-graywolf-api.py --no-enable      # write unit, don't start
  python3 scripts/enable-graywolf-api.py serve            # run the API directly
  python3 scripts/enable-graywolf-api.py serve --port 8085

Requires: Linux + systemd + sudo for the enable path; Flask + psutil (the repo
.venv) for the serve path. DB path is overridable with $APRS_DB_PATH.
"""

import argparse
import getpass
import json
import os
import pwd
import sqlite3
import subprocess
import sys
import threading
import time

SERVICE      = "graywolf-api"
PORT         = 8085
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"

SELF        = os.path.abspath(__file__)
REPO_ROOT   = os.path.dirname(os.path.dirname(SELF))
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "bin", "python3")

# DB path is overridable for testing off-Pi (e.g. APRS_DB_PATH=./test.db).
DB_PATH = os.environ.get("APRS_DB_PATH", "/var/lib/graywolf/graywolf-history.db")

# Persistent DB connection — opened once per process, reused across requests.
# WAL mode is set on first open so readers and GrayWolf's writer never block
# each other, and the overhead of opening the file on every request (costly on
# SD card storage) is eliminated.  Read-write access is required to issue the
# WAL pragma; the API itself never writes any rows.
_db_conn: "sqlite3.Connection | None" = None


def _get_db() -> "tuple[sqlite3.Connection | None, str | None]":
    """Return the shared DB connection, opening + configuring it if needed.

    Returns (connection, None) on success or (None, error_string) on failure.
    Resets *_db_conn* to None whenever an error is detected so the next call
    retries the open — important when GrayWolf hasn't created the DB yet.
    """
    global _db_conn
    if _db_conn is not None:
        return _db_conn, None
    if not os.path.exists(DB_PATH):
        return None, f"Database not found at {DB_PATH}"
    try:
        # isolation_level=None → autocommit: every SELECT sees the latest
        # committed data rather than a pinned WAL snapshot.  Without this a
        # persistent connection in WAL mode holds a read transaction open
        # indefinitely and GrayWolf's new packets are invisible to queries.
        conn = sqlite3.connect(DB_PATH, isolation_level=None)  # read-write; needed for WAL pragma
        conn.text_factory = lambda b: b.decode("latin-1")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; faster on SD
        _db_conn = conn
        return _db_conn, None
    except Exception as exc:
        _db_conn = None
        return None, str(exc)


# ══ API server (serve mode) ═════════════════════════════════════════════════════
# Flask/psutil are imported lazily inside the serve path so the enable path can
# run under a system python3 that doesn't have them.

# ── Background CPU sampler ────────────────────────────────────────────────────
# psutil.cpu_percent(interval=N) blocks for N seconds. A daemon thread refreshes
# the cached value every 5 s so /api/system returns immediately.
_cpu_pct_cache       = 0.0
_cpu_sampler_started = False
_cpu_sampler_lock    = threading.Lock()


def _start_cpu_sampler():
    global _cpu_sampler_started
    with _cpu_sampler_lock:
        if _cpu_sampler_started:
            return
        _cpu_sampler_started = True

    def _sample():
        global _cpu_pct_cache
        import psutil
        psutil.cpu_percent()          # prime the counter (non-blocking first call)
        while True:
            time.sleep(5)
            _cpu_pct_cache = psutil.cpu_percent()

    threading.Thread(target=_sample, daemon=True, name="cpu-sampler").start()


def read_stations():
    """Return (stations, error) — latest position per station from the DB."""
    global _db_conn
    con, error = _get_db()
    if error:
        return None, error
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT
                s.callsign,
                s.is_object,
                s.symbol,
                s.comment      AS s_comment,
                s.last_heard,
                p.lat,
                p.lon,
                p.alt,
                p.speed,
                p.course,
                p.has_course,
                p.comment      AS p_comment,
                p.via,
                p.path,
                p.timestamp
            FROM stations s
            JOIN positions p ON p.station_key = s.key
            WHERE p.id IN (
                SELECT id FROM positions p2
                WHERE p2.station_key = s.key
                ORDER BY p2.timestamp DESC
                LIMIT 1
            )
            ORDER BY p.timestamp DESC
        """)
        rows = cur.fetchall()

        stations = []
        for r in rows:
            (callsign, is_object, symbol, s_comment, last_heard,
             lat, lon, alt, speed, course, has_course, p_comment,
             via, path, timestamp) = r

            # Decode symbol bytes → two chars
            if isinstance(symbol, (bytes, bytearray)):
                sym_str = symbol.decode("latin-1")
            else:
                sym_str = symbol or "/-"

            sym_table = sym_str[0] if len(sym_str) >= 1 else "/"
            sym_code  = sym_str[1] if len(sym_str) >= 2 else "-"

            # Parse path JSON array
            try:
                path_list = json.loads(path) if path else []
            except Exception:
                path_list = []

            comment = p_comment or s_comment or ""

            stations.append({
                "callsign":  callsign,
                "is_object": bool(is_object),
                "sym_table": sym_table,
                "sym_code":  sym_code,
                "lat":       lat,
                "lon":       lon,
                "alt_m":     round(alt, 1) if alt else None,
                "speed_mph": round(speed * 1.15078, 1) if speed else 0,
                "course":    int(course) if has_course else None,
                "comment":   comment,
                "last_heard": last_heard or timestamp,
                "via":       via or "",
                "path":      path_list,
            })

        return stations, None

    except Exception as e:
        _db_conn = None   # force reconnect on next request
        return None, str(e)


def read_track(callsign, minutes):
    """Return ordered position history for *callsign* over the last *minutes*.
    Pass minutes=0 to return all available history.
    Returns (points, error)."""
    global _db_conn
    con, error = _get_db()
    if error:
        return None, error
    try:
        cur = con.cursor()
        if minutes > 0:
            cur.execute("""
                SELECT p.lat, p.lon, p.timestamp, p.speed, p.course, p.has_course, p.alt
                FROM stations s
                JOIN positions p ON p.station_key = s.key
                WHERE s.callsign = ?
                  AND p.timestamp >= datetime('now', ? || ' minutes')
                ORDER BY p.timestamp ASC
            """, (callsign, f"-{minutes}"))
        else:
            cur.execute("""
                SELECT p.lat, p.lon, p.timestamp, p.speed, p.course, p.has_course, p.alt
                FROM stations s
                JOIN positions p ON p.station_key = s.key
                WHERE s.callsign = ?
                ORDER BY p.timestamp ASC
            """, (callsign,))
        rows = cur.fetchall()

        points = []
        for lat, lon, timestamp, speed, course, has_course, alt in rows:
            if lat is None or lon is None:
                continue
            points.append({
                "lat":       lat,
                "lon":       lon,
                "timestamp": timestamp,
                "speed_mph": round(speed * 1.15078, 1) if speed else 0,
                "course":    int(course) if has_course else None,
                "alt_m":     round(alt, 1) if alt else None,
            })
        return points, None

    except Exception as e:
        _db_conn = None   # force reconnect on next request
        return None, str(e)


def gather_system():
    """Return a system-stats dict. Raises ImportError if psutil is missing."""
    import psutil

    # Disk — auto-detect: SSD → eMMC → system root
    disk_info = None
    for mount, label in [("/mnt/ssd", "SSD"), ("/mnt/emmc", "eMMC"),
                         ("/", "System"), ("C:\\", "System")]:
        try:
            disk = psutil.disk_usage(mount)
            disk_info = {
                "label":    label,
                "total_gb": round(disk.total / 1e9, 1),
                "used_gb":  round(disk.used  / 1e9, 1),
                "free_gb":  round(disk.free  / 1e9, 1),
                "pct":      disk.percent,
            }
            break
        except Exception:
            continue
    if disk_info is None:
        disk_info = {"error": "unavailable"}

    # CPU — read from background sampler cache (non-blocking).
    _start_cpu_sampler()
    cpu_pct   = _cpu_pct_cache
    cpu_count = psutil.cpu_count(logical=True) or 1

    # RAM
    ram = psutil.virtual_memory()
    ram_info = {
        "total_mb": round(ram.total     / 1e6, 1),
        "used_mb":  round(ram.used      / 1e6, 1),
        "free_mb":  round(ram.available / 1e6, 1),
        "pct":      ram.percent,
    }

    # Load average (Linux/macOS only — AttributeError on Windows)
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

    # CPU temperature (Pi-specific; gracefully absent on macOS/Windows)
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
    fcc_db_path = "/mnt/ssd/Documents/reference/fcc-offline-database/data/EN.dat"
    try:
        mtime = os.path.getmtime(fcc_db_path)
        fcc_db_mtime = time.strftime("%Y-%m-%d", time.localtime(mtime))
    except Exception:
        pass

    return {
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
    }


def _build_app():
    """Construct the Flask app. Imports Flask lazily (serve path only)."""
    from flask import Flask, jsonify

    app = Flask(__name__)

    @app.after_request
    def cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    @app.route("/api/aprs/stations")
    def api_stations():
        stations, error = read_stations()
        if error:
            return jsonify({"ok": False, "error": error}), 503
        return jsonify({"ok": True, "count": len(stations), "stations": stations})

    @app.route("/api/aprs/track")
    def api_track():
        from flask import request as freq
        callsign = freq.args.get("callsign", "").strip().upper()
        if not callsign:
            return jsonify({"ok": False, "error": "callsign parameter required"}), 400
        try:
            minutes = int(freq.args.get("minutes", "60"))
        except ValueError:
            minutes = 60
        minutes = max(0, minutes)
        points, error = read_track(callsign, minutes)
        if error:
            return jsonify({"ok": False, "error": error}), 503
        return jsonify({"ok": True, "callsign": callsign, "minutes": minutes,
                        "count": len(points), "points": points})

    @app.route("/api/system")
    def api_system():
        try:
            return jsonify(gather_system())
        except ImportError:
            return jsonify({"ok": False, "error": "psutil not installed"}), 503

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "db": DB_PATH, "db_exists": os.path.exists(DB_PATH)})

    return app


def serve(port):
    """Run the API server (blocking). What the systemd unit execs."""
    app = _build_app()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=False)


# ══ Service enabler (default mode) ══════════════════════════════════════════════
def _target_user_home():
    """Return (user, home) for the operator — honours sudo's original user."""
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        home = os.path.expanduser("~")
    return user, home


def enable_service(start=True):
    # Imported here so the serve path (and the backward-compat shim) never need
    # the OASIS lib or a writable /etc.
    sys.path.insert(0, os.path.dirname(SELF))
    from common.oasis_lib import _step, _ok, _info, _warn, _fail, _run

    _step(1, f"Enabling the GrayWolf APRS History API (port {PORT})")

    if sys.platform != "linux":
        _fail("This sets up a systemd service — Linux only.")

    # The API needs Flask + psutil from the repo .venv (built by setup-server.py).
    python = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "/usr/bin/python3"
    if python != VENV_PYTHON:
        _warn(f"{VENV_PYTHON} not found — using system python3.")
        _warn("If the API fails to start (missing Flask/psutil), run first:")
        _warn("  python3 scripts/setup-server.py")

    user, _home = _target_user_home()
    unit = f"""[Unit]
Description=GrayWolf APRS History API — OASIS
After=network.target graywolf.service
Wants=graywolf.service

[Service]
Type=simple
User={user}
WorkingDirectory={os.path.dirname(SELF)}
ExecStart={python} {SELF} serve --port {PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    proc = subprocess.Popen(["sudo", "tee", SERVICE_FILE],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(unit.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {SERVICE_FILE}")
    _run(["sudo", "chmod", "644", SERVICE_FILE], check=False)
    _ok(f"Service file: {SERVICE_FILE}  (runs as {user})")

    _run(["sudo", "systemctl", "daemon-reload"], check=False)

    if not start:
        _info(f"--no-enable: not starting. Later: sudo systemctl enable --now {SERVICE}")
        return

    _run(["sudo", "systemctl", "enable", "--now", SERVICE], check=False)
    status = _run(["systemctl", "is-active", SERVICE],
                  check=False, capture_output=True, text=True).stdout.strip()
    if status == "active":
        _ok(f"{SERVICE} is active on :{PORT}")
    else:
        _warn(f"{SERVICE} status: {status}")
        log = _run(["journalctl", "-u", SERVICE, "-n", "12", "--no-pager", "--no-hostname"],
                   check=False, capture_output=True, text=True)
        for line in (log.stdout or log.stderr).strip().splitlines():
            _info(line)
        _info(f"Check logs with:  journalctl -u {SERVICE} -f")


# ══ Entry point ═════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="GrayWolf APRS History API — run it (serve) or enable it (default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-enable", action="store_true",
                        help="Write the systemd unit but don't enable/start it.")
    sub = parser.add_subparsers(dest="cmd")
    p_serve = sub.add_parser("serve", help="Run the API server (used by the systemd unit).")
    p_serve.add_argument("--port", type=int,
                         default=int(os.environ.get("APRS_API_PORT", PORT)),
                         help=f"TCP port to listen on (default: {PORT}, or $APRS_API_PORT).")
    args = parser.parse_args()

    if args.cmd == "serve":
        serve(args.port)
    else:
        enable_service(start=not args.no_enable)


if __name__ == "__main__":
    main()
