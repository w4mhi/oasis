#!/usr/bin/env python3
"""
services/aprs/common/aprs.py
----------------------------
Service-owned implementation for the APRS History API enabler.
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

# services/aprs/common/aprs.py is 3 directories under the repo root
# (services/aprs/common/<file>), so 4 dirname() calls are needed to reach it:
# strip the filename, then walk up common -> aprs -> services -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _REPO_ROOT)

from common.oasis_lib import _hr, _step, _ok, _warn, _info, _fail, _run

SERVICE = "graywolf-api"
PORT = 8085
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"
SELF = os.path.abspath(__file__)
REPO_ROOT = _REPO_ROOT
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
DB_PATH = os.environ.get("APRS_DB_PATH", "/var/lib/graywolf/graywolf-history.db")

# Persistent DB connection — reused across requests.
_db_conn: "sqlite3.Connection | None" = None


def _get_db() -> "tuple[sqlite3.Connection | None, str | None]":
    global _db_conn
    if _db_conn is not None:
        return _db_conn, None
    if not os.path.exists(DB_PATH):
        return None, f"Database not found at {DB_PATH}"
    try:
        conn = sqlite3.connect(DB_PATH, isolation_level=None)
        conn.text_factory = lambda b: b.decode("latin-1")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _db_conn = conn
        return _db_conn, None
    except Exception as exc:
        _db_conn = None
        return None, str(exc)


_cpu_pct_cache = 0.0
_cpu_sampler_started = False
_cpu_sampler_lock = threading.Lock()


def _start_cpu_sampler():
    global _cpu_sampler_started
    with _cpu_sampler_lock:
        if _cpu_sampler_started:
            return
        _cpu_sampler_started = True

    def _sample():
        global _cpu_pct_cache
        import psutil
        psutil.cpu_percent()
        while True:
            time.sleep(5)
            _cpu_pct_cache = psutil.cpu_percent()

    threading.Thread(target=_sample, daemon=True, name="cpu-sampler").start()


def read_stations():
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
            LEFT JOIN positions p ON p.id = (
                SELECT p2.id FROM positions p2
                WHERE p2.station_key = s.key
                ORDER BY p2.timestamp DESC
                LIMIT 1
            )
            ORDER BY COALESCE(p.timestamp, s.last_heard) DESC
        """)
        rows = cur.fetchall()

        stations = []
        for r in rows:
            (callsign, is_object, symbol, s_comment, last_heard,
             lat, lon, alt, speed, course, has_course, p_comment,
             via, path, timestamp) = r
            if isinstance(symbol, (bytes, bytearray)):
                sym_str = symbol.decode("latin-1")
            else:
                sym_str = symbol or "/-"
            sym_table = sym_str[0] if len(sym_str) >= 1 else "/"
            sym_code = sym_str[1] if len(sym_str) >= 2 else "-"
            try:
                path_list = json.loads(path) if path else []
            except Exception:
                path_list = []
            comment = p_comment or s_comment or ""
            stations.append({
                "callsign": callsign,
                "is_object": bool(is_object),
                "sym_table": sym_table,
                "sym_code": sym_code,
                "lat": lat,
                "lon": lon,
                "alt_m": round(alt, 1) if alt else None,
                "speed_mph": round(speed * 1.15078, 1) if speed else 0,
                "course": int(course) if has_course else None,
                "comment": comment,
                "last_heard": last_heard or timestamp,
                "via": via or "",
                "path": path_list,
            })
        return stations, None
    except Exception:
        _db_conn = None
        return None, str(sys.exc_info()[1])


def read_track(callsign, minutes):
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
                "lat": lat,
                "lon": lon,
                "timestamp": timestamp,
                "speed_mph": round(speed * 1.15078, 1) if speed else 0,
                "course": int(course) if has_course else None,
                "alt_m": round(alt, 1) if alt else None,
            })
        return points, None
    except Exception:
        _db_conn = None
        return None, str(sys.exc_info()[1])


def gather_system():
    import psutil

    disk_info = None
    for mount, label in [("/mnt/ssd", "SSD"), ("/mnt/emmc", "eMMC"), ("/", "System"), ("C:\\", "System")]:
        try:
            disk = psutil.disk_usage(mount)
            disk_info = {
                "label": label,
                "total_gb": round(disk.total / 1e9, 1),
                "used_gb": round(disk.used / 1e9, 1),
                "free_gb": round(disk.free / 1e9, 1),
                "pct": disk.percent,
            }
            break
        except Exception:
            continue
    if disk_info is None:
        disk_info = {"error": "unavailable"}

    _start_cpu_sampler()
    cpu_pct = _cpu_pct_cache
    cpu_count = psutil.cpu_count(logical=True) or 1

    ram = psutil.virtual_memory()
    ram_info = {
        "total_mb": round(ram.total / 1e6, 1),
        "used_mb": round(ram.used / 1e6, 1),
        "free_mb": round(ram.available / 1e6, 1),
        "pct": ram.percent,
    }

    load_info = None
    try:
        load1, _, _ = psutil.getloadavg()
        load_info = {
            "avg1": round(load1, 2),
            "cores": cpu_count,
            "pct": round((load1 / cpu_count) * 100, 1),
        }
    except AttributeError:
        pass

    boot_ts = psutil.boot_time()
    uptime_sec = int(time.time() - boot_ts)
    boot_str = time.strftime("%a %b %d %H:%M", time.localtime(boot_ts))

    temp = None
    try:
        temps = psutil.sensors_temperatures()
        for key in ("cpu_thermal", "cpu-thermal", "soc_thermal", "coretemp"):
            if key in temps and temps[key]:
                temp = round(temps[key][0].current, 1)
                break
    except Exception:
        pass

    fcc_db_mtime = None
    fcc_db_path = "/mnt/ssd/Documents/reference/fcc-offline-database/data/EN.dat"
    try:
        mtime = os.path.getmtime(fcc_db_path)
        fcc_db_mtime = time.strftime("%Y-%m-%d", time.localtime(mtime))
    except Exception:
        pass

    return {
        "ok": True,
        "cpu_pct": cpu_pct,
        "cpu_count": cpu_count,
        "cpu_temp_c": temp,
        "ram": ram_info,
        "disk": disk_info,
        "load": load_info,
        "uptime_sec": uptime_sec,
        "boot_str": boot_str,
        "fcc_db_date": fcc_db_mtime,
    }


def _build_app():
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
    app = _build_app()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=False)


def _target_user_home():
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        home = os.path.expanduser("~")
    return user, home


def enable_service(start=True, port=PORT):
    _step(1, f"Enabling the GrayWolf APRS History API (port {port})")

    if sys.platform != "linux":
        _fail("This sets up a systemd service — Linux only.")

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
WorkingDirectory={REPO_ROOT}
ExecStart={python} {os.path.join(REPO_ROOT, 'services', 'graywolf', 'enable-graywolf-api.py')} serve --port {port}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    proc = subprocess.Popen(["sudo", "tee", SERVICE_FILE], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
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
    status = _run(["systemctl", "is-active", SERVICE], check=False, capture_output=True, text=True).stdout.strip()
    if status == "active":
        _ok(f"{SERVICE} is active on :{port}")
    else:
        _warn(f"{SERVICE} status: {status}")
        log = _run(["journalctl", "-u", SERVICE, "-n", "12", "--no-pager", "--no-hostname"], check=False, capture_output=True, text=True)
        for line in (log.stdout or log.stderr).strip().splitlines():
            _info(line)
        _info(f"Check logs with:  journalctl -u {SERVICE} -f")


def run(no_enable=False, port=None, cmd=None):
    print()
    print("  OASIS -- APRS History API Installer")
    _hr()
    _info(f"Service: {SERVICE}")
    _info(f"Port: {port or PORT}")

    if cmd == "serve":
        serve(port or PORT)
        return

    enable_service(start=not no_enable, port=port or PORT)


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
        run(no_enable=args.no_enable, port=args.port, cmd="serve")
    else:
        run(no_enable=args.no_enable, port=args.port if hasattr(args, 'port') else None)


if __name__ == "__main__":
    main()
