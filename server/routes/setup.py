"""
Setup Orchestrator route blueprint — the web setup flow: preflight blockers,
plan/run/job/log/cancel lifecycle (in-process job state, single gunicorn
worker — see app.py __main__), the privileged-install handoff to the
out-of-process root worker, and the reboot request. Extracted verbatim from
server/app.py in the blueprint split; URLs unchanged.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from flask import Blueprint, jsonify, request

import appconfig
from common import config_paths
from common import gpsd_chrony
from common import installed_services
from common import hardware_detect as HD_detect
from common import setup_engine as SE
from common import setup_registry as SETUP_REGISTRY
from common.oasis_lib import has_internet
from routes.health import _health_paths
from routes.wifi import NETCTL_PATH, _netctl

SUITE_ROOT = appconfig.SUITE_ROOT
INSTALLED_SERVICES_FILE = appconfig.INSTALLED_SERVICES_FILE

bp = Blueprint("setup", __name__)

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

    if "winlink" in sel and not (win.get("password") or "").strip() and not _setup_pat_password_set():
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
            "pi-headless", "pi-local-monitor", "pi-small-screen-7", "cm4stack",
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
        "graywolf", "winlink", "kiwix", "openwebrx", "adsb", "satellites",
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
    dst = config_paths.station_json(SUITE_ROOT)
    os.makedirs(config_paths.config_dir(SUITE_ROOT), exist_ok=True)
    existing = {}
    try:
        with open(dst, "r", encoding="utf-8") as fh:
            existing = json.load(fh) or {}
    except Exception:
        existing = {}

    # Preserve existing values when a field is omitted in setup payload.
    if not callsign:
        callsign = (existing.get("callsign") or "").strip().upper()
    if not grid:
        grid = (existing.get("grid") or "").strip().upper()
    if lat is None:
        lat = existing.get("lat")
    if lon is None:
        lon = existing.get("lon")

    if not (callsign or grid or lat is not None or lon is not None):
        return

    body = {
        "callsign": callsign or "N0CALL",
        "grid": grid,
        "lat": lat,
        "lon": lon,
        "updated": _setup_iso_now(),
    }
    # Preserve keys this write doesn't own (the APRS frequency is set from its own
    # Setup control / endpoint, not the station form) so a station save can't drop it.
    if existing.get("aprs_freq"):
        body["aprs_freq"] = existing["aprs_freq"]
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2)
        fh.write("\n")


def _setup_pat_password_set():
    path = _health_paths().get("pat_config")
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
        return bool((cfg.get("secure_login_password") or "").strip())
    except Exception:
        return False


def _setup_registry(payload=None):
    return SETUP_REGISTRY.build_registry(SUITE_ROOT, payload)


_SETUP_SUCCESS_STATUSES = {SE.STATUS_INSTALLED, SE.STATUS_INSTALLED_ENABLED_NOT_STARTED,
                           SE.STATUS_INSTALLED_NEEDS_REBOOT}


def _setup_record_installed_features(summary):
    """Persist this web run's successful features into installed-services.json,
    mirroring setup-oasis.py's record_installed().

    The manifest is purely ADDITIVE: successful installs are unioned with
    whatever is already recorded, and a feature is NEVER removed here as a side
    effect of being left unticked. (Installed features render green+unticked on
    the Setup page; dropping them on the next run would silently uninstall them
    from the dashboard's point of view.) Per-feature removal has its own explicit
    path — see FeatureSpec.remove_fn — and does not go through this function.

    Writes through common/installed_services.py, the single owner of the manifest
    schema. Removal records for the just-installed features are attached in Task 7
    (see removal_backfill); this function stays additive."""
    ok_keys = {item.get("feature") for item in summary.features
               if item.get("status") in _SETUP_SUCCESS_STATUSES}
    ok_keys.discard(None)
    installed_services.add_installed(SUITE_ROOT, ok_keys, {})


# ── Privileged installs: hand off to the out-of-process root worker ─────────────
# The web server never runs as root and has no TTY/cached sudo credential, so a
# privileged FeatureSpec's install_fn is NOT called in-process here. Instead we
# drop a small job file (feature key + the same setup payload, so the worker's
# build_registry() call produces the byte-for-byte identical install_fn) into
# the installer queue, and wait for the out-of-process root worker
# (scripts/oasis_installer_worker.py, triggered by the oasis-installer.path
# systemd unit — see scripts/enable-oasis-installer.py) to pick it up and write
# a result file back. No password ever touches this process.
INSTALLER_QUEUE_DIR = config_paths.installer_queue_dir(SUITE_ROOT)
_INSTALLER_POLL_INTERVAL_S = 0.5
_INSTALLER_JOB_TIMEOUT_S = 300
_INSTALLER_HEARTBEAT_INTERVAL_S = 10
# Written by scripts/enable-oasis-installer.py; its presence is the cheapest
# (no subprocess, no root needed) signal that the root worker daemon exists.
INSTALLER_PATH_UNIT_FILE = "/etc/systemd/system/oasis-installer.path"

_INSTALLER_DAEMON_UNAVAILABLE_TEXT = (
    "No privileged installer worker is enabled. Run "
    "'python3 scripts/enable-oasis-installer.py' once from a terminal to enable it."
)


def _installer_daemon_enabled():
    return sys.platform == "linux" and os.path.exists(INSTALLER_PATH_UNIT_FILE)


def _setup_enqueue_and_wait_install(key, spec, payload, job_id=None):
    if key not in SETUP_REGISTRY.PRIVILEGED_FEATURES:
        # Defensive: only PRIVILEGED_FEATURES should ever reach here (run_plan
        # only calls privileged_run_fn when spec.privileged is True), but never
        # hand an unvalidated/unknown key to the root worker.
        return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": f"{key} is not a privileged feature"}

    # Fail fast instead of silently queueing a job nothing will ever pick up
    # and only finding out ~5 minutes later (the old behavior was ~15 min) —
    # this is the single most common reason a privileged install looks
    # "stuck" with no further log output.
    if not _installer_daemon_enabled():
        return {
            "ok": False,
            "reason_code": "INSTALLER_DAEMON_UNAVAILABLE",
            "reason_text": _INSTALLER_DAEMON_UNAVAILABLE_TEXT,
        }

    os.makedirs(INSTALLER_QUEUE_DIR, exist_ok=True)
    job_id_suffix = f"{time.time():.6f}-{key}-{uuid.uuid4().hex[:8]}"
    job_path = os.path.join(INSTALLER_QUEUE_DIR, f"{job_id_suffix}.job.json")
    result_path = os.path.join(INSTALLER_QUEUE_DIR, f"{job_id_suffix}.result.json")
    tmp_path = job_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump({"feature": key, "payload": payload or {}}, fh)
        os.rename(tmp_path, job_path)  # atomic on the same filesystem
    except Exception as exc:
        return {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": f"could not queue install job: {exc}"}

    # The worker writes its live output to <job>.log; tail it into the job's log
    # stream so the setup page's log window shows the privileged install as it runs.
    log_path = os.path.join(INSTALLER_QUEUE_DIR, f"{job_id_suffix}.log")
    log_off = 0

    def _drain_log(off):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                lf.seek(off)
                chunk = lf.read()
                new_off = lf.tell()
        except OSError:
            return off
        if chunk and job_id:
            for ln in chunk.splitlines():
                if ln.strip():
                    _setup_emit_log_line(job_id, ln)
        return new_off

    started = _setup_now()
    deadline = started + _INSTALLER_JOB_TIMEOUT_S
    next_heartbeat = started + _INSTALLER_HEARTBEAT_INTERVAL_S
    while _setup_now() < deadline:
        log_off = _drain_log(log_off)
        if os.path.exists(result_path):
            log_off = _drain_log(log_off)   # final flush before cleanup
            try:
                with open(result_path, "r", encoding="utf-8") as fh:
                    result = json.load(fh)
            except Exception as exc:
                result = {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": f"unreadable result: {exc}"}
            for _p in (result_path, log_path):
                try:
                    os.remove(_p)
                except OSError:
                    pass
            return result
        now = _setup_now()
        if job_id and now >= next_heartbeat:
            _setup_emit_event(job_id, {
                "schemaVersion": "1.0",
                "event": "installer_waiting",
                "jobId": job_id,
                "ts": _setup_iso_now(),
                "feature": key,
                "waitedS": int(now - started),
                "timeoutS": _INSTALLER_JOB_TIMEOUT_S,
            })
            next_heartbeat = now + _INSTALLER_HEARTBEAT_INTERVAL_S
        time.sleep(_INSTALLER_POLL_INTERVAL_S)

    # Timed out — most likely the privileged worker/unit was never enabled.
    try:
        os.remove(job_path)
    except OSError:
        pass
    return {
        "ok": False,
        "reason_code": "INSTALLER_DAEMON_UNAVAILABLE",
        "reason_text": _INSTALLER_DAEMON_UNAVAILABLE_TEXT,
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


def _setup_emit_log_line(job_id, line):
    """Live-stream one line of a running installer script's console output
    into the job's log (see common/setup_registry.py's set_log_sink). Tagged
    with whatever feature is currently installing so the console shows it
    right under that feature's "install started" line."""
    with _setup_lock:
        job = _setup_jobs.get(job_id)
        feature = job.get("currentFeature") if job else None
    _setup_emit_event(job_id, {
        "schemaVersion": "1.0",
        "event": "stage_log",
        "jobId": job_id,
        "ts": _setup_iso_now(),
        "feature": feature,
        "line": line,
    })


def _setup_run_job(job_id, plan_obj, payload):
    global _setup_active_job
    reg = _setup_registry(payload)
    run_opts = SE.RunOptions(
        sequential=True,
        auto_start_services=False,
        job_id=job_id,
        cancel_requested=lambda: _setup_cancel_requested(job_id),
        privileged_run_fn=lambda key, spec: _setup_enqueue_and_wait_install(key, spec, payload, job_id),
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
    SETUP_REGISTRY.set_log_sink(lambda line: _setup_emit_log_line(job_id, line))
    try:
        _states, _blocked, summary = SE.run_plan(
            plan_obj,
            run_opts,
            reg,
            event_sink=lambda e: _setup_emit_event(job_id, e),
        )
    finally:
        SETUP_REGISTRY.set_log_sink(None)

    if not _blocked:
        try:
            _setup_record_installed_features(summary)
        except Exception:
            pass

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


@bp.route("/api/setup/permissions")
def api_setup_permissions():
    granted = bool(sys.platform == "linux" and os.path.exists("/etc/sudoers.d/oasis-service-controls"))
    installer_active = _installer_daemon_enabled()
    return jsonify({
        "ok": True,
        "serviceControlsGranted": granted,
        "localCommand": "python3 scripts/enable-service-controls.py",
        "message": "Run command in WebSSH and click Re-check" if not granted else "permissions granted",
        "installerDaemonActive": installer_active,
        "installerLocalCommand": "python3 scripts/enable-oasis-installer.py",
    })


@bp.route("/api/setup/hardware-detect")
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

    # Classify raw USB/ALSA/serial facts into recognized ham-radio peripherals
    # so the Setup page can say "1 RTL-SDR + 1 DigiRig, 4 other USB devices"
    # instead of an opaque lsusb count. See common/hardware_detect.py for why
    # DRA-Pi is checked via the ALSA card name, not lsusb/serial presence.
    usb_classified = HD_detect.classify_usb_devices(scan.get("usb") or [])
    has_dra_driver = HD_detect.dra_pi_present(scan.get("alsa") or [])
    # DigiRig: prefer the /dev/serial/by-id signal (needs no extra binary,
    # works even on images without usbutils installed) over the lsusb-based
    # match, which depends on the `lsusb` command actually succeeding.
    digirig = (HD_detect.digirig_candidates(scan.get("serial") or [])
               or usb_classified["digirig"])
    try:
        gps_configured_device = gpsd_chrony.configured_device()
    except Exception:
        gps_configured_device = None

    return jsonify({
        "ok": True,
        "lsusb": lsusb,
        "detected": scan,
        "draPiDriverReady": has_dra_driver,
        "usbClassified": {
            "rtlSdr": usb_classified["rtl_sdr"],
            "digirig": digirig,
            "cm108": usb_classified["cm108"],
            "other": usb_classified["other"],
        },
        "gpsConfiguredDevice": gps_configured_device,
    })


@bp.route("/api/setup/plan", methods=["POST"])
def api_setup_plan():
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403

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
            "installerDaemonActive": _installer_daemon_enabled(),
            "installerLocalCommand": "python3 scripts/enable-oasis-installer.py",
        },
        "orderedFeatures": ordered_for_response,
        "resolvedFeatures": ordered_for_response,
        "preflight": {"blocked": plan.blocked, "warnings": []},
        "reboot": {"requiredIfRunNow": bool(reboot_reasons), "reasons": reboot_reasons},
    })


@bp.route("/api/setup/run", methods=["POST"])
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


@bp.route("/api/setup/jobs/<job_id>")
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


@bp.route("/api/setup/jobs/<job_id>/log")
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


@bp.route("/api/setup/cancel", methods=["POST"])
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


@bp.route("/api/setup/reboot", methods=["POST"])
def api_setup_reboot():
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False, "error": "reboot unavailable"}), 200
    # Debian/Raspberry Pi OS convention; BENCH-VERIFY on real hardware — not
    # confirmed in this dev environment. MUST match the OASIS_REBOOT Cmnd_Alias
    # in scripts/enable-service-controls.py token-for-token, or `sudo -n` denies it.
    reboot_bin = shutil.which("reboot") or "/sbin/reboot"
    try:
        subprocess.run(["sudo", "-n", reboot_bin], capture_output=True, text=True, timeout=5)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "message": "reboot requested"})


