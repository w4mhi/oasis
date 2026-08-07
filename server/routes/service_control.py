"""
Service-control route blueprint — the dashboard power buttons. /api/service
starts/stops/restarts the allowlisted OASIS systemd units through the scoped
sudoers grant (scripts/enable-service-controls.py). Also owns the unit
allowlists shared with the health checks and the hardware-release path.
Extracted verbatim from server/app.py in the blueprint split; URL unchanged.
"""

import subprocess

from flask import Blueprint, jsonify, request

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
# in index.html). They're all installed OFF (e.g. setup passes services/rtl-feed/install.py
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


def _with_sudoers_hint(err):
    """Append the fix-it hint when systemctl failed for lack of permission.
    The sudoers rule is per-unit AND per-verb (see scripts/enable-service-
    controls.py UNITS/ACTIONS), so a rule written before a unit was installed
    authorizes start but not enable — which is exactly how a boot-state step
    fails while the action itself succeeds."""
    low = (err or "").lower()
    if "password" in low or "a terminal is required" in low or "not allowed" in low:
        return err + " — run: python3 scripts/enable-service-controls.py (grants permission)"
    return err


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

    # No hardware start-gate: "never refuse a start" is the end state for every
    # hardware-bound service (spec 2026-07-15-hardware-conflict-resolution-v2 §4).
    # Device assignment is advisory bookkeeping; a service acquires its device
    # exclusively only when it actually STARTS. Cross-service contention on the
    # shared RTL-SDR (and Winlink RF vs. GrayWolf on a radio port) is resolved
    # client-side at start-click time in index.html's resolveHardwareConflict,
    # not by a server 409. Assign-time exclusivity for digirig/dra-pi still holds
    # via /api/hardware/assign's can_assign check.
    affected = []

    # Build the systemctl step(s). Boot-state-tracking units enable-on-start /
    # disable-on-stop; the `action` verb is the one whose success we report on.
    persist = unit in _PERSIST_BOOT_STATE
    if action == "start":
        steps = ["enable", "start"] if persist else ["start"]
    elif action == "stop":
        steps = ["stop", "disable"] if persist else ["stop"]
    else:
        steps = ["restart"]

    # Every step's result is kept, not just the primary verb's. A failed
    # enable/disable used to be discarded, so a box whose sudoers rule predated
    # these units reported a clean "started" while silently never persisting the
    # boot state — the operator only found out at the next reboot.
    result = None
    boot_step = None          # (verb, CompletedProcess) for a FAILED enable/disable
    try:
        for verb in steps:
            r = subprocess.run(["sudo", "-n", "systemctl", verb, f"{unit}.service"],
                               capture_output=True, text=True, timeout=30)
            if verb == action:        # the primary verb (start/stop/restart)
                result = r
            elif r.returncode != 0 and boot_step is None:
                boot_step = (verb, r)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "affected": affected}), 500

    # Re-query state regardless of rc so the UI can refresh from the truth.
    try:
        active = subprocess.run(["systemctl", "is-active", f"{unit}.service"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        active = "unknown"

    if result is None or result.returncode != 0:
        err = _with_sudoers_hint((result.stderr or result.stdout or "").strip()
                                 if result else "no command run")
        return jsonify({"ok": False, "service": unit, "action": action,
                        "active": active or "unknown", "affected": affected,
                        "error": err or "systemctl failed"}), 500

    # The action itself worked. If its boot-state companion did not, say so
    # rather than returning a bare success: the service is running (or stopped)
    # NOW, but that state was not persisted, so a reboot will contradict it.
    payload = {"ok": True, "service": unit, "action": action,
               "active": active or "unknown", "affected": affected}
    if boot_step is not None:
        verb, r = boot_step
        detail = _with_sudoers_hint((r.stderr or r.stdout or "").strip()
                                    or f"systemctl {verb} failed")
        payload["warning"] = (f"{unit} was {action}ed, but could not be {verb}d for boot "
                              f"— this will not survive a reboot: {detail}")
        payload["boot_state_persisted"] = False
    elif persist:
        payload["boot_state_persisted"] = True
    return jsonify(payload)


