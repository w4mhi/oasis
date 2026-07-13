#!/usr/bin/env python3
"""services/adsb/common/adsb.py — ADS-B recorder + JSON API (serve) and installer (run)."""
import json
import os
import platform
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
from common import manifest as M  # noqa: E402

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

# FlightAware apt repo (online fallback). dump1090-fa isn't in base Debian/Pi OS
# apt — it ships from FlightAware's own repo. Path/suite mapping below is the
# maintainer's best current understanding; confirm on a real build before relying
# on it for a new suite.
FA_KEY_URL   = "https://www.flightaware.com/adsb/piaware/files/packages/flightaware-apt-repository.gpg"
FA_KEYRING   = "/etc/apt/trusted.gpg.d/flightaware-apt-repository.gpg"
FA_LIST_PATH = "/etc/apt/sources.list.d/flightaware.list"
FA_BASE_URL  = "https://www.flightaware.com/adsb/piaware/files/packages"
FA_REPO_SUITES = {"bookworm": "bookworm", "trixie": "trixie"}

ARCH_MAP = {"aarch64": "arm64", "arm64": "arm64", "armv7l": "armhf",
            "armhf": "armhf", "armv6l": "armhf", "x86_64": "amd64", "amd64": "amd64"}


def _detect_suite():
    """Return the Debian suite (bookworm / trixie / …), mirroring rtl_sdr.py."""
    try:
        result = subprocess.run(["lsb_release", "-cs"], capture_output=True, text=True, check=True)
        suite = result.stdout.strip().lower()
    except Exception:
        suite = "other"
    ubuntu_to_debian = {"jammy": "bookworm", "focal": "bullseye", "noble": "bookworm"}
    suite = ubuntu_to_debian.get(suite, suite)
    _info(f"Debian suite: {suite}")
    return suite


def _deb_arch():
    machine = platform.machine()
    arch = ARCH_MAP.get(machine)
    if not arch:
        _fail(f"No dump1090-fa package available for architecture '{machine}'.")
    _info(f"Architecture → .deb arch: {arch}")
    return arch


def _find_local_debs(directory, deb_arch):
    """Scan *directory* for vendored dump1090-fa .debs matching deb_arch."""
    found = []
    if directory and os.path.isdir(directory):
        for fname in sorted(os.listdir(directory)):
            if fname.startswith("._") or not fname.endswith(f"_{deb_arch}.deb"):
                continue
            found.append(os.path.join(directory, fname))
    return found


def _install_debs_offline(deb_paths):
    """Install bundled .debs; apt resolves any co-vendored deps from the same dir."""
    _info("Installing (offline):")
    for p in deb_paths:
        _info(f"  {os.path.basename(p)}")
    cmd = ["sudo", "apt-get", "install", "-y"] + deb_paths
    if _run(cmd, check=False).returncode == 0:
        _ok("dump1090-fa installed from vendored .deb")
        return True
    _warn("Offline install failed. If you saw dependency errors, try:")
    _warn("  sudo apt-get install -f")
    return False


def _add_flightaware_repo(suite):
    repo_suite = FA_REPO_SUITES.get(suite)
    if not repo_suite:
        _warn(f"Unknown suite '{suite}' — assuming FlightAware repo suite '{suite}'.")
        repo_suite = suite

    _info("Installing the FlightAware repo signing key ...")
    key_cmd = f"curl -fsSL {FA_KEY_URL} | sudo gpg --dearmor -o {FA_KEYRING}"
    if _run(["bash", "-c", key_cmd], check=False).returncode != 0:
        _fail("Could not install the FlightAware repo signing key (need internet + curl).")

    line = f"deb [signed-by={FA_KEYRING}] {FA_BASE_URL} {repo_suite} piaware"
    if _run(["bash", "-c", f'echo "{line}" | sudo tee {FA_LIST_PATH}'], check=False).returncode != 0:
        _fail("Could not write the FlightAware apt sources entry.")
    _ok(f"FlightAware repo added: {FA_BASE_URL} {repo_suite} piaware")


def _install_dump1090(repo_root, online):
    """Install dump1090-fa offline-first from the vendored .deb; falls back to
    adding the FlightAware apt repo and installing online. See rtl_sdr.py
    (vendored-.deb pattern) and openwebrx.py (third-party-repo pattern)."""
    suite    = _detect_suite()
    deb_arch = _deb_arch()

    bundle_root = os.path.join(repo_root, "offline-packages")
    offline_dir = M.bundle_dir(bundle_root, "dump1090-fa", suite)
    _info(f"Offline dir: {os.path.relpath(offline_dir, repo_root)}")
    deb_paths = _find_local_debs(offline_dir, deb_arch)

    if deb_paths:
        _ok(f"Found {len(deb_paths)} vendored dump1090-fa package(s) — installing offline.")
        if _install_debs_offline(deb_paths):
            return
        _warn("Falling back to online install.")
    else:
        _info("No vendored dump1090-fa .deb found — will install online.")

    if not online:
        _fail("dump1090-fa is not vendored offline and online install is disabled.")
        return

    _add_flightaware_repo(suite)
    _run(["sudo", "apt-get", "update"], check=False)
    if _run(["sudo", "apt-get", "install", "-y", "dump1090-fa"], check=False).returncode != 0:
        _fail("apt-get could not install dump1090-fa — check the FlightAware repo entry and internet.")
    _ok("dump1090-fa installed via the FlightAware apt repo")


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
    _install_dump1090(repo_root, online)
    _run(["sudo", "systemctl", "disable", "--now", f"{DECODER_UNIT}.service"])
    _step(2, "adsb-api unit (disabled by default)")
    _write_api_unit(repo_root)
    _run(["sudo", "systemctl", "disable", f"{API_SERVICE}.service"])
    _ok("ADS-B installed. Start it from the dashboard (ADS-B card).")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
