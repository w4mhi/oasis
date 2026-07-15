#!/usr/bin/env python3
"""services/adsb/common/adsb.py — ADS-B recorder + JSON API (serve) and installer (run)."""
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _REPO_ROOT)

from services.adsb.common import history, alerts
from common.oasis_lib import (_hr, _step, _ok, _warn, _info, _fail, _run, has_internet,
                        download_bytes, sudo_apt_cmd, dpkg_installed_version)  # noqa: E402
from common import manifest as M  # noqa: E402
from common import config_paths  # noqa: E402

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
        with open(config_paths.station_json(_REPO_ROOT)) as f:
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
# apt — it ships from FlightAware's own repo. This installs FlightAware's own
# 'flightaware-apt-repository' .deb (see flightaware.com/adsb/piaware/install) —
# that package bundles the signing keyring AND a postinst script that
# auto-detects the OS codename and writes /etc/apt/sources.list.d/
# flightaware.sources itself, so we don't need to track suite→URL mappings or
# manage the keyring by hand. (Replaces an older approach that hand-downloaded
# a standalone .gpg key file from a URL FlightAware has since discontinued —
# it now 404s.)
FA_REPO_DEB_URL = (
    "https://www.flightaware.com/adsb/piaware/files/packages/pool/piaware/f/"
    "flightaware-apt-repository/flightaware-apt-repository_1.3_all.deb"
)

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
    cmd = sudo_apt_cmd("apt-get", "install", "-y", *deb_paths)
    if _run(cmd, check=False).returncode == 0:
        _ok("dump1090-fa installed from vendored .deb")
        return True
    _warn("Offline install failed. If you saw dependency errors, try:")
    _warn("  sudo apt-get install -f")
    return False


def _add_flightaware_repo():
    if not has_internet():
        _fail("Could not reach the internet to fetch the FlightAware apt-repository package.")

    _info("Downloading the FlightAware apt-repository package ...")
    deb_bytes, err = download_bytes(FA_REPO_DEB_URL)
    if err or not deb_bytes:
        _fail(f"Could not download the FlightAware apt-repository package: {err or 'empty response'}")

    fd, tmp = tempfile.mkstemp(prefix="flightaware-apt-repository-", suffix=".deb")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(deb_bytes)
        if _run(sudo_apt_cmd("dpkg", "-i", tmp), check=False).returncode != 0:
            _fail("Could not install the FlightAware apt-repository package (dpkg -i failed).")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    _ok("FlightAware apt repository installed (signing key + sources.list.d/flightaware.sources).")


def _remove_conflicting_dump1090_minimal():
    """Some Raspberry Pi OS images/repos ship `dump1090-fa-minimal`, a
    lightweight variant that installs its binary at the same path
    (/usr/bin/dump1090-fa) as FlightAware's full `dump1090-fa` package.
    Since the two packages don't know about each other's Conflicts/Replaces,
    dpkg aborts the unpack ("trying to overwrite '/usr/bin/dump1090-fa',
    which is also in package dump1090-fa-minimal") instead of apt resolving
    it automatically. Remove the minimal variant first if present."""
    if dpkg_installed_version("dump1090-fa-minimal"):
        _info("Removing conflicting package dump1090-fa-minimal ...")
        if _run(sudo_apt_cmd("apt-get", "remove", "-y", "dump1090-fa-minimal"), check=False).returncode != 0:
            _warn("Could not remove dump1090-fa-minimal — dump1090-fa install may fail with a file conflict.")


def _install_dump1090(repo_root, online):
    """Install dump1090-fa offline-first from the vendored .deb; falls back to
    adding the FlightAware apt repo and installing online. See rtl_sdr.py
    (vendored-.deb pattern) and openwebrx.py (third-party-repo pattern)."""
    _remove_conflicting_dump1090_minimal()
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

    _add_flightaware_repo()
    _run(sudo_apt_cmd("apt-get", "update"), check=False)
    if _run(sudo_apt_cmd("apt-get", "install", "-y", "dump1090-fa"), check=False).returncode != 0:
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


def run(repo_root, online=None):
    if sys.platform != "linux":
        _fail("ADS-B service installs on Linux/Raspberry Pi only.")
        return
    online = has_internet() if online is None else bool(online)
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


# ── Device binding (hardware-aware engine, Slice 2a) ──────────────────────────
# dump1090-fa selects its dongle with `--device <serial-or-index>`. OASIS pins it
# to the SERIAL of whatever device is assigned to the `adsb` logical service, so
# with multiple dongles ADS-B always uses its own (not "index 0", which reorders
# on replug). The device options live in the FlightAware package env-file that
# dump1090-fa.service sources.
DUMP1090_ENV_FILE = "/etc/default/dump1090-fa"


def dump1090_device_args(device):
    """Pure: the dump1090-fa CLI device selector for an assigned device dict (or
    None). Returns [] when unassigned or the device has no serial."""
    if not device:
        return []
    serial = device.get("serial")
    return ["--device", serial] if serial else []


def apply(repo_root, device):
    """Bind (or unbind, when device is None) dump1090-fa to the assigned dongle
    by writing a `--device <serial>` into its env-file's RECEIVER_OPTIONS.

    Linux/root only (no-op elsewhere). BENCH-VERIFY: confirm on the target Pi
    that (a) the FlightAware dump1090-fa package sources DUMP1090_ENV_FILE with a
    RECEIVER_OPTIONS variable, and (b) dump1090-fa accepts `--device <serial>`
    (vs `--device-index`). This writer is the one Slice-2a element that cannot be
    validated off-Pi — the pure arg builder above is what the tests cover.
    """
    if sys.platform != "linux":
        return
    args = dump1090_device_args(device)
    device_token = " ".join(args)  # "--device 1090" or ""
    existing = ""
    try:
        with open(DUMP1090_ENV_FILE) as f:
            existing = f.read()
    except OSError:
        existing = ""
    out = []
    found = False
    for ln in existing.splitlines():
        m = re.match(r'\s*RECEIVER_OPTIONS\s*=\s*"?(.*?)"?\s*$', ln)
        if m:
            found = True
            opts = re.sub(r'--device(-index)?\s+\S+', '', m.group(1)).strip()
            opts = (opts + " " + device_token).strip()
            out.append(f'RECEIVER_OPTIONS="{opts}"')
        else:
            out.append(ln)
    if not found:
        out.append(f'RECEIVER_OPTIONS="{device_token}"')
    new_text = "\n".join(out) + "\n"
    # Reuse the exact sudo-tee Popen pattern from _write_api_unit above:
    proc = subprocess.Popen(
        ["sudo", "tee", DUMP1090_ENV_FILE],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    proc.communicate(new_text.encode())


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
