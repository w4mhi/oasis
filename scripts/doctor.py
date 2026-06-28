#!/usr/bin/env python3
"""
doctor.py
---------
Headless health check for an OASIS deployment.

Mirrors every check in system/setup.html — safe to run over SSH with no
browser, or as a post-deploy verification step.

Core checks (server, FCC index, maps, disk) set the exit code:
  0  All core checks pass  (optional services may still show warnings)
  1  One or more CORE checks failed

Usage:
  python3 scripts/doctor.py                        # all checks
  python3 scripts/doctor.py --core                 # core checks only
  python3 scripts/doctor.py --json                 # machine-readable JSON
  python3 scripts/doctor.py --host HOST            # non-default host
  python3 scripts/doctor.py --host HOST --port PORT
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT   = os.path.dirname(_SCRIPT_DIR)

FCC_INDEX   = os.path.join(REPO_ROOT, "fcc-offline-database", "data", "EN.idx")
MAPS_DIR    = os.path.join(REPO_ROOT, "maps")
WINLINK_DIR = os.path.join(REPO_ROOT, "winlink")

# Default port (matches PORTS in setup.html)
DEFAULT_PORT = 8083

# Live port table — updated from /server-ports.json if the server is up.
PORTS = {
    "flask":    8083,
    "graywolf": 8080,
    "kiwix":    8081,
    "winlink":  8082,
    "aprs_api": 8085,
    "webssh":   7681,
}

# GrayWolf offline tiles directory (Pi OS default installation path)
GRAYWOLF_TILES_DIR = "/var/lib/graywolf/tiles"

# RTL-SDR DVB driver blacklist written by install-rtl-sdr.py
RTL_BLACKLIST = "/etc/modprobe.d/rtlsdr-blacklist.conf"

# ── Display helpers ────────────────────────────────────────────────────────────
_RESET  = "\033[0m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_BOLD   = "\033[1m"


def _c(text, code):
    """Wrap text in ANSI colour code only when stdout is a real TTY."""
    return f"{code}{text}{_RESET}" if sys.stdout.isatty() else text


def _ok(msg):   print(f"  {_c('✓', _GREEN)}  {msg}")
def _warn(msg): print(f"  {_c('⚠', _YELLOW)}  {msg}")
def _fail(msg): print(f"  {_c('✗', _RED)}  {msg}")
def _info(msg): print(f"     {msg}")
def _hr():      print("─" * 62)


def _section(title):
    print(f"\n  {_c(title, _BOLD)}")
    _hr()


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _probe_port(host, port, timeout=3.0):
    """True if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError, socket.timeout):
        return False


def _which_binary(name):
    """Locate a binary on PATH + standard dirs. Returns path string or None."""
    path = shutil.which(name)
    if path:
        return path
    for d in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
              "/usr/bin", "/sbin", "/bin"):
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _svc_status(name):
    """Query systemd for a service unit. Always returns a dict — never raises."""
    if sys.platform != "linux":
        return {"active": "n/a", "enabled": "n/a", "installed": False}

    def _q(verb):
        try:
            r = subprocess.run(
                ["systemctl", verb, f"{name}.service"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    active  = _q("is-active")
    enabled = _q("is-enabled")
    return {
        "active":    active  or "unknown",
        "enabled":   enabled or "not-found",
        "installed": bool(enabled),
    }


def _svc_word(svc):
    """Compact 'active, enabled' description string for a _svc_status result."""
    en = f", {svc['enabled']}" if svc["enabled"] not in ("not-found", "n/a", "") else ""
    return f"{svc['active']}{en}"


def _http_get(url, timeout=5):
    """GET url and JSON-decode. Returns (ok:bool, data:dict|None). Never raises."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "OASIS-Doctor/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        try:
            return True, json.loads(body)
        except Exception:
            return True, {}
    except Exception:
        return False, None


# ── Check functions ────────────────────────────────────────────────────────────
# Each returns {"ok": True|False|"warn", "badge": str, "detail": str}
# where "warn" means non-critical / advisory.

def check_server(host, port):
    up = _probe_port(host, port)
    if not up:
        return {
            "ok": False, "badge": "DOWN",
            "detail": (
                f"No response on {host}:{port}. Is OASIS running?\n"
                "     Start with: ./scripts/start-server.sh"
            ),
        }

    ok, info = _http_get(f"http://{host}:{port}/api/server-info")
    if ok and info:
        wsgi  = info.get("wsgi", "unknown")
        ver   = info.get("wsgi_version", "?")
        dev   = wsgi != "gunicorn"
        svc   = _svc_status("oasis")
        svc_t = f", service {_svc_word(svc)}" if svc["installed"] else ""
        detail = f"Serving on :{port} via {wsgi} v{ver}{svc_t}."
        if dev:
            detail += "\n     For always-on use, run under gunicorn (see docs/SETUP.md)."
        return {
            "ok":    "warn" if dev else True,
            "badge": "DEV SERVER" if dev else "RUNNING",
            "detail": detail,
        }

    # Listening but /api/server-info unavailable — still counts as up.
    return {"ok": True, "badge": "RUNNING", "detail": f"Listening on :{port}."}


def check_fcc():
    if not os.path.isfile(FCC_INDEX):
        return {
            "ok": False, "badge": "NOT BUILT",
            "detail": (
                f"Index not found at fcc-offline-database/data/EN.idx.\n"
                "     Run: python3 scripts/setup-fcc-database.py"
            ),
        }
    try:
        count = sum(1 for _ in open(FCC_INDEX, "rb"))
    except OSError:
        count = 0
    return {
        "ok":     True,
        "badge":  "READY",
        "detail": f"Callsign index built ({count:,} entries).",
    }


def check_maps(host, port):
    # Local .pmtiles in the suite maps/ dir
    local_tiles = []
    if os.path.isdir(MAPS_DIR):
        local_tiles = [
            f.replace(".pmtiles", "")
            for f in os.listdir(MAPS_DIR)
            if f.endswith(".pmtiles")
        ]

    # GrayWolf offline tiles (Pi-only path)
    gw_tiles = []
    if os.path.isdir(GRAYWOLF_TILES_DIR):
        gw_tiles = [
            f for f in os.listdir(GRAYWOLF_TILES_DIR)
            if f.endswith(".pmtiles")
        ]

    total = len(local_tiles) + len(gw_tiles)

    # Check /api/fs/browse only when the server is reachable — a missing route
    # means the server is running stale code and needs a restart.
    server_up = _probe_port(host, port)
    if server_up:
        fs_ok, _ = _http_get(f"http://{host}:{port}/api/fs/browse", timeout=4)
        if not fs_ok:
            extra = f" ({len(local_tiles)} local map(s) in maps/.)" if local_tiles else ""
            return {
                "ok":    "warn",
                "badge": "RESTART",
                "detail": (
                    "/api/fs/browse missing — server is running old code.\n"
                    "     Restart OASIS to pick up the latest server/app.py."
                    + extra
                ),
            }

    gw_hint = (
        "GrayWolf provides offline maps. Download a region, then open\n"
        "     Offline Maps → Load maps and browse /var/lib/graywolf/tiles/."
    )

    if not total:
        return {
            "ok":    "warn",
            "badge": "N/A",
            "detail": (
                "No .pmtiles maps found in maps/ or /var/lib/graywolf/tiles/.\n"
                "     " + gw_hint
            ),
        }

    parts = []
    if local_tiles:
        parts.append(f"{len(local_tiles)} in maps/: {', '.join(local_tiles)}")
    if gw_tiles:
        parts.append(f"{len(gw_tiles)} in GrayWolf tiles")
    detail = f"Map(s) found — {'; '.join(parts)}."
    if server_up:
        detail += " Load maps endpoint OK."
    return {"ok": True, "badge": "READY", "detail": detail}


def check_disk():
    for mount, label in [
        ("/mnt/ssd", "SSD"),
        ("/mnt/emmc", "eMMC"),
        ("/", "System"),
        ("C:\\", "System"),
    ]:
        try:
            d        = shutil.disk_usage(mount)
            total_gb = round(d.total / 1e9, 1)
            free_gb  = round(d.free  / 1e9, 1)
            pct      = round(d.used  / d.total * 100)
            detail   = f"{free_gb} GB free of {total_gb} GB on {label} ({pct}% used)."
            if free_gb < 1:
                return {"ok": False, "badge": "CRITICAL",
                        "detail": detail + "\n     Free space now — maps / Wikipedia / FCC may fail to write."}
            if free_gb < 2 or pct >= 92:
                return {"ok": "warn", "badge": "LOW",
                        "detail": detail + "\n     Running low; large downloads (maps, ZIM, FCC) may not fit."}
            return {"ok": True, "badge": "OK", "detail": detail}
        except (FileNotFoundError, OSError):
            continue
    return {"ok": "warn", "badge": "N/A", "detail": "Could not read disk usage."}


# ── Optional service checks ────────────────────────────────────────────────────

def check_graywolf(host):
    port = PORTS["graywolf"]
    up   = _probe_port(host, port)
    svc  = _svc_status("graywolf")
    if up:
        return {"ok": True, "badge": "UP",
                "detail": f"graywolf ({_svc_word(svc)}), listening on :{port}."}
    if svc["installed"]:
        return {
            "ok": "warn", "badge": "STOPPED",
            "detail": (
                f"Installed but not listening (service graywolf is {svc['active']}).\n"
                "     Start: sudo systemctl start graywolf\n"
                "     Logs:  journalctl -u graywolf -f"
            ),
        }
    return {"ok": "warn", "badge": "NOT INSTALLED",
            "detail": "Install with: python3 scripts/install-graywolf.py"}


def check_graywolf_api(host):
    port = PORTS["aprs_api"]
    up   = _probe_port(host, port)
    svc  = _svc_status("graywolf-api")
    if up:
        ok2, data = _http_get(f"http://{host}:{port}/api/aprs/stations", timeout=5)
        count  = data.get("count") if ok2 and data else None
        detail = f"API on :{port} ({_svc_word(svc)})"
        detail += f", {count} station(s) in history DB." if count is not None else "."
        return {"ok": True, "badge": "UP", "detail": detail}
    if svc["installed"]:
        return {
            "ok": "warn", "badge": "STOPPED",
            "detail": (
                f"Installed but not responding ({svc['active']}).\n"
                "     Start: sudo systemctl start graywolf-api"
            ),
        }
    return {"ok": "warn", "badge": "NOT INSTALLED",
            "detail": "Enabled by: python3 scripts/install-graywolf.py"}


def check_pat(host):
    port    = PORTS["winlink"]
    up      = _probe_port(host, port)
    bin_    = _which_binary("pat")
    svc     = _svc_status("pat")
    pat_cfg = os.path.join(os.path.expanduser("~"), ".config", "pat", "config.json")

    if up:
        cfg_note = ""
        if os.path.isfile(pat_cfg):
            try:
                with open(pat_cfg) as fh:
                    cfg = json.load(fh)
                if not cfg.get("secure_login_password"):
                    cfg_note = "\n     Winlink password not set — run: pat configure"
            except Exception:
                pass
        badge  = "WARN" if cfg_note else "UP"
        ok_val = "warn" if cfg_note else True
        return {"ok": ok_val, "badge": badge,
                "detail": f"Pat web UI on :{port} ({_svc_word(svc)}).{cfg_note}"}

    if bin_:
        return {
            "ok": "warn", "badge": "STOPPED",
            "detail": (
                f"Pat installed ({bin_}) but web UI not running ({svc['active']}).\n"
                "     Start: sudo systemctl start pat"
            ),
        }
    return {"ok": "warn", "badge": "NOT INSTALLED",
            "detail": "Install with: python3 scripts/install-winlink.py"}


def check_rtl_sdr():
    rtl_test = _which_binary("rtl_test")
    rtl_fm   = _which_binary("rtl_fm")
    socat    = _which_binary("socat")
    tcpdump  = _which_binary("tcpdump")
    blk      = os.path.isfile(RTL_BLACKLIST)
    feed_svc = _svc_status("aprs-sdr-feed")

    if not rtl_test:
        return {
            "ok": "warn", "badge": "NOT INSTALLED",
            "detail": "RTL-SDR tools not found.\n     Install with: python3 scripts/install-rtl-sdr.py",
        }

    Y = "✓"
    N = "✗"
    lines = [
        f"rtl_test: {rtl_test}",
        f"rtl_fm: {Y if rtl_fm else N}  socat: {Y if socat else N}  tcpdump: {Y if tcpdump else N}",
        (f"DVB driver blacklist: {Y}" if blk
         else "DVB driver blacklist: ✗  (re-run install-rtl-sdr.py, then reboot)"),
    ]

    if feed_svc["active"] == "active":
        lines.append(f"APRS→GrayWolf feed: active ({_svc_word(feed_svc)})")
        feed_ok = True
    elif feed_svc["installed"]:
        lines.append(
            f"Feed service {feed_svc['active']}.\n"
            "     Start: sudo systemctl start aprs-sdr-feed"
        )
        feed_ok = False
    else:
        lines.append("Feed not enabled. Run: python3 scripts/enable-rtl-sdr.py")
        feed_ok = False

    tools_ok = bool(rtl_fm and socat and blk)
    badge    = "READY" if (feed_ok and tools_ok) else ("INSTALLED" if tools_ok else "PARTIAL")
    ok_val   = True if (feed_ok and tools_ok) else "warn"
    return {"ok": ok_val, "badge": badge, "detail": "\n     ".join(lines)}


def check_kiwix(host):
    port = PORTS["kiwix"]
    up   = _probe_port(host, port)
    bin_ = _which_binary("kiwix-serve")
    svc  = _svc_status("kiwix")
    if up:
        return {"ok": True, "badge": "UP",
                "detail": f"kiwix-serve on :{port} ({_svc_word(svc)})."}
    if bin_:
        return {
            "ok": "warn", "badge": "STOPPED",
            "detail": (
                f"kiwix-serve installed but not serving ({svc['active']}).\n"
                "     Add ZIM content, then: sudo systemctl start kiwix"
            ),
        }
    return {"ok": "warn", "badge": "NOT INSTALLED",
            "detail": "Install with: python3 scripts/install-kiwix.py"}


def check_webssh(host):
    port = PORTS["webssh"]
    up   = _probe_port(host, port)
    bin_ = _which_binary("ttyd")
    svc  = _svc_status("webssh")
    if up:
        return {"ok": True, "badge": "UP",
                "detail": f"ttyd (browser terminal) on :{port} ({_svc_word(svc)})."}
    if bin_:
        return {
            "ok": "warn", "badge": "STOPPED",
            "detail": (
                f"ttyd installed but not running ({svc['active']}).\n"
                "     Start: sudo systemctl start webssh"
            ),
        }
    return {"ok": "warn", "badge": "NOT INSTALLED",
            "detail": "Install with: python3 scripts/install-webssh.py"}


def check_winlink_forms():
    pages = [
        ("position-report.html", "position-report"),
        ("to-position.html",     "to-position"),
        ("radio-settings.html",  "radio-settings"),
    ]
    present = [lbl for fname, lbl in pages
               if os.path.isfile(os.path.join(WINLINK_DIR, fname))]
    missing = [lbl for fname, lbl in pages
               if not os.path.isfile(os.path.join(WINLINK_DIR, fname))]

    if not present:
        return {"ok": False, "badge": "MISSING",
                "detail": "No Winlink form pages found in winlink/."}
    detail = f"{len(present)} form(s) available: {', '.join(present)}"
    if missing:
        detail += f"  |  missing: {', '.join(missing)}"
    return {
        "ok":    "warn" if missing else True,
        "badge": "PARTIAL" if missing else "READY",
        "detail": detail,
    }


# ── Check table ────────────────────────────────────────────────────────────────
# (key, label, callable)  — callable receives (host, port) or just host where needed.
CORE_CHECKS = [
    ("server", "OASIS Server"),
    ("fcc",    "FCC Callsign Database"),
    ("maps",   "Map Tiles (PMTiles)"),
    ("disk",   "Disk Space"),
]
OPTIONAL_CHECKS = [
    ("graywolf",      "GrayWolf APRS  :8080"),
    ("graywolf_api",  "GrayWolf APRS History API  :8085"),
    ("pat",           "Winlink (Pat)  :8082"),
    ("rtl_sdr",       "RTL-SDR + APRS audio feed"),
    ("kiwix",         "Kiwix (Offline Wikipedia)  :8081"),
    ("webssh",        "Web SSH (ttyd)  :7681"),
    ("winlink_forms", "Winlink Forms (offline pages)"),
]


def _run_checks(host, port):
    """Run all checks and return a dict of key → result."""
    results = {}
    results["server"] = check_server(host, port)
    results["fcc"]    = check_fcc()
    results["maps"]   = check_maps(host, port)
    results["disk"]   = check_disk()
    results["graywolf"]      = check_graywolf(host)
    results["graywolf_api"]  = check_graywolf_api(host)
    results["pat"]           = check_pat(host)
    results["rtl_sdr"]       = check_rtl_sdr()
    results["kiwix"]         = check_kiwix(host)
    results["webssh"]        = check_webssh(host)
    results["winlink_forms"] = check_winlink_forms()
    return results


# ── Output ─────────────────────────────────────────────────────────────────────

def _badge_color(ok):
    if ok is True:   return _GREEN
    if ok is False:  return _RED
    return _YELLOW   # "warn"


def _print_result(label, result, width=42):
    ok     = result["ok"]
    badge  = result["badge"]
    detail = result["detail"]
    icon   = "✓" if ok is True else ("✗" if ok is False else "⚠")
    color  = _badge_color(ok)

    print(f"  {_c(icon, color)}  {label:<{width}} {_c('[' + badge + ']', color)}")
    for line in detail.splitlines():
        print(f"     {line.strip()}")


def run_all(args):
    host      = args.host
    port      = args.port
    core_only = args.core
    as_json   = args.json

    # Attempt to pull live port config from the server (if up).
    ok, cfg = _http_get(f"http://{host}:{port}/server-ports.json", timeout=3)
    if ok and cfg and "ports" in cfg:
        PORTS.update(cfg["ports"])

    # Run checks.
    if core_only:
        results = {
            "server": check_server(host, port),
            "fcc":    check_fcc(),
            "maps":   check_maps(host, port),
            "disk":   check_disk(),
        }
    else:
        results = _run_checks(host, port)

    core_ok = all(
        results.get(k, {}).get("ok") is True
        for k, _ in CORE_CHECKS
    )

    # ── JSON output ──
    if as_json:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "host":      host,
            "port":      port,
            "core_ok":   core_ok,
            "checks":    results,
        }
        print(json.dumps(payload, indent=2))
        return 0 if core_ok else 1

    # ── Human-readable output ──
    print()
    print(f"  {_c('OASIS Doctor', _BOLD)} — {host}:{port}")
    _hr()

    _section("Core")
    for key, label in CORE_CHECKS:
        if key in results:
            _print_result(label, results[key])

    if not core_only:
        _section("Optional Services")
        for key, label in OPTIONAL_CHECKS:
            if key in results:
                _print_result(label, results[key])

    print()
    _hr()
    if core_ok:
        _ok("All core checks passed.")
    else:
        _fail("One or more core checks FAILED.")
    print()

    return 0 if core_ok else 1


def main():
    ap = argparse.ArgumentParser(
        description=(
            "OASIS deployment health check.\n"
            "Mirrors system/setup.html — usable over SSH, no browser needed.\n\n"
            "Exit 0 = all CORE checks pass.  Exit 1 = core failure."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/doctor.py\n"
            "  python3 scripts/doctor.py --core\n"
            "  python3 scripts/doctor.py --json\n"
            "  python3 scripts/doctor.py --host 192.168.1.42\n"
            "  python3 scripts/doctor.py --host 192.168.1.42 --port 8083\n"
        ),
    )
    ap.add_argument(
        "--host", default="localhost",
        help="OASIS server host (default: localhost).",
    )
    ap.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"OASIS server port (default: {DEFAULT_PORT}).",
    )
    ap.add_argument(
        "--core", action="store_true",
        help="Run core checks only (server, FCC, maps, disk). Skip optional services.",
    )
    ap.add_argument(
        "--json", action="store_true",
        help="Output results as JSON to stdout instead of human-readable text.",
    )
    args = ap.parse_args()
    sys.exit(run_all(args))


if __name__ == "__main__":
    main()
