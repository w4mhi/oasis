#!/usr/bin/env python3
"""
app.py
------
Off-grid Flask web server for OASIS - Off-grid Amateur Station Information Suite.

Serves the main index.html and all suite static files at the root.
The FCC amateur-radio call-sign lookup is available at /lookup.

Designed to run on a Raspberry Pi with no internet connection.
All FCC data is served from local flat files (EN.dat + EN.idx + zipcodes.csv);
no database engine is involved.

Run (development):   python3 app.py
Run (recommended):   see fcc-offline-database/README.md for the gunicorn + systemd setup.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory, Response

# The Windows embeddable Python (shipped in _runtime/windows/) uses a pythonXX._pth
# file, which builds sys.path solely from that file and suppresses the automatic
# script-directory entry that normal CPython adds. Insert both the suite root and
# the server directory so the shared `common` package and server-local modules
# resolve under every launcher (embedded or system Python).
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_ROOT = os.path.abspath(os.path.join(SERVER_DIR, ".."))
for path in (SUITE_ROOT, SERVER_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from common import lookup
from common import config_paths
from common import server as SERVER_SETUP
from common import setup_engine as SE
from common import hardware as HW
from common import hardware_detect as HD_detect
from common.oasis_lib import has_internet
APRS_STATIC_DIR = os.path.join(SUITE_ROOT, "services", "aprs", "static")
WINLINK_STATIC_DIR = os.path.join(SUITE_ROOT, "services", "winlink", "static")
FCC_STATIC_DIR = os.path.join(SUITE_ROOT, "services", "fcc_database", "static")
MAP_ASSETS  = os.path.join(APRS_STATIC_DIR, "map-assets")
APRS_DIR    = os.path.join(APRS_STATIC_DIR, "aprs")
WINLINK_DIR = WINLINK_STATIC_DIR
FCC_DIR     = FCC_STATIC_DIR
MAPS_DIR    = os.path.join(SUITE_ROOT, "maps")

# Suite version (git-tracked, single source of truth). The dashboard reads it
# via /version.json; the server also surfaces it in /api/server-info so doctor.py
# and the setup page can report it.
VERSION_FILE = os.path.join(SUITE_ROOT, "version.json")

# Written by setup-oasis.py: the set of features the operator chose to install.
# The dashboard reads it (via /api/installed-services) to hide cards for
# services that were never installed. Absent file → show everything.
INSTALLED_SERVICES_FILE = config_paths.installed_services_json(SUITE_ROOT)

# Portable / "locked" profile. When OASIS_FEATURES is set (comma-separated
# feature keys, e.g. "fcc,forms,repeaterbook"), the suite runs as a read-mostly
# standalone-tools build straight off a USB stick: /api/installed-services
# reports exactly this list with locked=True (so the dashboard shows only these
# gated cards AND hides the "show not installed" reveal button), and the routes
# for the daemon-backed features left out are refused (see _portable_gate).
# Absent/empty → None → normal behaviour (read installed-services.json).
# run-portable.command sets this before launching the server.
_portable_env = os.environ.get("OASIS_FEATURES", "").strip()
PORTABLE_FEATURES = (
    sorted({f.strip() for f in _portable_env.split(",") if f.strip()})
    if _portable_env else None
)

# In portable mode, refuse the URL prefixes for daemon-backed features that were
# left out of PORTABLE_FEATURES — both the JSON proxies and their static pages.
# Keeps a locked USB build from exposing a Winlink/APRS surface whose backing
# services (pat, direwolf, graywolf-api) are not running. Feature key → prefixes.
_PORTABLE_BLOCK = {
    "winlink":  ("/api/winlink", "/server/winlink"),
    "graywolf": ("/api/aprs", "/server/aprs"),
}

# Operator-placed map warnings (flood/fire/etc.) shared across every device that
# views the APRS map. Small JSON list on disk; serialized writes via a lock.
# Runtime state, not repo content — gitignored.
WARNINGS_FILE = os.path.join(SUITE_ROOT, "aprs-warnings.json")
_warnings_lock = threading.Lock()

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

# ── Shared light/dark theme toggle ────────────────────────────────────────────
# Inject static/theme.js just before </head> on every owned HTML page, so the
# sun/moon toggle (and the no-flash theme apply) appears everywhere without
# editing each page. Pages that manage their own theming are skipped:
#   • the 7" kiosk (/small-screen/)   • the APRS map (/aprs/)
#   • the graywolf-handbook (/static/graywolf-handbook/)
# theme.js is idempotent — it leaves a page's own toggle button (e.g. the
# dashboard's) alone and only adds the floating one when none exists.
_THEME_SKIP_PREFIXES = ("/small-screen/", "/server/aprs/", "/static/graywolf-handbook/")
_THEME_SNIPPET = '<script src="/static/theme.js"></script>'

# ── Setup orchestrator state (web API) ──────────────────────────────────────
_SETUP_PLAN_TTL_S = 3600
_setup_lock = threading.Lock()
_setup_plans = {}          # plan_id -> {plan, created_at}
_setup_jobs = {}           # job_id -> job dict
_setup_active_job = None   # active job id or None
_setup_cancel_requests = set()  # job ids requested for cooperative cancel


def _setup_now():
    return time.time()


def _setup_iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _setup_cleanup_plans_locked():
    cutoff = _setup_now() - _SETUP_PLAN_TTL_S
    stale = [k for k, v in _setup_plans.items() if v.get("created_at", 0) < cutoff]
    for k in stale:
        _setup_plans.pop(k, None)


def _setup_server_install():
    try:
        SERVER_SETUP.run(check_mode=False, repo_root=SUITE_ROOT)
        return {"ok": True}
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": f"server setup exited with code {code}",
        }
    except Exception as exc:
        return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": str(exc)}


def _setup_service_controls_install():
    script = os.path.join(SUITE_ROOT, "scripts", "enable-service-controls.py")
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": str(exc)}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "service-controls install failed").strip()
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": err.splitlines()[-1][:300] if err else "service-controls install failed",
            "stderr_tail": (r.stderr or "").strip()[-300:] or None,
            "stdout_tail": (r.stdout or "").strip()[-300:] or None,
        }
    return {"ok": True}


def _setup_run_script(script_rel, args=None, timeout=900):
    args = args or []
    script = os.path.join(SUITE_ROOT, script_rel)
    if not os.path.exists(script):
        return {
            "ok": False,
            "reason_code": "MISSING_SCRIPT",
            "reason_text": f"missing script: {script_rel}",
        }
    try:
        r = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason_code": "TIMEOUT",
            "reason_text": f"script timed out: {script_rel}",
        }
    except Exception as exc:
        return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": str(exc)}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "script failed").strip()
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": err.splitlines()[-1][:300] if err else f"script failed: {script_rel}",
            "stderr_tail": (r.stderr or "").strip()[-300:] or None,
            "stdout_tail": (r.stdout or "").strip()[-300:] or None,
        }
    return {"ok": True}


def _setup_run_chain(steps):
    for step in steps:
        res = _setup_run_script(step.get("script"), step.get("args"), timeout=step.get("timeout", 900))
        if not res.get("ok"):
            return res
    return {"ok": True}


def _setup_record_only(_name):
    return {"ok": True}


def _setup_callsign_ok(c):
    if not c:
        return True
    return bool(re.match(r"^[A-Za-z0-9/\-]{3,16}$", c))


def _setup_grid_ok(g):
    if not g:
        return True
    return bool(re.match(r"^[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?$", g))


def _setup_preflight_blockers(selected, payload, online):
    blocked = []
    sel = set(selected)
    win = payload.get("winlink") or {}
    st = payload.get("station") or {}

    if "winlink" in sel and not (win.get("password") or "").strip():
        blocked.append({
            "feature": "winlink",
            "reason_code": "WINLINK_PASSWORD_REQUIRED",
            "reason_text": "winlink password is required when winlink is selected",
        })
    if not _setup_callsign_ok((st.get("callsign") or "").strip()):
        blocked.append({
            "feature": "station",
            "reason_code": "INVALID_STATION_CALLSIGN",
            "reason_text": "invalid station callsign format",
        })
    if not _setup_grid_ok((st.get("grid") or "").strip()):
        blocked.append({
            "feature": "station",
            "reason_code": "INVALID_STATION_GRID",
            "reason_text": "invalid station grid format",
        })

    wifi = payload.get("wifi") or {}
    wifi_mode = (wifi.get("mode") or "none").strip().lower()
    if wifi_mode not in {"none", "client", "ap"}:
        blocked.append({
            "feature": "wifi",
            "reason_code": "INVALID_INPUT",
            "reason_text": "wifi.mode must be one of: none, client, ap",
        })
    if wifi_mode == "client":
        ssid = (wifi.get("ssid") or "").strip()
        psk = wifi.get("password") or ""
        if not ssid:
            blocked.append({
                "feature": "wifi",
                "reason_code": "INVALID_INPUT",
                "reason_text": "wifi.ssid is required for wifi client mode",
            })
        if not (8 <= len(psk) <= 63):
            blocked.append({
                "feature": "wifi",
                "reason_code": "INVALID_INPUT",
                "reason_text": "wifi.password must be 8-63 chars for wifi client mode",
            })

    if sys.platform != "linux":
        linux_only = {
            "service-controls", "ap-fallback", "graywolf", "winlink", "kiwix",
            "openwebrx", "adsb", "rtl-sdr-feed", "gps", "dra-pi-rx-led", "rtc",
            "pi-headless", "pi-local-monitor", "pi-small-screen-7", "cm4stack", "pi-e-ink",
            "wikipedia",
        }
        for key in sorted(sel & linux_only):
            blocked.append({
                "feature": key,
                "reason_code": "UNSUPPORTED_PLATFORM",
                "reason_text": f"{key} requires linux target platform",
            })

    try:
        import psutil
        d = None
        for mount in ("/mnt/ssd", "/mnt/emmc", "/", "C:\\"):
            try:
                d = psutil.disk_usage(mount)
                break
            except Exception:
                continue
        if d is not None and (d.free / 1e9) < 1.0:
            blocked.append({
                "feature": "system",
                "reason_code": "DISK_CRITICAL",
                "reason_text": "less than 1 GB free disk space",
            })
    except Exception:
        pass

    internet_required = {
        "graywolf", "winlink", "kiwix", "openwebrx", "adsb",
        "gps", "cm4stack", "wikipedia",
    }
    if not online:
        for key in sorted(sel & internet_required):
            blocked.append({
                "feature": key,
                "reason_code": "INTERNET_REQUIRED",
                "reason_text": f"internet required for {key}",
            })
    return blocked


def _setup_wifi_mode(payload):
    wifi = payload.get("wifi") or {}
    return (wifi.get("mode") or "none").strip().lower()


def _setup_apply_wifi(payload):
    wifi = payload.get("wifi") or {}
    mode = _setup_wifi_mode(payload)
    if mode == "none":
        return {"ok": True, "skipped": True, "reason_text": "wifi unchanged"}
    if sys.platform != "linux":
        return {"ok": False, "reason_code": "UNSUPPORTED_PLATFORM", "reason_text": "wifi orchestration requires linux"}
    if not os.path.exists(NETCTL_PATH):
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": "wifi controls not installed; run scripts/enable-ap-fallback.py",
        }

    if mode == "client":
        ssid = (wifi.get("ssid") or "").strip()
        psk = wifi.get("password") or ""
        ok, out, err = _netctl("connect", ssid, stdin=psk + "\n", timeout=60)
        if not ok:
            return {
                "ok": False,
                "reason_code": "INSTALL_FAILED",
                "reason_text": (err or out or "wifi connect failed").strip()[:300],
            }
        return {"ok": True, "mode": "client", "ssid": ssid, "requires_reboot": True}

    if mode == "ap":
        ok, out, err = _netctl("forget", timeout=30)
        if not ok:
            return {
                "ok": False,
                "reason_code": "INSTALL_FAILED",
                "reason_text": (err or out or "wifi ap fallback failed").strip()[:300],
            }
        return {"ok": True, "mode": "ap", "requires_reboot": True}

    return {"ok": False, "reason_code": "INVALID_INPUT", "reason_text": f"unsupported wifi mode: {mode}"}


def _setup_write_station(payload):
    st = payload.get("station") or {}
    callsign = (st.get("callsign") or "").strip().upper()
    grid = (st.get("grid") or "").strip().upper()
    lat = st.get("lat")
    lon = st.get("lon")
    if not (callsign or grid or lat is not None or lon is not None):
        return
    dst = config_paths.station_json(SUITE_ROOT)
    os.makedirs(config_paths.config_dir(SUITE_ROOT), exist_ok=True)
    body = {
        "callsign": callsign or "N0CALL",
        "grid": grid,
        "lat": lat,
        "lon": lon,
        "updated": _setup_iso_now(),
    }
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2)
        fh.write("\n")


def _setup_registry():
    # Expanded registry maps spec feature keys to installer scripts.
    return {
        "server": SE.FeatureSpec(
            key="server",
            dependencies=[],
            install_fn=_setup_server_install,
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "webssh": SE.FeatureSpec(
            key="webssh",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/install-webssh.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "service-controls": SE.FeatureSpec(
            key="service-controls",
            dependencies=["server"],
            install_fn=_setup_service_controls_install,
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "ap-fallback": SE.FeatureSpec(
            key="ap-fallback",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/enable-ap-fallback.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "graywolf": SE.FeatureSpec(
            key="graywolf",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/install-graywolf.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "winlink": SE.FeatureSpec(
            key="winlink",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/install-winlink.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "kiwix": SE.FeatureSpec(
            key="kiwix",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/install-kiwix.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "openwebrx": SE.FeatureSpec(
            key="openwebrx",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/install-openwebrx.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "adsb": SE.FeatureSpec(
            key="adsb",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("services/adsb/install.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "rtl-sdr-feed": SE.FeatureSpec(
            key="rtl-sdr-feed",
            dependencies=[],
            install_fn=lambda: _setup_run_chain([
                {"script": "features/rtl-sdr/install-rtl-sdr.py"},
                {"script": "features/rtl-sdr/enable-rtl-sdr.py"},
            ]),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "gps": SE.FeatureSpec(
            key="gps",
            dependencies=[],
            install_fn=lambda: _setup_run_script("features/gps/install-gps.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "dra-pi-rx-led": SE.FeatureSpec(
            key="dra-pi-rx-led",
            dependencies=["graywolf"],
            install_fn=lambda: _setup_run_chain([
                {"script": "features/dra-audio-interface/enable-dra-pi.py"},
                {"script": "features/dra-audio-interface/enable-dra-rx-led.py"},
            ]),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "rtc": SE.FeatureSpec(
            key="rtc",
            dependencies=[],
            install_fn=lambda: _setup_run_script("features/rtc-hat/enable-rtc.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "pi-headless": SE.FeatureSpec(
            key="pi-headless",
            dependencies=["server"],
            install_fn=lambda: _setup_record_only("pi-headless"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "pi-local-monitor": SE.FeatureSpec(
            key="pi-local-monitor",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/enable-autostart-pi.py", ["--with-browser"]),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
        ),
        "pi-small-screen-7": SE.FeatureSpec(
            key="pi-small-screen-7",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/enable-autostart-pi.py", ["--7inch"]),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
        ),
        "cm4stack": SE.FeatureSpec(
            key="cm4stack",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("displays/cm4stack/install-cm4stack.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
            requires_reboot=True,
        ),
        "pi-e-ink": SE.FeatureSpec(
            key="pi-e-ink",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("displays/e-ink/install-e-ink.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "fcc": SE.FeatureSpec(
            key="fcc",
            dependencies=["server"],
            install_fn=lambda: _setup_run_script("scripts/install-fcc-database.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "wikipedia": SE.FeatureSpec(
            key="wikipedia",
            dependencies=["kiwix"],
            install_fn=lambda: _setup_run_script("scripts/download-wikipedia.py"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "repeaterbook": SE.FeatureSpec(
            key="repeaterbook",
            dependencies=[],
            install_fn=lambda: _setup_record_only("repeaterbook"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "forms": SE.FeatureSpec(
            key="forms",
            dependencies=[],
            install_fn=lambda: _setup_record_only("forms"),
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
    }


def _setup_emit_event(job_id, event):
    line = json.dumps(event, sort_keys=True)
    with _setup_lock:
        job = _setup_jobs.get(job_id)
        if not job:
            return
        job["events"].append(line)
        job["updatedAt"] = _setup_iso_now()
        kind = event.get("event")
        if kind == "stage_started":
            job["currentFeature"] = event.get("feature")
            job["currentStage"] = event.get("stage")
        elif kind in ("feature_terminal", "feature_skipped"):
            f = event.get("feature")
            if f:
                st = {
                    "feature": f,
                    "status": event.get("status"),
                    "reasonCode": event.get("reasonCode"),
                    "reasonText": event.get("reasonText"),
                }
                job["featureStates"][f] = st
        elif kind == "job_finished":
            job["exitCode"] = event.get("exitCode", 1)
            job["summary"] = event.get("summary") or {}
        elif kind == "job_canceled":
            job["status"] = "canceled"


def _setup_cancel_requested(job_id):
    with _setup_lock:
        return job_id in _setup_cancel_requests


def _setup_run_job(job_id, plan_obj, payload):
    global _setup_active_job
    reg = _setup_registry()
    run_opts = SE.RunOptions(
        sequential=True,
        auto_start_services=False,
        job_id=job_id,
        cancel_requested=lambda: _setup_cancel_requested(job_id),
    )
    try:
        _setup_write_station(payload or {})
    except Exception as exc:
        blocker = {
            "feature": "station",
            "reason_code": "WRITE_FAILED",
            "reason_text": str(exc),
        }
        plan_obj = SE.SetupPlan(
            selected_features=plan_obj.selected_features,
            ordered_features=plan_obj.ordered_features,
            blocked=[*plan_obj.blocked, blocker],
        )
    _states, _blocked, summary = SE.run_plan(
        plan_obj,
        run_opts,
        reg,
        event_sink=lambda e: _setup_emit_event(job_id, e),
    )

    wifi_result = None
    if not _blocked and _setup_wifi_mode(payload or {}) != "none":
        _setup_emit_event(job_id, {
            "schemaVersion": "1.0",
            "event": "feature_started",
            "jobId": job_id,
            "ts": _setup_iso_now(),
            "feature": "wifi",
            "position": len(plan_obj.ordered_features) + 1,
            "total": len(plan_obj.ordered_features) + 1,
        })
        _setup_emit_event(job_id, {
            "schemaVersion": "1.0",
            "event": "stage_started",
            "jobId": job_id,
            "ts": _setup_iso_now(),
            "feature": "wifi",
            "stage": "install",
        })
        wifi_result = _setup_apply_wifi(payload or {})
        _setup_emit_event(job_id, {
            "schemaVersion": "1.0",
            "event": "stage_completed",
            "jobId": job_id,
            "ts": _setup_iso_now(),
            "feature": "wifi",
            "stage": "install",
            "ok": bool(wifi_result.get("ok", False)),
            "durationMs": 0,
        })
        _setup_emit_event(job_id, {
            "schemaVersion": "1.0",
            "event": "feature_terminal",
            "jobId": job_id,
            "ts": _setup_iso_now(),
            "feature": "wifi",
            "status": "installed_needs_reboot" if wifi_result.get("ok") else "install_failed",
            "reasonCode": None if wifi_result.get("ok") else wifi_result.get("reason_code", "INSTALL_FAILED"),
            "reasonText": None if wifi_result.get("ok") else wifi_result.get("reason_text", "wifi setup failed"),
        })

    canceled = _setup_cancel_requested(job_id)
    exit_code = 130 if canceled else (1 if _blocked else SE.terminal_exit_code(summary))
    with _setup_lock:
        job = _setup_jobs.get(job_id)
        if job:
            if exit_code == 0:
                job["status"] = "completed"
            elif exit_code == 130:
                job["status"] = "canceled"
            else:
                job["status"] = "failed"
            job["updatedAt"] = _setup_iso_now()
            if not job.get("summary"):
                job["summary"] = summary.__dict__
            for item in summary.features:
                job["featureStates"][item.get("feature")] = {
                    "feature": item.get("feature"),
                    "status": item.get("status"),
                    "reasonCode": item.get("reasonCode"),
                    "reasonText": item.get("reasonText"),
                }
            if wifi_result is not None:
                job["featureStates"]["wifi"] = {
                    "feature": "wifi",
                    "status": "installed_needs_reboot" if wifi_result.get("ok") else "install_failed",
                    "reasonCode": None if wifi_result.get("ok") else wifi_result.get("reason_code", "INSTALL_FAILED"),
                    "reasonText": None if wifi_result.get("ok") else wifi_result.get("reason_text", "wifi setup failed"),
                }
                if "wifi" not in job.get("orderedFeatures", []):
                    job["orderedFeatures"].append("wifi")
        if _setup_active_job == job_id:
            _setup_active_job = None
        _setup_cancel_requests.discard(job_id)


@app.route("/api/setup/permissions")
def api_setup_permissions():
    granted = bool(sys.platform == "linux" and os.path.exists("/etc/sudoers.d/oasis-service-controls"))
    return jsonify({
        "ok": True,
        "serviceControlsGranted": granted,
        "localCommand": "python3 scripts/enable-service-controls.py",
        "message": "Run command in WebSSH and click Re-check" if not granted else "permissions granted",
    })


@app.route("/api/setup/hardware-detect")
def api_setup_hardware_detect():
    scan = HD_detect.scan()
    lsusb = []
    if sys.platform == "linux":
        try:
            r = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                lsusb = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        except Exception:
            lsusb = []
    has_dra_driver = bool(scan.get("alsa") or scan.get("serial"))
    return jsonify({
        "ok": True,
        "lsusb": lsusb,
        "detected": scan,
        "draPiDriverReady": has_dra_driver,
    })


@app.route("/api/setup/plan", methods=["POST"])
def api_setup_plan():
    data = request.get_json(silent=True) or {}
    selected = data.get("selectedFeatures")
    if not isinstance(selected, list):
        selected = ["server"]

    online = bool(has_internet())
    reg = _setup_registry()
    plan = SE.resolve_plan(selected, reg)
    blockers = _setup_preflight_blockers(plan.ordered_features, data, online)
    if blockers:
        plan = SE.SetupPlan(
            selected_features=plan.selected_features,
            ordered_features=plan.ordered_features,
            blocked=[*plan.blocked, *blockers],
        )

    reboot_reasons = []
    for key in plan.ordered_features:
        spec = reg.get(key)
        if spec and spec.requires_reboot:
            reboot_reasons.append(key)
    if _setup_wifi_mode(data) != "none":
        reboot_reasons.append("wifi")

    ordered_for_response = list(plan.ordered_features)
    if _setup_wifi_mode(data) != "none":
        ordered_for_response.append("wifi")

    plan_id = f"setup-plan-{uuid.uuid4().hex[:7]}"
    with _setup_lock:
        _setup_cleanup_plans_locked()
        _setup_plans[plan_id] = {"plan": plan, "created_at": _setup_now(), "payload": data}

    return jsonify({
        "ok": True,
        "planId": plan_id,
        "internet": {"connected": online},
        "permissions": {
            "serviceControlsGranted": bool(sys.platform == "linux" and os.path.exists("/etc/sudoers.d/oasis-service-controls")),
            "localCommand": "python3 scripts/enable-service-controls.py",
        },
        "orderedFeatures": ordered_for_response,
        "resolvedFeatures": ordered_for_response,
        "preflight": {"blocked": plan.blocked, "warnings": []},
        "reboot": {"requiredIfRunNow": bool(reboot_reasons), "reasons": reboot_reasons},
    })


@app.route("/api/setup/run", methods=["POST"])
def api_setup_run():
    global _setup_active_job
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    plan_id = (data.get("planId") or "").strip()
    if not plan_id:
        return jsonify({"ok": False, "error": "planId required"}), 400

    with _setup_lock:
        if _setup_active_job and _setup_jobs.get(_setup_active_job, {}).get("status") == "running":
            return jsonify({
                "ok": False,
                "error": "setup already running",
                "reasonCode": "JOB_LOCKED",
                "activeJobId": _setup_active_job,
            }), 409

        rec = _setup_plans.get(plan_id)
        if not rec:
            return jsonify({"ok": False, "error": "unknown planId"}), 404
        plan_obj = rec.get("plan")
        payload = rec.get("payload") or {}

        job_id = f"setup-job-{uuid.uuid4().hex[:7]}"
        _setup_jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "cancelRequested": False,
            "startedAt": _setup_iso_now(),
            "updatedAt": _setup_iso_now(),
            "currentFeature": None,
            "currentStage": None,
            "events": [],
            "summary": {},
            "featureStates": {k: {"feature": k, "status": "pending", "reasonCode": None, "reasonText": None} for k in plan_obj.ordered_features},
            "orderedFeatures": list(plan_obj.ordered_features),
        }
        if _setup_wifi_mode(payload) != "none":
            _setup_jobs[job_id]["featureStates"]["wifi"] = {
                "feature": "wifi", "status": "pending", "reasonCode": None, "reasonText": None
            }
            _setup_jobs[job_id]["orderedFeatures"].append("wifi")
        _setup_active_job = job_id
        _setup_cancel_requests.discard(job_id)

    t = threading.Thread(target=_setup_run_job, args=(job_id, plan_obj, payload), daemon=True)
    t.start()
    return jsonify({"ok": True, "jobId": job_id, "status": "running", "startedAt": _setup_jobs[job_id]["startedAt"]})


@app.route("/api/setup/jobs/<job_id>")
def api_setup_job(job_id):
    with _setup_lock:
        job = _setup_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        features = []
        for k in job.get("orderedFeatures", []):
            if k in job["featureStates"]:
                features.append(job["featureStates"][k])
        summary = job.get("summary") or {"green": 0, "amber": 0, "red": 0, "gray": len(features)}
        payload = {
            "ok": True,
            "job": {
                "id": job["id"],
                "status": job["status"],
                "currentFeature": job.get("currentFeature"),
                "currentStage": job.get("currentStage"),
                "startedAt": job.get("startedAt"),
                "updatedAt": job.get("updatedAt"),
            },
            "features": features,
            "summary": {
                "green": int(summary.get("green", 0)),
                "amber": int(summary.get("amber", 0)),
                "red": int(summary.get("red", 0)),
                "gray": int(summary.get("gray", 0)),
            },
        }
    return jsonify(payload)


@app.route("/api/setup/jobs/<job_id>/log")
def api_setup_job_log(job_id):
    cursor_raw = request.args.get("cursor", "0")
    try:
        cursor = max(0, int(cursor_raw))
    except ValueError:
        cursor = 0
    with _setup_lock:
        job = _setup_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        lines = job.get("events", [])[cursor:]
        next_cursor = cursor + len(lines)
        eof = job.get("status") in {"completed", "failed", "canceled"}
    return jsonify({"ok": True, "cursor": cursor, "nextCursor": next_cursor, "lines": lines, "eof": eof})


@app.route("/api/setup/cancel", methods=["POST"])
def api_setup_cancel():
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    target = (data.get("jobId") or "").strip()
    with _setup_lock:
        job_id = target or _setup_active_job
        if not job_id:
            return jsonify({"ok": False, "error": "no active setup job"}), 409
        job = _setup_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        if job.get("status") in {"completed", "failed", "canceled"}:
            return jsonify({"ok": True, "jobId": job_id, "status": job.get("status"), "alreadyTerminal": True})
        _setup_cancel_requests.add(job_id)
        job["cancelRequested"] = True
        job["updatedAt"] = _setup_iso_now()
        job["events"].append(json.dumps({
            "schemaVersion": "1.0",
            "event": "job_cancel_requested",
            "jobId": job_id,
            "ts": _setup_iso_now(),
            "reason": "user requested cancel",
        }, sort_keys=True))
    return jsonify({
        "ok": True,
        "jobId": job_id,
        "status": "cancel_requested",
        "message": "cancel requested; current feature step will finish before stop",
    })


@app.route("/api/setup/reboot", methods=["POST"])
def api_setup_reboot():
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False, "error": "reboot unavailable"}), 200
    try:
        subprocess.run(["sudo", "-n", "reboot"], capture_output=True, text=True, timeout=5)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "message": "reboot requested"})


@app.before_request
def _portable_gate():
    """In portable mode, 404 the Winlink/APRS surfaces (API + pages) for any
    daemon-backed feature not in PORTABLE_FEATURES. No-op when unlocked."""
    if PORTABLE_FEATURES is None:
        return None
    allowed = set(PORTABLE_FEATURES)
    path = request.path or "/"
    for feat, prefixes in _PORTABLE_BLOCK.items():
        if feat not in allowed and any(path.startswith(p) for p in prefixes):
            return jsonify({"ok": False, "error": "disabled in portable mode"}), 404
    return None


@app.after_request
def _inject_theme_toggle(resp):
    try:
        if resp.mimetype != "text/html":
            return resp
        path = request.path or "/"
        if any(path.startswith(p) for p in _THEME_SKIP_PREFIXES):
            return resp
        if resp.direct_passthrough:
            resp.direct_passthrough = False
        html = resp.get_data(as_text=True)
        if "</head>" not in html or "/static/theme.js" in html:
            return resp
        resp.set_data(html.replace("</head>", _THEME_SNIPPET + "</head>", 1))
    except Exception:
        # The toggle is a nicety — never let injection break a page.
        pass
    return resp


@app.route("/")
def index():
    """Smart home: JS reads localStorage and redirects to the small-screen layout or index.html."""
    return '''<!doctype html><meta charset="utf-8">
<script>
window.location.replace(
  localStorage.getItem("oasis_layout") === "7inch" ? "/small-screen/index7.html" : "/index.html"
);
</script>
<noscript><meta http-equiv="refresh" content="0;url=/index.html"></noscript>''', 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route("/station.json")
def serve_station_json():
    """Serve the operator's station profile, now stored under configuration/."""
    path = config_paths.station_json(SUITE_ROOT)
    if not os.path.exists(path):
        return jsonify({}), 404
    return send_file(path, mimetype="application/json")


@app.route("/map-assets/<path:filename>")
def map_assets(filename):
    """Serve APRS map assets from the service-owned static directory."""
    return send_from_directory(MAP_ASSETS, filename)


@app.route("/server/aprs/<path:filename>")
def aprs_static(filename):
    """Serve the APRS UI from the service-owned static directory."""
    return send_from_directory(APRS_DIR, filename)


@app.route("/server/winlink/<path:filename>")
def winlink_static(filename):
    """Serve the Winlink UI from the service-owned static directory."""
    return send_from_directory(WINLINK_DIR, filename)


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
    """Serve the FCC call-sign lookup page from the service-owned FCC package."""
    return send_from_directory(FCC_DIR, "lookup.html")


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


@app.route("/api/lookup/name")
def api_lookup_name():
    """
    Search FCC licenses by last name (required) and optional first name prefix.
    Query: ?last=SMITH  or  ?last=SMITH&first=JOHN
    Returns up to 50 active-license records sorted by last name then first name.
    Requires EN_name.idx (built by install-fcc-database.py).
    """
    last  = (request.args.get("last")  or "").strip()
    first = (request.args.get("first") or "").strip()
    if not last:
        return jsonify({"ok": False, "error": "Please enter a last name."}), 400
    if len(last) < 2:
        return jsonify({"ok": False, "error": "Last name must be at least 2 characters."}), 400
    try:
        results = lookup.lookup_by_name(last, first or None, zip_table=ZIP_TABLE)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True, "count": len(results), "results": results,
                    "query": {"last": last.upper(), "first": first.upper() or None}})


@app.route("/api/lookup/grid")
def api_lookup_grid():
    """
    Search FCC licenses by Maidenhead grid square prefix.
    Query: ?grid=CN87  (2, 4, or 6 characters)
    Returns up to 100 active-license records for that grid area.
    Requires EN_grid.idx (built by install-fcc-database.py after zipcodes.csv exists).
    """
    grid = (request.args.get("grid") or "").strip()
    if not grid:
        return jsonify({"ok": False, "error": "Please enter a grid square (e.g. CN87)."}), 400
    if len(grid) < 2:
        return jsonify({"ok": False, "error": "Grid prefix must be at least 2 characters."}), 400
    try:
        results = lookup.lookup_by_grid(grid, zip_table=ZIP_TABLE)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True, "count": len(results), "results": results,
                    "query": {"grid": grid.upper()}})


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


@app.route("/api/save-ics205", methods=["POST"])
def api_save_ics205():
    """Save an ICS-205 plan as JSON into static/ics-205/saved/.

    Expects JSON body: { "filename": "ics-205-<...>.json", "content": "<json text>" }
    Only filenames ending in .json and containing no path separators are accepted.
    """
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    content  = data.get("content", "")

    # Validate filename — no path traversal, .json only
    if not filename or os.sep in filename or "/" in filename or not filename.endswith(".json"):
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    saved_dir = os.path.join(SUITE_ROOT, "static", "ics-205", "saved")
    os.makedirs(saved_dir, exist_ok=True)
    dest = os.path.realpath(os.path.join(saved_dir, filename))
    if not dest.startswith(os.path.realpath(saved_dir) + os.sep):
        return jsonify({"ok": False, "error": "Path traversal rejected"}), 403

    try:
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "saved": os.path.join("static", "ics-205", "saved", filename)})


@app.route("/api/list-ics205")
def api_list_ics205():
    """List saved ICS-205 plans in static/ics-205/saved/, newest first.

    The files themselves are fetched directly as static assets
    (/static/ics-205/saved/<name>); this only enumerates them.
    """
    saved_dir = os.path.join(SUITE_ROOT, "static", "ics-205", "saved")
    files = []
    if os.path.isdir(saved_dir):
        for name in os.listdir(saved_dir):
            if not name.endswith(".json"):
                continue
            try:
                st = os.stat(os.path.join(saved_dir, name))
            except OSError:
                continue
            files.append({"name": name, "size": st.st_size, "mtime": int(st.st_mtime)})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify({"ok": True, "files": files})


@app.route("/api/list-chirp")
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
    return jsonify({"ok": True, "files": files})


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
        "name_index_present": os.path.exists(lookup.NAME_IDX_PATH),
        "grid_index_present": os.path.exists(lookup.GRID_IDX_PATH),
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

    ALLOWED = {"graywolf", "kiwix", "webssh", "aprs_api", "winlink", "openwebrx"}
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
    "graywolf", "graywolf-api", "pat", "pat-direwolf", "kiwix", "webssh",
    "aprs-sdr-feed", "openwebrx", "gpsd", "oasis",
    "dump1090-fa", "adsb-api",
}

# Units the dashboard may start/stop/restart. Everything in _OASIS_SERVICES
# EXCEPT the web server itself (stopping it kills the dashboard) and gpsd (time
# infrastructure — status-only, no power button).
_CONTROLLABLE_SERVICES = _OASIS_SERVICES - {"oasis", "gpsd"}
_SERVICE_ACTIONS = {"start", "stop", "restart"}

# Units whose boot state tracks their running state: starting also `enable`s them
# (comes back after reboot), stopping also `disable`s them (stays off). Everything
# else is transient (plain start/stop; boot state left untouched). restart never
# changes boot state.
_PERSIST_BOOT_STATE = {"kiwix"}


def _systemctl_seq(unit, verbs):
    """Best-effort `sudo -n systemctl <verb> <unit>.service` for each verb in
    order (used for conflict services — failures are tolerated/ignored)."""
    for verb in verbs:
        try:
            subprocess.run(["sudo", "-n", "systemctl", verb, f"{unit}.service"],
                           capture_output=True, text=True, timeout=30)
        except Exception:
            pass


def _apply_hardware_async():
    """Best-effort: re-template every hardware-claiming service's device config
    after an assignment change. Failure here does not undo the already-
    persisted assignment (HW.assign/release already wrote hardware.json) — it
    just means the binding isn't live until the next successful apply, exactly
    like _systemctl_seq's own tolerated-failure philosophy."""
    venv_python = os.path.join(SUITE_ROOT, ".venv", "bin", "python3")
    script = os.path.join(SUITE_ROOT, "scripts", "apply_hardware.py")
    try:
        subprocess.run(["sudo", "-n", venv_python, script],
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

    # gpsd is socket-activated: gpsd.service is only "active" while a client is
    # connected and goes idle otherwise, but it's available whenever gpsd.socket
    # is listening. Treat a live socket as active so the card isn't falsely down.
    if name == "gpsd" and active != "active":
        try:
            sock = subprocess.run(["systemctl", "is-active", "gpsd.socket"],
                                  capture_output=True, text=True, timeout=5).stdout.strip()
            if sock == "active":
                active = "active"
                if not enabled:
                    enabled = "enabled"
        except Exception:
            pass

    return jsonify({
        "ok":        active == "active",
        "service":   name,
        "active":    active or "unknown",
        "enabled":   enabled or "not-found",
        "installed": bool(enabled),     # is-enabled prints nothing for absent units
    })


# ── RTL-SDR → GrayWolf feed flow probe ────────────────────────────────────────
# The aprs-sdr-feed.service unit is `rtl_fm … | socat … UDP-SENDTO:127.0.0.1:7355`.
# systemctl is-active only proves the pipe's tail (socat) is alive — a dongle
# yanked from USB leaves the unit "active" while ZERO audio reaches GrayWolf.
# To prove audio is actually moving we passively sniff the loopback feed with
# tcpdump (libpcap copies datagrams; it never steals them from GrayWolf's reader).
# socat emits ~50 datagrams/s (one per 20 ms audio chunk), so a short capture
# either fills fast (healthy) or times out empty (silent/dead feed).
FEED_FLOW_PORT     = 7355     # matches features/rtl-sdr/enable-rtl-sdr.py default + the sudoers rule
_FEED_FLOW_NPKTS   = 10       # capture up to N packets, then tcpdump exits
_FEED_FLOW_TIMEOUT = 1.0      # hard cap (s): bounds a dead feed's response time
_FEED_FLOW_NOMINAL = 50       # pkt/s at full feed — the UI scales the bar to this
# Pinned tcpdump argv (sans sudo + binary path). MUST match the OASIS_SNIFF
# Cmnd_Alias in scripts/enable-service-controls.py token-for-token, or `sudo -n`
# denies it. The port is baked in (not client-supplied) so there is no argument-
# injection surface on the privileged path.
_FEED_FLOW_ARGS = ["-ni", "lo", "-l", "-c", str(_FEED_FLOW_NPKTS),
                   "udp", "port", str(FEED_FLOW_PORT)]


def _resolve_tcpdump():
    """tcpdump path, PATH first then the standard sbin/bin dirs (the WSGI server
    can run under a trimmed systemd PATH). Mirrors api_health_binary's lookup."""
    import shutil
    path = shutil.which("tcpdump")
    if not path:
        for d in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
                  "/usr/bin", "/sbin", "/bin"):
            cand = os.path.join(d, "tcpdump")
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                path = cand
                break
    return path


@app.route("/api/health/feed-flow")
def api_health_feed_flow():
    """Report whether UDP datagrams are actually flowing on the RTL-SDR feed port
    (a data-flow health check the systemd is-active probe can't give). Returns
    packet rate over a short passive capture. Linux + scoped sudo (tcpdump) only."""
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False, "reason": "not-linux"})

    tcpdump = _resolve_tcpdump()
    if not tcpdump:
        return jsonify({"ok": False, "supported": True, "reason": "tcpdump-missing"})

    argv = ["sudo", "-n", tcpdump, *_FEED_FLOW_ARGS]
    t0 = time.monotonic()
    out = ""
    err = ""
    rc = None
    timed_out = False
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=_FEED_FLOW_TIMEOUT)
        out, err, rc = r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        # Healthy feeds exit on -c N before this; a timeout means the feed is slow
        # or dead. Partial stdout (any trickle captured) is still on the exception.
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
        timed_out = True
    except Exception as e:
        return jsonify({"ok": False, "supported": True, "reason": "probe-error",
                        "error": str(e)})
    elapsed = max(time.monotonic() - t0, 1e-3)

    packets = sum(1 for ln in out.splitlines() if ln.strip())

    # No packets AND tcpdump itself failed (not just an empty capture) → tell the
    # operator whether it's a missing sudo grant vs. some other tcpdump error.
    if packets == 0 and not timed_out and rc not in (0, None):
        low = (err or "").lower()
        if "password is required" in low or "not allowed to execute" in low or "a terminal is required" in low:
            return jsonify({"ok": False, "supported": True, "reason": "no-privilege"})
        return jsonify({"ok": False, "supported": True, "reason": "probe-error",
                        "error": (err or "").strip()[:200]})

    pps = round(packets / elapsed, 1)
    return jsonify({
        "ok": True, "supported": True, "port": FEED_FLOW_PORT,
        "packets": packets, "elapsed_ms": round(elapsed * 1000),
        "pps": pps, "nominal_pps": _FEED_FLOW_NOMINAL,
        "flowing": packets > 0,
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

    # Hardware-aware gate: starting a unit that claims a physical device (SDR /
    # radio port) is refused unless its logical service is assigned a present
    # device. Exclusive allocation (common/hardware.py) means the device can
    # never be held by ANOTHER service, so this can only fail for THIS
    # service's own assignment state — never cross-service contention (that's
    # refused earlier, at assign time, via /api/hardware/assign, added in a
    # later task).
    affected = []
    if action == "start":
        inv = HW.load(SUITE_ROOT)
        hw_service = HW.service_for_unit(inv, unit)
        if hw_service:
            ok, reason = HW.can_start(inv, hw_service)
            if not ok:
                return jsonify({"ok": False, "error": f"cannot start {unit}: {reason}",
                                "reason": reason}), 409

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


# ── Wi-Fi controls (scan / join known networks; AP fallback) ──────────────────
# The dashboard Wi-Fi picker drives NetworkManager through a single pinned helper,
# /usr/local/bin/oasis-netctl, run as `sudo -n`. scripts/enable-ap-fallback.py
# installs the helper + a sudoers rule scoped to exactly its four subcommands, so
# no credential ever reaches this layer. The helper also backs the AP-fallback
# watcher (oasis-netwatch) that keeps the dashboard reachable off-grid.
NETCTL_PATH = "/usr/local/bin/oasis-netctl"       # must match enable-ap-fallback.py
AP_CON_NAME = "OASIS-AP"                           # must match enable-ap-fallback.py


def _netctl(*sub, stdin=None, timeout=45):
    """Run `sudo -n oasis-netctl <sub…>`; returns (ok, stdout, stderr).
    ok=False (and empty stdout) when the helper is missing or unauthorised."""
    if sys.platform != "linux" or not os.path.exists(NETCTL_PATH):
        return False, "", "unavailable"
    try:
        r = subprocess.run(["sudo", "-n", NETCTL_PATH, *sub],
                           input=stdin, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", str(exc)
    return r.returncode == 0, r.stdout, r.stderr


def _nmcli_unescape(field):
    """Undo nmcli -t terse-mode escaping ('\\:' -> ':', '\\\\' -> '\\')."""
    return field.replace(r"\:", ":").replace(r"\\", "\\")


def _split_terse(line):
    """Split an nmcli -t line on unescaped ':' separators."""
    parts, buf, esc = [], "", False
    for ch in line:
        if esc:
            buf += "\\" + ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [_nmcli_unescape(p) for p in parts]


@app.route("/api/hardware/devices")
def api_hardware_devices():
    """Live device-allocation list for the dashboard HARDWARE card."""
    inv = HW.load(SUITE_ROOT)
    return jsonify({"devices": HW.device_states(inv), "errors": inv.errors})


@app.route("/api/hardware/detect")
def api_hardware_detect():
    """Enumerate attached hardware (RTL-SDRs, ALSA cards, serial devices) as
    candidates for the assignment editor. Read-only — never writes
    hardware.json (spec §6)."""
    return jsonify(HD_detect.scan())


@app.route("/api/hardware/assign", methods=["POST"])
def api_hardware_assign():
    """Assign a device to a logical service (exclusive — refused if the device
    is already held by a DIFFERENT service; see HW.can_assign)."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    service = (data.get("service") or "").strip()
    device_id = (data.get("device_id") or "").strip()
    if service not in HW.SERVICE_UNITS:
        return jsonify({"ok": False, "error": "unknown service"}), 400
    inv = HW.load(SUITE_ROOT)
    ok, holder = HW.can_assign(inv, service, device_id)
    if not ok:
        if holder:
            return jsonify({"ok": False, "error": f"{device_id} is in use by {holder}",
                            "holder": holder}), 409
        return jsonify({"ok": False,
                        "error": "device not declared, or wrong kind for this service"}), 400
    HW.assign(SUITE_ROOT, inv, service, device_id)
    _apply_hardware_async()
    return jsonify({"ok": True})


@app.route("/api/hardware/release", methods=["POST"])
def api_hardware_release():
    """Unassign a service's device. If the service is running, its unit(s) are
    stopped first (frees the device) — nothing else auto-restarts."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    service = (data.get("service") or "").strip()
    if service not in HW.SERVICE_UNITS:
        return jsonify({"ok": False, "error": "unknown service"}), 400
    inv = HW.load(SUITE_ROOT)

    def _stop(unit):
        _systemctl_seq(unit, ["stop"])

    HW.release(SUITE_ROOT, inv, service, stop_fn=_stop)
    _apply_hardware_async()
    return jsonify({"ok": True})


@app.route("/api/hardware/burn-serial", methods=["POST"])
def api_hardware_burn_serial():
    """Burn a unique serial onto the sole connected, unclaimed RTL-SDR dongle
    (see common/hardware_detect.can_burn_serial for the exclusive-access +
    ambiguity guard). The actual eeprom write runs via a validating wrapper
    script authorized by a single pinned sudoers entry (see
    scripts/burn_dongle_serial.py / scripts/enable-service-controls.py). This
    route's own regex is a first-line check for a fast 400; the wrapper script
    re-validates independently and is the actual trust boundary — do not treat
    this route's check as sufficient on its own."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    new_serial = data.get("serial") or ""
    if not re.match(r'^[A-Za-z0-9]{1,32}\Z', new_serial):
        return jsonify({"ok": False, "error": "invalid serial format"}), 400

    candidates = HD_detect.scan().get("rtl_sdr", [])
    ok, reason = HD_detect.can_burn_serial(candidates, is_active=HW._default_is_active)
    if not ok:
        return jsonify({"ok": False, "error": reason}), 409

    venv_python = os.path.join(SUITE_ROOT, ".venv", "bin", "python3")
    script = os.path.join(SUITE_ROOT, "scripts", "burn_dongle_serial.py")
    try:
        r = subprocess.run(["sudo", "-n", venv_python, script, new_serial],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    if r.returncode != 0:
        return jsonify({"ok": False, "error": (r.stderr or r.stdout or "").strip()[:200]}), 500
    return jsonify({"ok": True})


def _current_ssid():
    """The SSID the radio is associated with, via read-only iwgetid (no sudo).
    NetworkManager profile names can be netplan-style (netplan-wlan-<SSID>), so
    we read the live SSID rather than showing the connection name."""
    try:
        r = subprocess.run(["iwgetid", "-r"], capture_output=True,
                           text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


@app.route("/api/wifi/status")
def api_wifi_status():
    """Report Wi-Fi mode (ap | client | none), SSID and whether the AP-fallback
    controls are wired up. Linux + NetworkManager only; degrades elsewhere."""
    if sys.platform != "linux":
        return jsonify({"ok": True, "supported": False})
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": True, "supported": False,
                        "reason": "controls-not-installed"})
    ok, out, _ = _netctl("status")
    mode, ssid = "none", None
    if ok:
        for line in out.splitlines():
            cols = _split_terse(line)
            if len(cols) < 2:
                continue
            name, ctype = cols[0], cols[1]
            if name == AP_CON_NAME:
                mode, ssid = "ap", AP_CON_NAME
                break
            if "wireless" in ctype:
                # Prefer the live SSID over the NM profile name (netplan-wlan-*).
                mode, ssid = "client", _current_ssid() or name
    return jsonify({"ok": True, "supported": True, "mode": mode,
                    "ssid": ssid, "ap_ip": "10.42.0.1"})


@app.route("/api/wifi/scan")
def api_wifi_scan():
    """List Wi-Fi networks in range (via NetworkManager). Linux only."""
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False}), 200
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": False, "supported": False,
                        "reason": "controls-not-installed"}), 200
    ok, out, err = _netctl("scan")
    if not ok:
        low = (err or "").lower()
        reason = "no-privilege" if ("password" in low or "not allowed" in low) else "scan-failed"
        return jsonify({"ok": False, "supported": True, "reason": reason,
                        "error": (err or "").strip()[:200]}), 200
    seen, nets = set(), []
    for line in out.splitlines():
        cols = _split_terse(line)
        if len(cols) < 4:
            continue
        in_use, ssid, signal, security = cols[0], cols[1], cols[2], cols[3]
        if not ssid or ssid in seen:            # skip hidden/blank + de-dup by SSID
            continue
        seen.add(ssid)
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        nets.append({
            "ssid":     ssid,
            "signal":   sig,
            "secure":   bool(security and security != "--"),
            "in_use":   in_use.strip() in ("*", "yes"),
        })
    nets.sort(key=lambda n: n["signal"], reverse=True)
    return jsonify({"ok": True, "supported": True, "networks": nets})


@app.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    """Join a Wi-Fi network by SSID + password (WPA2). Linux only.

    Body (JSON): {"ssid": "<name>", "password": "<8–63 chars>"}. The single
    onboard radio can't host the AP and be a client at once, so joining a network
    drops the OASIS AP — clients on the AP must reconnect via the new IP. CSRF is
    blocked by the same custom-header requirement as /api/service.
    """
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False,
                        "error": "NetworkManager not available"}), 200
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": False, "supported": False,
                        "error": "Wi-Fi controls not installed — run "
                                 "scripts/enable-ap-fallback.py"}), 200

    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    psk  = data.get("password") or ""
    if not ssid:
        return jsonify({"ok": False, "error": "SSID required"}), 400
    if not (8 <= len(psk) <= 63):
        return jsonify({"ok": False, "error": "WPA2 password must be 8–63 characters"}), 400

    # Trailing newline so the helper's `read` sees a complete line (without it,
    # `read` hits EOF, returns non-zero, and the password would be dropped).
    ok, out, err = _netctl("connect", ssid, stdin=psk + "\n", timeout=60)
    if not ok:
        msg = (err or out or "").strip() or "connection failed"
        low = msg.lower()
        if "password" in low and "authoriz" not in low:
            # Distinguish a missing sudo grant from a wrong Wi-Fi password.
            if "not allowed" in low or "a terminal is required" in low:
                msg += " — run: python3 scripts/enable-ap-fallback.py"
        return jsonify({"ok": False, "ssid": ssid, "error": msg[:300]}), 200
    return jsonify({"ok": True, "ssid": ssid})


@app.route("/api/wifi/forget", methods=["POST"])
def api_wifi_forget():
    """Forget the current Wi-Fi network — delete its saved profile so the radio
    disconnects and stops auto-rejoining. The AP-fallback watcher then raises the
    OASIS access point. Linux only; CSRF-guarded like /api/service."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False,
                        "error": "NetworkManager not available"}), 200
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": False, "supported": False,
                        "error": "Wi-Fi controls not installed — run "
                                 "scripts/enable-ap-fallback.py"}), 200

    ok, out, err = _netctl("forget", timeout=30)
    if not ok:
        msg = (err or out or "").strip() or "could not forget network"
        low = msg.lower()
        if "not allowed" in low or "a terminal is required" in low or "password is required" in low:
            msg += " — run: python3 scripts/enable-ap-fallback.py"
        return jsonify({"ok": False, "error": msg[:300]}), 200
    return jsonify({"ok": True})


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


@app.route("/api/health/rtc")
def api_health_rtc():
    """Hardware-RTC status from sysfs (no sudo): presence, driver name, whether
    it set the system clock at boot (hctosys), and drift vs the system clock.
    The Witty Pi 3's DS3231 appears here once features/rtc-hat/enable-rtc.py + a reboot load it."""
    import sys as _sys
    base = "/sys/class/rtc/rtc0"
    if _sys.platform != "linux" or not os.path.isdir(base):
        return jsonify({"ok": True, "present": False})

    def _read(name):
        try:
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    drift = None
    date_s, time_s = _read("date"), _read("time")   # sysfs RTC date/time are UTC
    if date_s and time_s:
        try:
            import calendar
            import datetime as _dt
            t = _dt.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
            drift = round(time.time() - calendar.timegm(t.timetuple()), 1)
        except (ValueError, OverflowError):
            pass

    return jsonify({"ok": True, "present": True, "name": _read("name"),
                    "hctosys": _read("hctosys") == "1", "drift_s": drift})


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


def _adsb_proxy(path, timeout=10):
    import urllib.request, urllib.error
    url = f"http://127.0.0.1:8086{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return Response(resp.read(), status=200, content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"ADS-B API unavailable ({reason}). "
                                 "Is the adsb-api service running?"}), 503
    except TimeoutError:
        return jsonify({"ok": False, "error": "ADS-B API timed out."}), 503


@app.route("/api/adsb/health")
def api_adsb_health_proxy():
    return _adsb_proxy("/health", timeout=3)


@app.route("/api/adsb/aircraft")
def api_adsb_aircraft_proxy():
    return _adsb_proxy("/aircraft")


@app.route("/api/adsb/history")
def api_adsb_history_proxy():
    import urllib.parse
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    return _adsb_proxy(f"/history?{qs}")


@app.route("/api/adsb/alerts")
def api_adsb_alerts_proxy():
    return _adsb_proxy("/alerts")


@app.route("/api/aprs/system")
def api_aprs_system_proxy():
    """Proxy system stats (CPU/RAM/temp) from the graywolf-api (port 8085).
    The value is server-cached (5 s sampler) so this is a cheap, fast read."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:8085/api/system"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
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


# ── Operator map warnings (shared, persisted) ─────────────────────────────────
# Flood/fire/etc. markers an operator drops on the APRS map. Stored server-side
# so every device viewing the map sees the same set. Small JSON list on disk,
# writes serialized through _warnings_lock. Owned by this server (not the APRS
# API), so warnings work even when the APRS chain is offline.
_WARN_TYPE_MAX = 64    # max length of a warning "type" id
_WARN_NOTE_MAX = 50    # note character cap (mirrors the UI maxlength)
_WARN_MAX      = 500   # hard cap on total stored warnings


def _load_warnings():
    """Return the warnings list from disk ([] if absent/unreadable)."""
    try:
        with open(WARNINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_warnings(warnings):
    """Write the list atomically (temp file + os.replace)."""
    tmp = WARNINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(warnings, fh)
    os.replace(tmp, WARNINGS_FILE)


def _clean_note(value):
    """Single-line, trimmed, length-capped note string."""
    return str(value or "").replace("\n", " ").replace("\r", " ").strip()[:_WARN_NOTE_MAX]


@app.route("/api/aprs/warnings", methods=["GET"])
def api_aprs_warnings_list():
    return jsonify({"ok": True, "warnings": _load_warnings()})


@app.route("/api/aprs/warnings", methods=["POST"])
def api_aprs_warnings_add():
    body = request.get_json(silent=True) or {}
    try:
        lon = float(body.get("lon"))
        lat = float(body.get("lat"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lon/lat required (numeric)"}), 400
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return jsonify({"ok": False, "error": "lon/lat out of range"}), 400
    wtype = str(body.get("type") or "").strip()[:_WARN_TYPE_MAX]
    if not wtype:
        return jsonify({"ok": False, "error": "type required"}), 400
    note = _clean_note(body.get("note"))
    with _warnings_lock:
        warnings = _load_warnings()
        if len(warnings) >= _WARN_MAX:
            return jsonify({"ok": False, "error": "warning limit reached"}), 409
        item = {
            "id":   uuid.uuid4().hex,
            "type": wtype,
            "lon":  lon,
            "lat":  lat,
            "note": note,
            "ts":   int(time.time()),
        }
        warnings.append(item)
        _save_warnings(warnings)
    return jsonify({"ok": True, "warning": item})


@app.route("/api/aprs/warnings/<wid>", methods=["PATCH"])
def api_aprs_warnings_update(wid):
    body = request.get_json(silent=True) or {}
    note = _clean_note(body.get("note"))
    with _warnings_lock:
        warnings = _load_warnings()
        found = None
        for w in warnings:
            if w.get("id") == wid:
                w["note"] = note
                found = w
                break
        if found is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        _save_warnings(warnings)
    return jsonify({"ok": True, "warning": found})


@app.route("/api/aprs/warnings/<wid>", methods=["DELETE"])
def api_aprs_warnings_delete(wid):
    with _warnings_lock:
        warnings = _load_warnings()
        kept = [w for w in warnings if w.get("id") != wid]
        if len(kept) == len(warnings):
            return jsonify({"ok": False, "error": "not found"}), 404
        _save_warnings(kept)
    return jsonify({"ok": True})


# ── Winlink (Pat) proxy ───────────────────────────────────────────────────────
# OASIS ships an OASIS-styled Winlink mail client (/server/winlink/mail.html) that talks
# to Pat's JSON API. Pat runs on port 8082 and does NOT emit CORS headers, so the
# browser stays same-origin by going through these thin pass-through proxies
# (same pattern as the /api/aprs/* routes above). The live connect-session log is
# the one exception: the page opens a WebSocket straight to Pat (ws://host:8082/ws),
# which is not subject to CORS and keeps streaming off this sync backend.
#
# These are byte/JSON pass-throughs, so they are resilient to Pat payload-shape
# changes; the front-end binds the actual field names.
WINLINK_PORT = 8082
WINLINK_BOXES = {"in", "out", "sent", "archive"}


def _winlink_proxy(path, *, method="GET", query="", data=None, headers=None,
                   timeout=10):
    """Forward a request to Pat on WINLINK_PORT and pass its response through.

    Mirrors api_aprs_*_proxy: same-origin pass-through, verbatim HTTPError body,
    503 on URLError/timeout with an OASIS-style message.
    """
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{WINLINK_PORT}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200) or 200
        return Response(body, status=status, content_type="application/json")
    except urllib.error.HTTPError as e:
        # Pat is up but returned an error — pass its body through verbatim.
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"Pat (Winlink) unreachable ({reason}). "
                                 "Is the 'pat' service running on "
                                 f"port {WINLINK_PORT}?"}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "Pat (Winlink) timed out."}), 503


@app.route("/api/winlink/mailbox/<box>", methods=["GET"])
def api_winlink_mailbox_list(box):
    """List messages in a Pat mailbox (in / out / sent / archive)."""
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox"}), 400
    return _winlink_proxy(f"/api/mailbox/{box}")


@app.route("/api/winlink/mailbox/<box>/<mid>", methods=["GET", "DELETE"])
def api_winlink_message(box, mid):
    """Read or delete a single Pat message."""
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox"}), 400
    return _winlink_proxy(f"/api/mailbox/{box}/{mid}", method=request.method)


@app.route("/api/winlink/mailbox/<box>/<mid>/<path:attachment>", methods=["GET"])
def api_winlink_attachment(box, mid, attachment):
    """Stream a message attachment from Pat, passing Pat's Content-Type through.

    Unlike the JSON proxies above, attachments are arbitrary bytes (form XML,
    FormData.txt, photos, PDFs), so we forward Pat's Content-Type rather than
    forcing application/json. ?download=1 adds a Content-Disposition so the
    browser saves rather than renders inline.
    """
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox"}), 400
    import urllib.request
    import urllib.error
    import urllib.parse

    url = (f"http://127.0.0.1:{WINLINK_PORT}/api/mailbox/"
           f"{box}/{mid}/{urllib.parse.quote(attachment)}")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
        out = Response(body, status=200, content_type=ctype)
        if request.args.get("download"):
            safe = attachment.replace('"', "").replace("\\", "")
            out.headers["Content-Disposition"] = f'attachment; filename="{safe}"'
        return out
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"Pat (Winlink) unreachable ({reason})."}), 503
    except TimeoutError:
        return jsonify({"ok": False, "error": "Pat (Winlink) timed out."}), 503


@app.route("/api/winlink/mailbox/out", methods=["POST"])
def api_winlink_compose():
    """Queue a composed message into Pat's outbox.

    Forwards the form body (to / cc / subject / body) verbatim with its
    Content-Type so Pat parses it exactly as its own UI would.
    """
    headers = {}
    ctype = request.headers.get("Content-Type")
    if ctype:
        headers["Content-Type"] = ctype
    return _winlink_proxy("/api/mailbox/out", method="POST",
                          data=request.get_data(), headers=headers)


@app.route("/api/winlink/status", methods=["GET"])
def api_winlink_status():
    """Proxy Pat's connection status."""
    return _winlink_proxy("/api/status")


@app.route("/api/winlink/aliases", methods=["GET"])
def api_winlink_aliases():
    """Proxy Pat's configured connect aliases (available transports)."""
    return _winlink_proxy("/api/connect_aliases")


@app.route("/api/winlink/disconnect", methods=["GET"])
def api_winlink_disconnect():
    """Abort the in-progress Pat connect session (the Abort button).

    Returns 400 from Pat when there's no active session — harmless.
    """
    return _winlink_proxy("/api/disconnect")


# Transport modes Pat's rmslist accepts (verified against a live Pat). The
# no-mode call hangs, so a mode is required and allowlisted.
RMS_MODES = {"packet", "ardop", "vara", "varahf", "varafm", "pactor"}


@app.route("/api/winlink/rmslist", methods=["GET"])
def api_winlink_rmslist():
    """Slim proxy of Pat's RMS gateway list for one transport mode.

    Pat's /api/rmslist?mode=X returns the gateways PLUS a per-gateway VOACAP
    propagation report whose raw text is ~95% of the (multi-MB) payload. We
    strip that — keeping the link_quality score — so the browser caches a lean
    snapshot (essential on the Pi Zero). The data is sourced from Pat's web API,
    so it needs connectivity at download time; the client caches it for offline
    use. Generous timeout because Pat computes the predictions live.
    """
    import json as _json
    import urllib.request
    import urllib.error

    mode = (request.args.get("mode") or "").strip().lower()
    if mode not in RMS_MODES:
        return jsonify({"ok": False, "error": "unknown mode"}), 400

    url = f"http://127.0.0.1:{WINLINK_PORT}/api/rmslist?mode={mode}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"Pat (Winlink) unreachable ({reason}). The "
                                 "gateway list needs Pat running + connectivity."}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "Pat timed out building the gateway list."}), 503

    try:
        gateways = _json.loads(raw)
    except ValueError:
        return jsonify({"ok": False, "error": "Pat returned malformed data."}), 502

    slim = []
    for g in (gateways or []):
        if not isinstance(g, dict):
            continue
        freq = g.get("freq") or {}
        dial = g.get("dial") or {}
        pred = g.get("prediction") or {}
        slim.append({
            "callsign":     g.get("callsign"),
            "gridsquare":   g.get("gridsquare"),
            "distance":     g.get("distance"),
            "azimuth":      g.get("azimuth"),
            "modes":        g.get("modes"),
            "freq_khz":     freq.get("khz"),
            "dial_khz":     dial.get("khz"),
            "link_quality": pred.get("link_quality"),
            "url":          g.get("url"),
        })
    return jsonify({"ok": True, "mode": mode, "count": len(slim), "gateways": slim})


@app.route("/api/winlink/connect", methods=["GET"])
def api_winlink_connect():
    """Start a Pat connect session. Forwards ?url=<alias-or-transport-url>.

    Longer timeout: a connect (esp. RF) can take a while. The live log streams
    over the browser's direct WebSocket to Pat, not through here.
    """
    import urllib.parse
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    return _winlink_proxy("/api/connect", query=qs, timeout=120)


@app.route("/api/winlink/log", methods=["GET"])
def api_winlink_log():
    """Tail the Pat journald log for the mail UI's session console.

    Pat's live-log WebSocket is same-origin only (gorilla's default origin check
    rejects this cross-origin page on :8083 → Pat :8082), so it never reaches the
    browser. journald is the reliable same-origin source (the same lines you see
    in `journalctl -u pat`). We tail only the `pat` unit — Direwolf's audio-level
    meters and raw AX.25 frame dumps are excluded so the console shows just Pat's
    session/B2F lines. Read-only; the only input is a clamped line count. Falls
    back gracefully (ok:false) so the client keeps whatever it already showed."""
    import subprocess
    import sys as _sys

    if _sys.platform != "linux":
        return jsonify({"ok": False, "supported": False, "lines": []})
    try:
        n = int(request.args.get("lines", 40))
    except (TypeError, ValueError):
        n = 40
    n = max(1, min(n, 300))

    # -o cat = message text only; Pat already prefixes its own timestamps.
    cmd = ["journalctl", "-u", "pat",
           "-n", str(n), "-o", "cat", "--no-pager"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "lines": []})
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "permission" in err.lower() or not err:
            err += " (journal not readable — add the OASIS user to the 'systemd-journal' group)"
        return jsonify({"ok": False, "error": err, "lines": []})
    return jsonify({"ok": True, "lines": r.stdout.splitlines()})


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

    def oasis_ver():
        try:
            import json as _json
            with open(VERSION_FILE, encoding="utf-8") as fh:
                return str(_json.load(fh).get("version") or "unknown")
        except (OSError, ValueError):
            return "unknown"

    if "gunicorn" in sys.modules:
        wsgi = "gunicorn"
        wsgi_version = get_ver("gunicorn")
    else:
        wsgi = "werkzeug"
        wsgi_version = get_ver("werkzeug")

    return jsonify({
        "ok":            True,
        "version":       oasis_ver(),
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


@app.route("/api/installed-services")
def api_installed_services():
    """Report which features setup-oasis.py recorded as installed, so the
    dashboard can hide cards for services the operator chose not to install.

    Returns {"ok": True, "features": [...]} when the manifest exists, or
    {"ok": True, "features": None} when it's absent/unreadable — the dashboard
    treats a null list as “no manifest” and shows every card.

    Portable mode (OASIS_FEATURES set) overrides the on-disk manifest: it
    returns exactly that list with locked=True, so the dashboard shows only
    these cards and hides the reveal button. Nothing is written to disk."""
    if PORTABLE_FEATURES is not None:
        return jsonify({"ok": True, "locked": True,
                        "features": PORTABLE_FEATURES})
    try:
        with open(INSTALLED_SERVICES_FILE) as fh:
            data = json.load(fh)
        feats = data.get("features")
        if isinstance(feats, list):
            return jsonify({"ok": True,
                            "features": sorted({str(k) for k in feats})})
    except FileNotFoundError:
        pass
    except (ValueError, OSError):
        pass
    return jsonify({"ok": True, "features": None})


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


def _gps_info():
    """Snapshot from gpsd (127.0.0.1:2947): fix mode, sats, position. Returns None
    if gpsd isn't reachable (so the card hides), or a dict (mode 0 = gpsd up, no
    fix yet). Dependency-free — speaks gpsd's JSON protocol over a socket."""
    import json as _json
    try:
        s = socket.create_connection(("127.0.0.1", 2947), timeout=1.5)
    except OSError:
        return None
    info = {}
    try:
        s.sendall(b'?WATCH={"enable":true,"json":true};\n')
        s.settimeout(1.5)
        buf = b""
        deadline = time.time() + 2.0
        have_tpv = have_sky = False
        while time.time() < deadline and not (have_tpv and have_sky):
            try:
                chunk = s.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = _json.loads(line)
                except ValueError:
                    continue
                if msg.get("class") == "TPV":
                    info["mode"] = msg.get("mode", 0)
                    if msg.get("lat") is not None:
                        info["lat"] = msg["lat"]
                    if msg.get("lon") is not None:
                        info["lon"] = msg["lon"]
                    alt = msg.get("altMSL", msg.get("alt"))
                    if alt is not None:
                        info["alt_m"] = alt
                    have_tpv = True
                elif msg.get("class") == "SKY":
                    sats = msg.get("satellites", [])
                    info["seen"] = msg.get("nSat", len(sats))
                    info["used"] = msg.get("uSat", sum(1 for x in sats if x.get("used")))
                    if msg.get("hdop") is not None:
                        info["hdop"] = msg["hdop"]
                    have_sky = True
    except OSError:
        pass
    finally:
        try:
            s.close()
        except OSError:
            pass
    return info or {"mode": 0}


def _chrony_state():
    """Clock state from chrony — INDEPENDENT of GPS (chrony runs regardless and
    may sync from NTP or the RTC).

    `running` comes from systemd (authoritative, needs no privilege). Sync detail
    comes from `chronyc -c tracking` *when queryable* — the server user often
    can't reach chronyd's command socket, so we also try forcing the localhost
    UDP path, and degrade to {running, queryable:False} if neither works.
    CSV: field 1 = reference name ('GPS' for the refclock, else an NTP host),
    field 4 = system-time offset (s), last field = leap status."""
    active = ""
    for unit in ("chrony", "chronyd"):
        try:
            active = subprocess.run(["systemctl", "is-active", unit],
                                   capture_output=True, text=True, timeout=5).stdout.strip()
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            active = ""
        if active == "active":
            break
    if active != "active":
        return {"running": False}

    out = None
    for cmd in (["chronyc", "-c", "tracking"],
                ["chronyc", "-h", "127.0.0.1", "-c", "tracking"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            out = r.stdout.strip()
            break
    if out is None:
        return {"running": True, "queryable": False}   # daemon up, can't read detail

    f = out.split(",")
    if len(f) < 5:
        return {"running": True, "queryable": False}
    leap = f[-1].strip()
    res = {
        "running":   True,
        "queryable": True,
        "synced":    leap in ("Normal", "Insert second", "Delete second"),
        "source":    f[1],
        "gps":       "GPS" in f[1].upper(),
    }
    try:
        res["offset_s"] = float(f[4])
    except ValueError:
        pass
    return res


# ── Background sampler ─────────────────────────────────────────────────────────
# CPU% over a rolling 2s window, plus slower-changing Pi facts (throttle state,
# Wi-Fi), are measured on a daemon thread and cached. /api/system then never
# spawns a subprocess or blocks in the request path — stable, Pi-friendly, and
# values agree across repeated polls. (Under gunicorn each worker samples its
# own; they track closely since both average the same window.)
_CPU_PCT  = None  # most recent rolling CPU%; None until the first sample lands
_THROTTLE = None  # cached _pi_throttled(); None on non-Pi
_NET      = None  # cached _wifi_info();    None when unavailable
_GPS      = None  # cached _gps_info();    None when gpsd unreachable
_CHRONY   = None  # cached _chrony_state(); clock state, independent of GPS

def _sampler():
    try:
        import psutil
    except ImportError:
        psutil = None
    global _CPU_PCT, _THROTTLE, _NET, _GPS, _CHRONY
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
            _GPS      = _gps_info()
            _CHRONY   = _chrony_state()
        i += 1

threading.Thread(target=_sampler, name="oasis-sampler", daemon=True).start()


def _lan_ip():
    """Best-effort primary LAN IP. First tries the UDP-connect trick to pick the
    outbound interface (no packets sent, no internet needed). When there is no
    default route — e.g. the Pi is hosting the OASIS AP with no upstream — that
    returns loopback, so fall back to the first non-loopback IPv4 address,
    preferring the Wi-Fi interface (the AP's 10.42.0.1)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        s.close()
    try:
        import psutil
        addrs = psutil.net_if_addrs()
        # Prefer wlan* (AP/client radio), then other real interfaces; skip lo.
        for name in sorted(addrs, key=lambda n: (0 if n.startswith("wlan") else 1, n)):
            if name.startswith("lo"):
                continue
            for a in addrs[name]:
                if (a.family == socket.AF_INET and a.address
                        and not a.address.startswith("127.")):
                    return a.address
    except Exception:
        pass
    return "127.0.0.1"


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
        "gps":         _GPS,
        "chrony":      _CHRONY,
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
        if _port_bindable(p):
            return p
    raise RuntimeError(f"No free port found between {start} and {end}")


def _port_bindable(port, host=""):
    """True if `port` can be bound the way gunicorn binds it (SO_REUSEADDR).

    Mirroring gunicorn's bind semantics matters on restart: the previous
    instance's socket is often only in TIME_WAIT, which a plain bind() reports
    as "in use" even though gunicorn (SO_REUSEADDR) would bind it fine. Probing
    with SO_REUSEADDR keeps this check honest and avoids drifting to 8084.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def resolve_port(preferred=8083, wait=15.0, poll=0.5):
    """Pick the port to serve on, deterministically across restarts.

    Honors OASIS_PORT as an explicit override. Otherwise prefers `preferred`
    (8083, the canonical OASIS port the dashboard expects). On a service
    restart where the old instance is still releasing the port, waits up to
    `wait` seconds for it to free rather than drifting to the next port —
    drift strands the dashboard (every service shows red). Falls back to the
    next free port only if the preferred one never frees, so startup never
    hard-fails.
    """
    env = os.environ.get("OASIS_PORT")
    if env:
        try:
            preferred = int(env)
        except ValueError:
            pass

    deadline = time.monotonic() + wait
    while True:
        if _port_bindable(preferred):
            return preferred
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    return find_free_port(preferred)

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

    PORT = resolve_port()
    os.environ["OASIS_PORT"] = str(PORT)   # so the gunicorn-loaded app reports this port
    url = f"http://localhost:{PORT}/"

    # Prefer the gunicorn production server when it's installed — re-exec into it.
    # gunicorn imports this module as 'app', so __main__ never runs under gunicorn
    # (no recursion). Falls back to the Flask dev server if gunicorn is absent or
    # --dev is given. This is why a plain `python app.py` now serves via gunicorn.
    # gunicorn is POSIX-only (needs fcntl / os.fork), so Windows always uses the
    # Flask dev server regardless of what's installed.
    if not args.dev and sys.platform != "win32":
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
