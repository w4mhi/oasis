"""
Hardware route blueprint — device declaration/assignment for the dashboard
HARDWARE card (RTL-SDRs, DigiRig, DRA-Pi), auto-declare/reconcile on poll,
and the burn-serial flow. Extracted verbatim from server/app.py in the
blueprint split; URLs unchanged.
"""

import json
import os
import re
import subprocess
import threading
import time

from flask import Blueprint, jsonify, request

import appconfig
from common import config_paths
from common import guardian as GUARD
from common import hardware as HW
from common import hardware_detect as HD_detect
from routes.service_control import _CONTROLLABLE_SERVICES, _systemctl_seq

SUITE_ROOT = appconfig.SUITE_ROOT

bp = Blueprint("hardware", __name__)

# Emergency STOP ALL / guardian target: every controllable service EXCEPT WebSSH.
# Killing WebSSH would sever the operator's remote connection to the box at the
# worst possible moment (an unattended overheat) — you must never lose the way
# in. The core `oasis` server and `gpsd` are already outside
# _CONTROLLABLE_SERVICES; WebSSH is the one more thing we keep alive.
_EMERGENCY_STOP = _CONTROLLABLE_SERVICES - {"webssh"}

# Guardian threshold floors — an authenticated operator can tune thresholds, but
# not below sane minimums (a value under normal idle would arm perpetually).
_THRESHOLD_MIN = {"temp_c": 45.0, "cpu_pct": 50.0, "mem_pct": 60.0}


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


@bp.route("/api/hardware/devices")
def api_hardware_devices():
    """Live device-allocation list for the dashboard HARDWARE card, plus each
    logical service's own assignment/can_start state (Slice 4b, spec §7.1-7.2)
    — drives the per-card device dropdown's selected value and the
    unassigned-state messaging without duplicating can_start's logic in JS.

    Also reconciles default assignment
    (specs/2026-07-15-hardware-conflict-resolution-v2-design.md §3): the three
    RTL-SDR consumers (adsb, openwebrx, aprs) each get the first present rtl-sdr
    automatically when unassigned — shared, since assignment is advisory — so
    the single-dongle case needs no manual step.

    Before that, every detected-but-undeclared RTL-SDR with a UNIQUE serial is
    auto-declared (not just a lone one) so it appears in the assignment
    dropdowns, which list DECLARED devices. The expensive exclusive rtl_test
    scan is gated behind a cheap lsusb presence count: it runs only when more
    dongles are present than declared (first run / newly plugged), so once
    every dongle is declared the per-poll cost is just lsusb — a real concern
    on a low-power Pi, where running rtl_test constantly would also contend with
    whatever's actively using a dongle. Same-serial duplicates (factory
    00000001) are left for the burn-serial flow to disambiguate."""
    inv = HW.load(SUITE_ROOT)
    # Auto-declare every present dongle, not just a lone one. Gate the expensive
    # exclusive rtl_test scan behind a cheap lsusb count: only scan when more
    # RTL-SDRs are physically present than are declared (first run, or a newly
    # plugged dongle). Once every present dongle is declared, present == declared
    # and the per-poll cost is just the lsusb probe — no rtl_test contention.
    declared_rtl = sum(1 for d in inv.devices.values() if d.get("kind") == "rtl-sdr")
    present_rtl = HD_detect.rtl_sdr_usb_count()
    if present_rtl > declared_rtl:
        detected_serials = [d["serial"] for d in HD_detect.scan().get("rtl_sdr", [])]
        HW.auto_declare_rtl_sdrs(SUITE_ROOT, inv, detected_serials)
    elif present_rtl < declared_rtl:
        # A dongle was unplugged (fewer present than declared) — undeclare it so
        # services that had it show unassigned. Symmetric with auto-declare;
        # busy-safe + count-bounded inside reconcile_present_rtl_sdrs.
        detected_serials = [d["serial"] for d in HD_detect.scan().get("rtl_sdr", [])]
        HW.reconcile_present_rtl_sdrs(SUITE_ROOT, inv, detected_serials, present_rtl)
    # No auto-assign. SDR services start UNASSIGNED; the operator assigns each
    # dongle explicitly via the card dropdown (which applies on change). Auto-
    # assigning "the first present dongle" was a wrong guess on multi-dongle rigs
    # — it can't know which antenna is on which — and it drifted from the applied
    # config (it wrote the inventory on the poll but never re-applied). Auto-
    # DECLARE + reconcile-on-unplug above stay; only the assignment is manual.

    # Winlink radio ports auto-declare/undeclare like the SDRs — no manual form:
    #   DigiRig — a lone CP210x PTT by-id (detect_digirig);
    #   DRA-Pi  — its audioinjectorpi ALSA card (detect_dra_pi), fully fixed.
    # Assignment is MANUAL too (operator picks DigiRig/DRA-Pi in the dropdown).
    # PTT + audio flow into direwolf via winlink.apply, so when a declaration
    # change (an unplug that releases winlink's device) alters state, re-template
    # direwolf right away (threaded — never block the poll).
    winlink_before = inv.assignments.get("winlink")
    HW.reconcile_digirig(SUITE_ROOT, inv)
    if not any(d.get("kind") == "digirig" for d in inv.devices.values()):
        HW.auto_declare_digirig(SUITE_ROOT, inv, HD_detect.detect_digirig())
    dra_present = HD_detect.detect_dra_pi()
    HW.reconcile_dra_pi(SUITE_ROOT, inv, dra_present)
    HW.auto_declare_dra_pi(SUITE_ROOT, inv, dra_present)
    # Re-template direwolf only when winlink's assigned device actually changed
    # (a reconcile-release when its dongle is unplugged) — merely declaring a
    # newly-plugged device no longer assigns it, so declaration alone is a no-op.
    if inv.assignments.get("winlink") != winlink_before:
        threading.Thread(target=_apply_hardware_async, daemon=True).start()

    services = {}
    for service in HW.SERVICE_UNITS:
        ok, reason = HW.can_start(inv, service)
        services[service] = {
            "device_id": HW.device_of(inv, service),
            "ok": ok,
            "reason": reason,
        }
    # Tag each dongle with its physical USB port (sysfs, by serial) so the Setup
    # card can show WHICH one — cheap, and works even for a busy dongle.
    devices = HW.device_states(inv)
    ports = HD_detect.rtl_sdr_usb_ports()
    for d in devices:
        d["usb_port"] = ports.get(d.get("serial", ""), "")
    return jsonify({"devices": devices, "errors": inv.errors, "services": services})


_DEVICE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
_DEVICE_KIND_REQUIRED_FIELDS = {
    "rtl-sdr": ["serial"],
    "digirig": ["ptt", "alsa"],
    "dra-pi":  ["ptt"],
}


@bp.route("/api/hardware/devices", methods=["POST"])
def api_hardware_declare_device():
    """Declare a new device into hardware.json (Slice 4b setup view, spec
    §7.4) — NAMING only, never assigns it to a service. Per this slice's
    scope decision, the per-card dropdown (index.html) only ever lists
    already-declared devices; this is the one route that turns a detected
    candidate into something assignable."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    device_id = (data.get("id") or "").strip()
    kind = (data.get("kind") or "").strip()
    label = (data.get("label") or "").strip() or device_id

    if not _DEVICE_ID_RE.match(device_id):
        return jsonify({"ok": False,
                        "error": "invalid id (alphanumeric/dash/underscore, 1-32 chars)"}), 400
    if kind not in HW.VALID_KINDS:
        return jsonify({"ok": False, "error": "unknown kind"}), 400

    device = {"id": device_id, "kind": kind, "label": label}
    for field in _DEVICE_KIND_REQUIRED_FIELDS[kind]:
        value = (data.get(field) or "").strip()
        if not value:
            return jsonify({"ok": False, "error": f"missing field: {field}"}), 400
        device[field] = value

    inv = HW.load(SUITE_ROOT)
    if device_id in inv.devices:
        return jsonify({"ok": False,
                        "error": f"device id {device_id!r} already declared"}), 409
    inv.devices[device_id] = device
    HW.save(SUITE_ROOT, inv)
    return jsonify({"ok": True, "device": device})


@bp.route("/api/hardware/detect")
def api_hardware_detect():
    """Enumerate attached hardware (RTL-SDRs, ALSA cards, serial devices) as
    candidates for the assignment editor. Read-only — never writes
    hardware.json (spec §6)."""
    return jsonify(HD_detect.scan())


@bp.route("/api/hardware/assign", methods=["POST"])
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


@bp.route("/api/hardware/release", methods=["POST"])
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


@bp.route("/api/hardware/burn-serial", methods=["POST"])
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


# ── HW/SRV assignment console (design 2026-07-28) ────────────────────────────
# The matrix console's live state + control endpoints. Console-enforced
# exclusive: /route reassigns one service per dongle (stop old, start new),
# guarded by the per-device lock. Built on the tested engine primitives in
# common/hardware.py (reroute/can_reroute/set_lock/warnings).

# Column order + display labels for the matrix. Only services OASIS can actually
# route + start/stop appear here: radio-live/RX is a future claimant, and
# OpenWebRX is self-configured (no engine unit, picks its own dongle) so it's
# controlled from its own service card, not the matrix.
_CONSOLE_SERVICES = ["aprs", "adsb", "winlink", "satellites"]
_SERVICE_DISPLAY = {"aprs": "APRS", "adsb": "ADS-B", "openwebrx": "ORX",
                    "winlink": "Winlink", "satellites": "SAT"}


def _console_state():
    """Live matrix state: the service catalog (id/label/eligible kinds), each
    device's single presented assignment + running + lock, and the health
    warnings. 'assigned' collapses the engine's advisory multi-assign to one
    service for the exclusive console view (the running one, else the first)."""
    inv = HW.load(SUITE_ROOT)
    services = [{"id": s, "name": _SERVICE_DISPLAY.get(s, s),
                 "kinds": sorted(HW.DEVICE_KIND_FOR_SERVICE.get(s, []))}
                for s in _CONSOLE_SERVICES]
    devices = []
    for did, d in inv.devices.items():
        holders = HW.assignees(inv, did)
        running = next((s for s in holders
                        if any(HW._default_is_active(u) for u in HW.service_units(inv, s))),
                       None)
        assigned = running or (holders[0] if holders else None)
        devices.append({"id": did, "label": d.get("label", did), "kind": d["kind"],
                        "serial": d.get("serial", ""), "locked": HW.is_locked(inv, did),
                        "assigned": assigned, "running": running is not None})
    return {"services": services, "devices": devices, "warnings": HW.warnings(inv)}


@bp.route("/api/hardware/console")
def api_hardware_console():
    """Live state for the HW/SRV matrix console (rail + matrix + warnings)."""
    return jsonify(_console_state())


@bp.route("/api/hardware/route", methods=["POST"])
def api_hardware_route():
    """Console-enforced exclusive reroute: put `service` on `device_id`, starting
    it and displacing whatever was on that dongle. 409 if a lock blocks it (the
    two-step 'unlock to move it' signal); 400 for unknown/ineligible device."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    service = (data.get("service") or "").strip()
    device_id = (data.get("device_id") or "").strip()
    if service not in HW.SERVICE_UNITS:
        return jsonify({"ok": False, "error": "unknown service"}), 400
    inv = HW.load(SUITE_ROOT)
    if device_id not in inv.devices:
        return jsonify({"ok": False, "error": "unknown device"}), 400
    allowed = HW.DEVICE_KIND_FOR_SERVICE.get(service)
    if allowed is not None and inv.devices[device_id]["kind"] not in allowed:
        return jsonify({"ok": False, "error": "wrong device kind for this service"}), 400
    ok, reason = HW.can_reroute(inv, service, device_id)
    if not ok:
        return jsonify({"ok": False, "error": reason, "reason": reason}), 409

    to_start = []

    def _stop(unit):
        _systemctl_seq(unit, ["stop"])

    # reroute stops old/displaced units + reassigns (persisted); defer starts
    # until AFTER apply writes the new device config for the moved service.
    HW.reroute(SUITE_ROOT, inv, service, device_id,
               start_fn=to_start.append, stop_fn=_stop)
    _apply_hardware_async()                       # sync: re-template device config
    for unit in to_start:
        _systemctl_seq(unit, ["start"])
    return jsonify({"ok": True})


@bp.route("/api/hardware/lock", methods=["POST"])
def api_hardware_lock():
    """Lock/unlock a device to its current assignment (protects it from any
    reroute — operator or auto-assign — until unlocked)."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    device_id = (data.get("device_id") or "").strip()
    locked = bool(data.get("locked"))
    inv = HW.load(SUITE_ROOT)
    if device_id not in inv.devices:
        return jsonify({"ok": False, "error": "unknown device"}), 400
    HW.set_lock(SUITE_ROOT, inv, device_id, locked)
    return jsonify({"ok": True, "locked": locked})


@bp.route("/api/hardware/stop-all", methods=["POST"])
def api_hardware_stop_all():
    """Emergency STOP ALL: stop every controllable service. Plain stop (no
    disable) — boot state is untouched. Used by the matrix header button and the
    resource guardian's countdown."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    _stop_all_units()
    return jsonify({"ok": True, "stopped": sorted(_EMERGENCY_STOP)})


@bp.route("/api/hardware/service-stop", methods=["POST"])
def api_hardware_service_stop():
    """Stop a single service's unit(s) without changing its assignment — the
    matrix toggle-off (the dongle stays assigned to it, just idle)."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    service = (data.get("service") or "").strip()
    if service not in HW.SERVICE_UNITS:
        return jsonify({"ok": False, "error": "unknown service"}), 400
    inv = HW.load(SUITE_ROOT)
    dev = inv.assignments.get(service)
    if dev and HW.is_locked(inv, dev):     # lock protects from ANY displacement, incl. stop
        return jsonify({"ok": False, "error": "source-locked", "reason": "source-locked"}), 409
    for unit in HW.service_units(inv, service):
        _systemctl_seq(unit, ["stop"])
    return jsonify({"ok": True})


# ── Resource guardian (design 2026-07-28) ────────────────────────────────────
# The one sanctioned autonomous action, SEPARATE from the observe-only assignment
# monitor: a server-side background thread (so it protects an UNATTENDED box —
# no browser needed) that arms a 30 s cancellable countdown on high temp/CPU/mem
# and then STOP ALLs. The dashboard only displays the countdown + offers Cancel.
# Pure decision logic lives in common/guardian.py (unit-tested); this is the shell.

_GUARD_STATE = {"mode": GUARD.IDLE, "deadline": None, "reason": None}
_GUARD_STATS = {"temp_c": None, "cpu_pct": None, "mem_pct": None}
_GUARD_TICK = 3.0


def _guardian_config_path():
    return os.path.join(config_paths.config_dir(SUITE_ROOT), "guardian.json")


def _load_guardian_config():
    """Enabled flag + thresholds (defaults merged). Enabled by default with the
    conservative DEFAULT_THRESHOLDS — the operator tunes/disables it."""
    try:
        with open(_guardian_config_path()) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    thresholds = dict(GUARD.DEFAULT_THRESHOLDS)
    thresholds.update(raw.get("thresholds") or {})
    return {"enabled": raw.get("enabled", True), "thresholds": thresholds}


def _save_guardian_config(cfg):
    os.makedirs(config_paths.config_dir(SUITE_ROOT), exist_ok=True)
    tmp = _guardian_config_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, _guardian_config_path())


def _read_stats(psutil):
    stats = {"temp_c": None, "cpu_pct": None, "mem_pct": None}
    if psutil is None:
        return stats
    try:
        stats["cpu_pct"] = psutil.cpu_percent(interval=None)
    except Exception:
        pass
    try:
        stats["mem_pct"] = psutil.virtual_memory().percent
    except Exception:
        pass
    try:
        temps = psutil.sensors_temperatures()
        for key in ("cpu_thermal", "cpu-thermal", "soc_thermal", "coretemp"):
            if key in temps and temps[key]:
                stats["temp_c"] = round(temps[key][0].current, 1)
                break
    except Exception:
        pass
    return stats


def _stop_all_units():
    for unit in _EMERGENCY_STOP:      # every controllable service EXCEPT WebSSH
        _systemctl_seq(unit, ["stop"])


def _guardian_runner():
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            psutil.cpu_percent(interval=None)   # prime the rolling baseline
        except Exception:
            pass
    global _GUARD_STATE, _GUARD_STATS
    while True:
        time.sleep(_GUARD_TICK)
        cfg = _load_guardian_config()
        if not cfg["enabled"]:
            _GUARD_STATE = {"mode": GUARD.IDLE, "deadline": None, "reason": None}
            continue
        _GUARD_STATS = _read_stats(psutil)
        _GUARD_STATE, action = GUARD.evaluate(_GUARD_STATS, cfg["thresholds"],
                                              _GUARD_STATE, time.time())
        if action == "fire":
            _stop_all_units()


threading.Thread(target=_guardian_runner, name="oasis-guardian", daemon=True).start()


# ── Boot reconciler: assigned hardware comes back after a reboot ─────────────
# The assignment console persists WHAT a dongle is for (configuration/
# hardware.json) but its /route only issues a plain `systemctl start` — no
# `enable` — so before this, an assignment survived a reboot while the running
# service did not, and the operator had to re-enable everything by hand.
#
# Rather than sprinkle `enable` around (which would also make every assigned
# service race for USB simultaneously at boot), this reconciles once per boot
# from the persisted assignments: it reads hardware.json, asks
# HW.boot_start_plan what that implies, and starts those units one at a time
# with a gap between them.
#
# ONCE PER BOOT, not once per Flask start: `./start.sh` restarts the server
# often, and a service the operator deliberately stopped must not come back
# just because the web app was restarted. The kernel's boot_id changes on every
# boot, so a stamp file holding it tells the two cases apart exactly — no
# reliance on /tmp or /run being cleared.
_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
_BOOT_STAMP = os.path.join(SUITE_ROOT, "configuration", ".hw-boot-applied")
# Seconds to wait before the FIRST start (USB enumeration can lag the web app
# at boot), then between each subsequent unit. Both overridable for slow or
# crowded boxes; set OASIS_HW_BOOT_AUTOSTART=0 to switch the reconciler off.
_BOOT_AUTOSTART = os.environ.get("OASIS_HW_BOOT_AUTOSTART", "1") != "0"
_BOOT_SETTLE_S = int(os.environ.get("OASIS_HW_BOOT_SETTLE", "20"))
_BOOT_STAGGER_S = int(os.environ.get("OASIS_HW_BOOT_STAGGER", "8"))


def _current_boot_id():
    """This boot's kernel boot_id, or None off Linux / if unreadable."""
    try:
        with open(_BOOT_ID_PATH, "r") as fh:
            return fh.read().strip() or None
    except Exception:
        return None


def _boot_already_applied(boot_id):
    try:
        with open(_BOOT_STAMP, "r") as fh:
            return fh.read().strip() == boot_id
    except Exception:
        return False


def _mark_boot_applied(boot_id):
    try:
        os.makedirs(os.path.dirname(_BOOT_STAMP), exist_ok=True)
        tmp = _BOOT_STAMP + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(boot_id + "\n")
        os.replace(tmp, _BOOT_STAMP)      # atomic, like every other config write
    except Exception:
        pass                              # best-effort; worst case we re-run next start


def _unit_is_active(unit):
    try:
        r = subprocess.run(["systemctl", "is-active", f"{unit}.service"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _boot_log(msg):
    """One line to stdout, which oasis.service routes to the journal
    (StandardOutput=journal). This ran completely silently at first, and when it
    failed on a real box there was nothing to diagnose from: no record that it
    had run, what it planned, or whether a start took. Cheap insurance —
    a handful of lines once per boot."""
    try:
        print("[oasis-hw-boot] " + msg, flush=True)
    except Exception:
        pass


def _boot_reconcile_runner():
    boot_id = _current_boot_id()
    if boot_id is None:
        return                            # not Linux (dev box) — nothing to reconcile
    if not _BOOT_AUTOSTART:
        return                            # switched off by the operator
    if _boot_already_applied(boot_id):
        return                            # already done this boot; a Flask restart is not a reboot
    try:
        inv = HW.load(SUITE_ROOT)
        plan = HW.boot_start_plan(inv)
    except Exception as exc:
        _boot_log("could not read assignments: %s" % exc)
        return
    if not plan:
        return
    _boot_log("assigned services to start: %s (settle %ss, stagger %ss)"
              % (", ".join(plan), _BOOT_SETTLE_S, _BOOT_STAGGER_S))
    time.sleep(_BOOT_SETTLE_S)

    # Claim the boot HERE — immediately before acting, not before the sleep.
    #
    # scripts/start-server.sh runs `python server/app.py`, which imports this
    # module (starting this thread) and THEN os.execv()s into gunicorn. execv
    # replaces the process image and destroys every thread, so this first
    # reconciler is killed a second or two into the settle sleep. Claiming up
    # front meant that doomed thread consumed the boot: the gunicorn worker
    # imported the module moments later, saw the boot as handled, and did
    # nothing — services never started. Claiming late means a process that does
    # not survive the settle simply never claims, and the survivor does the work.
    #
    # Re-check first: whoever claims immediately before starting wins, and a
    # replay would be harmless anyway (already-active units are skipped and
    # `systemctl start` on a running unit is a no-op).
    if _boot_already_applied(boot_id):
        return
    _mark_boot_applied(boot_id)

    for i, unit in enumerate(plan):
        if i:
            time.sleep(_BOOT_STAGGER_S)   # gap between services, not before the first
        if _unit_is_active(unit):
            _boot_log("%s already active — leaving it alone" % unit)
            continue
        _systemctl_seq(unit, ["start"])
        # _systemctl_seq deliberately swallows failures, so confirm rather than
        # assume: a denied sudo rule or a unit that refuses to start would
        # otherwise be invisible until the operator noticed the service missing.
        _boot_log("started %s -> %s" % (unit, "active" if _unit_is_active(unit) else "NOT active"))


threading.Thread(target=_boot_reconcile_runner, name="oasis-hw-boot", daemon=True).start()


@bp.route("/api/hardware/guardian")
def api_hardware_guardian():
    """Guardian state for the dashboard: mode, live countdown, the tripping
    metric, current stats, and the enabled/threshold config."""
    cfg = _load_guardian_config()
    st = _GUARD_STATE
    seconds_left = None
    if st.get("mode") == GUARD.ARMED and st.get("deadline"):
        seconds_left = max(0, int(round(st["deadline"] - time.time())))
    return jsonify({"enabled": cfg["enabled"], "thresholds": cfg["thresholds"],
                    "mode": st.get("mode", GUARD.IDLE), "reason": st.get("reason"),
                    "seconds_left": seconds_left, "stats": _GUARD_STATS})


@bp.route("/api/hardware/guardian/cancel", methods=["POST"])
def api_hardware_guardian_cancel():
    """Operator override — cancel the countdown (or clear a tripped state)."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    global _GUARD_STATE
    _GUARD_STATE = GUARD.cancel(_GUARD_STATE)
    return jsonify({"ok": True})


@bp.route("/api/hardware/guardian/config", methods=["POST"])
def api_hardware_guardian_config():
    """Enable/disable the guardian and tune its thresholds."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    cfg = _load_guardian_config()
    if "enabled" in data:
        cfg["enabled"] = bool(data["enabled"])
    if isinstance(data.get("thresholds"), dict):
        for key in ("temp_c", "cpu_pct", "mem_pct"):
            if key in data["thresholds"]:
                try:
                    cfg["thresholds"][key] = max(_THRESHOLD_MIN[key],
                                                 float(data["thresholds"][key]))
                except (TypeError, ValueError):
                    pass
    _save_guardian_config(cfg)
    return jsonify({"ok": True, "enabled": cfg["enabled"], "thresholds": cfg["thresholds"]})


