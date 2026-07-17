"""
Service-control route blueprint — the dashboard power buttons. /api/service
starts/stops/restarts the allowlisted OASIS systemd units through the scoped
sudoers grant (scripts/enable-service-controls.py). Also owns the unit
allowlists shared with the health checks and the hardware-release path.
Extracted verbatim from server/app.py in the blueprint split; URL unchanged.
"""

import subprocess

from flask import Blueprint, jsonify, request

import appconfig
from common import hardware as HW

SUITE_ROOT = appconfig.SUITE_ROOT

bp = Blueprint("service_control", __name__)

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
#
# This is exactly the set of RTL-SDR consumers that share the one dongle
# advisorily (aprs-sdr-feed / dump1090-fa / openwebrx — see HW_SERVICE_FOR_CARD
# in index.html). They're all installed OFF (e.g. setup passes enable-rtl-sdr.py
# --no-enable), so nothing auto-starts after install or reboot. But the operator
# hands the dongle between them from the dashboard — stop the APRS feed, start
# ADS-B — and that choice must survive a reboot: the one they started comes back
# enabled, the one they stopped stays disabled. So start enables / stop disables
# each of them.
#
# ADS-B's card controls dump1090-fa (the decoder); its adsb-api recorder follows
# via the unit's Wants=/PartOf= drop-in, so enabling dump1090-fa alone brings the
# whole ADS-B stack back on boot. kiwix is deliberately NOT here — it's not a
# hardware-shared service, so its boot state is left untouched (transient
# start/stop) like every other unit.
_PERSIST_BOOT_STATE = {"aprs-sdr-feed", "dump1090-fa"}

# Services migrated OFF the hard "unassigned -> refuse" gate below, per
# specs/2026-07-15-hardware-conflict-resolution-v2-design.md — "never refuse a
# start" is the target end state for every hardware-bound service. The three
# RTL-SDR consumers (adsb, aprs feed, openwebrx) share one dongle advisorily
# and resolve contention at start-click time (index.html resolveHardwareConflict),
# not via a server gate. GrayWolf stays permanently absent from HW.SERVICE_UNITS
# (no apply hook ever backed its assignment — see common/hardware.py). winlink
# keeps the hard gate for now and joins on its own turn.
_HW_GATE_MIGRATED = {"adsb", "aprs", "openwebrx"}


def _systemctl_seq(unit, verbs):
    """Best-effort `sudo -n systemctl <verb> <unit>.service` for each verb in
    order (used for conflict services — failures are tolerated/ignored)."""
    for verb in verbs:
        try:
            subprocess.run(["sudo", "-n", "systemctl", verb, f"{unit}.service"],
                           capture_output=True, text=True, timeout=30)
        except Exception:
            pass


@bp.route("/api/service", methods=["POST"])
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
        if hw_service and hw_service not in _HW_GATE_MIGRATED:
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


