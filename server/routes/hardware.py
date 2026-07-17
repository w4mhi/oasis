"""
Hardware route blueprint — device declaration/assignment for the dashboard
HARDWARE card (RTL-SDRs, DigiRig, DRA-Pi), auto-declare/reconcile on poll,
and the burn-serial flow. Extracted verbatim from server/app.py in the
blueprint split; URLs unchanged.
"""

import os
import re
import subprocess
import threading

from flask import Blueprint, jsonify, request

import appconfig
from common import hardware as HW
from common import hardware_detect as HD_detect
from routes.service_control import _systemctl_seq

SUITE_ROOT = appconfig.SUITE_ROOT

bp = Blueprint("hardware", __name__)


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
    on a Pi Zero 2 W, where running rtl_test constantly would also contend with
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
    # All three RTL-SDR consumers default to the first present dongle (shared —
    # §2/§3); the operator spreads them across dongles via each card's dropdown.
    # aprs is pinned to rtl-sdr for the DEFAULT so it never auto-grabs a
    # digirig/dra-pi earmarked for winlink (its full kind set still governs
    # manual assignment).
    for _svc in ("adsb", "openwebrx", "aprs"):
        HW.default_assign(SUITE_ROOT, inv, _svc, {"rtl-sdr"})

    # Winlink radio ports auto-declare/undeclare like the SDRs — no manual form:
    #   DigiRig — a lone CP210x PTT by-id (detect_digirig);
    #   DRA-Pi  — its audioinjectorpi ALSA card (detect_dra_pi), fully fixed.
    # Winlink RF auto-assigns to whichever is present (both → first, operator
    # flips via the dropdown). Their PTT + audio flow straight into direwolf via
    # winlink.apply, so when a declaration or winlink's assignment actually
    # changes, re-template direwolf right away (threaded — never block the poll).
    winlink_before = inv.assignments.get("winlink")
    radio_before = {did for did, d in inv.devices.items() if d.get("kind") in ("digirig", "dra-pi")}
    HW.reconcile_digirig(SUITE_ROOT, inv)
    if not any(d.get("kind") == "digirig" for d in inv.devices.values()):
        HW.auto_declare_digirig(SUITE_ROOT, inv, HD_detect.detect_digirig())
    dra_present = HD_detect.detect_dra_pi()
    HW.reconcile_dra_pi(SUITE_ROOT, inv, dra_present)
    HW.auto_declare_dra_pi(SUITE_ROOT, inv, dra_present)
    HW.default_assign(SUITE_ROOT, inv, "winlink", {"digirig", "dra-pi"})
    radio_after = {did for did, d in inv.devices.items() if d.get("kind") in ("digirig", "dra-pi")}
    if inv.assignments.get("winlink") != winlink_before or radio_after != radio_before:
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


