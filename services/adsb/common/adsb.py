#!/usr/bin/env python3
"""services/adsb/common/adsb.py — ADS-B recorder + JSON API (serve) and installer (run)."""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from services.adsb.common import history, alerts
from common.oasis_lib import _hr, _step, _ok, _warn, _info, _fail, _run  # noqa: E402

DB_PATH     = os.environ.get("ADSB_DB_PATH", "/var/lib/adsb/adsb-history.db")
JSON_PATH   = os.environ.get("ADSB_JSON_PATH", "/run/dump1090-fa/aircraft.json")
API_PORT    = int(os.environ.get("ADSB_API_PORT", "8086"))
RADIUS_KM   = float(os.environ.get("ADSB_ALERT_RADIUS_KM", "50"))
POLL_SECS   = float(os.environ.get("ADSB_POLL_SECS", "1.0"))

_live = {"now": 0.0, "aircraft": []}      # last-seen snapshot
_alerts = []                              # in-memory ring (most recent last)
_ALERTS_MAX = 200
_lock = threading.Lock()


def _load_station():
    try:
        with open(os.path.join(_REPO_ROOT, "station.json")) as f:
            s = json.load(f)
        return {"lat": s.get("lat"), "lon": s.get("lon")}
    except Exception:
        return None


def _poller():
    station = _load_station()
    writer = history.open_writer(DB_PATH)
    seen_alert = {}   # (icao, kind) -> ts, for dedupe within 300s
    while True:
        try:
            with open(JSON_PATH) as f:
                data = json.load(f)
            ts = time.time()
            aircraft = data.get("aircraft", [])
            with _lock:
                _live["now"] = ts
                _live["aircraft"] = aircraft
            for ac in aircraft:
                history.record(writer, ac, ts)
                for a in alerts.evaluate(ac, station, RADIUS_KM):
                    key = (ac.get("hex"), a["kind"])
                    if ts - seen_alert.get(key, 0) > 300:
                        seen_alert[key] = ts
                        writer.execute(
                            "INSERT INTO alert_events(ts, icao, kind, detail) VALUES(?,?,?,?)",
                            (ts, ac.get("hex"), a["kind"], a["detail"]))
                        with _lock:
                            _alerts.append({"ts": ts, "icao": ac.get("hex"),
                                            "kind": a["kind"], "detail": a["detail"]})
                            del _alerts[:-_ALERTS_MAX]
        except FileNotFoundError:
            pass    # decoder not running yet
        except Exception:
            pass
        time.sleep(POLL_SECS)


class _Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            with _lock:
                age = (time.time() - _live["now"]) if _live["now"] else None
            self._send({"ok": True, "last_json_age_s": age})
        elif u.path == "/aircraft":
            with _lock:
                self._send({"now": _live["now"], "aircraft": _live["aircraft"]})
        elif u.path == "/history":
            since = float(q.get("since", ["0"])[0])
            icao = q.get("icao", [None])[0]
            r, err = history.open_reader(DB_PATH)
            if err:
                self._send({"ok": False, "error": err}, 503); return
            self._send({"observations": history.history(r, since, icao)})
            r.close()
        elif u.path == "/alerts":
            with _lock:
                self._send({"alerts": list(_alerts)})
        else:
            self._send({"ok": False, "error": "not found"}, 404)


def serve():
    threading.Thread(target=_poller, daemon=True, name="adsb-poller").start()
    ThreadingHTTPServer(("127.0.0.1", API_PORT), _Handler).serve_forever()


API_SERVICE  = "adsb-api"
DECODER_UNIT = "dump1090-fa"
UNIT_PATH    = f"/etc/systemd/system/{API_SERVICE}.service"


def _write_api_unit(repo_root):
    venv_py = os.path.join(repo_root, ".venv", "bin", "python3")
    entry   = os.path.join(repo_root, "services", "adsb", "install.py")
    unit = f"""[Unit]
Description=OASIS ADS-B recorder + history API
After=network.target

[Service]
Type=simple
ExecStart={venv_py} {entry} --serve
Restart=on-failure
StateDirectory=adsb

[Install]
WantedBy=multi-user.target
"""
    proc = subprocess.Popen(
        ["sudo", "tee", UNIT_PATH],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    proc.communicate(unit.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {UNIT_PATH}")
    _ok(f"Service file: {UNIT_PATH}")
    _run(["sudo", "systemctl", "daemon-reload"])


def run(repo_root, online=True):
    if sys.platform != "linux":
        _fail("ADS-B service installs on Linux/Raspberry Pi only.")
        return
    print()
    print("  OASIS -- ADS-B Installer (dump1090-fa + adsb-api)")
    _hr()
    _step(1, "dump1090-fa decoder")
    # Phase 1: install dump1090-fa via apt. Offline vendored-.deb install (via the
    # offline-manifest bundle group + services/adsb/packages/) is wired in Task 9.
    _run(["sudo", "apt-get", "install", "-y", "dump1090-fa"])
    _run(["sudo", "systemctl", "disable", "--now", f"{DECODER_UNIT}.service"])
    _step(2, "adsb-api unit (disabled by default)")
    _write_api_unit(repo_root)
    _run(["sudo", "systemctl", "disable", f"{API_SERVICE}.service"])
    _ok("ADS-B installed. Start it from the dashboard (ADS-B card).")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
