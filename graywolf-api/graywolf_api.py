#!/usr/bin/env python3
"""
graywolf_api.py
---------------
Minimal Flask service that reads graywolf-history.db and exposes
station positions as JSON for the APRS map page.

Port: 8085
DB:   /var/lib/graywolf/graywolf-history.db
"""

import json
import os
import sqlite3
import time

from flask import Flask, jsonify

# psutil is imported lazily inside /api/system so that a missing psutil only
# degrades the system-stats endpoint — the APRS station data must still serve.

app = Flask(__name__)

# DB path is overridable for testing off-Pi (e.g. APRS_DB_PATH=./test.db).
DB_PATH = os.environ.get("APRS_DB_PATH", "/var/lib/graywolf/graywolf-history.db")


@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def read_stations():
    if not os.path.exists(DB_PATH):
        return None, f"Database not found at {DB_PATH}"
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        con.text_factory = lambda b: b.decode("latin-1")
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
        con.close()

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
        return None, str(e)


@app.route("/api/aprs/stations")
def api_stations():
    stations, error = read_stations()
    if error:
        return jsonify({"ok": False, "error": error}), 503
    return jsonify({"ok": True, "count": len(stations), "stations": stations})


@app.route("/api/system")
def api_system():
    try:
        import psutil
    except ImportError:
        return jsonify({"ok": False, "error": "psutil not installed"}), 503

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

    # CPU — 1-second blocking sample
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


@app.route("/health")
def health():
    return jsonify({"ok": True, "db": DB_PATH, "db_exists": os.path.exists(DB_PATH)})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GrayWolf APRS History API")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("APRS_API_PORT", 8085)),
                        help="TCP port to listen on (default: 8085, or $APRS_API_PORT)")
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=False)
