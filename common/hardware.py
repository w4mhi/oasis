"""common/hardware.py — hardware-aware conflict engine (Slice 1: engine core).

Exclusive "switchboard" device allocation: a device is assigned to at most one
logical service at a time. Conflicts are prevented at ASSIGNMENT time
(can_assign), not refereed at start time — the runtime only ever needs to check
whether THIS service's own assignment is present and attached (can_start),
never whether another service is already using the device.
"""
import json
import os
import subprocess
import sys

from common import config_paths

VALID_KINDS = {"rtl-sdr", "digirig", "dra-pi"}

# Logical service -> the systemd unit(s) it starts. One unit per service in
# Slice 1; dual-mode selection (aprs's SDR-vs-radio-port TNC modes) and
# per-RTL feed units land in a later slice.
SERVICE_UNITS = {
    "aprs":      [],
    "winlink":   ["pat-direwolf"],
    "adsb":      ["dump1090-fa"],
}

# Which device kind(s) each logical service may be assigned.
DEVICE_KIND_FOR_SERVICE = {
    "aprs":      {"rtl-sdr", "digirig", "dra-pi"},
    "winlink":   {"digirig", "dra-pi"},
    "adsb":      {"rtl-sdr"},
}

# aprs runs an extra RX feed unit (rtl_fm -> UDP -> GrayWolf) ONLY when assigned
# an SDR; on a radio port it's GrayWolf soundcard-only. All other services are
# mode-invariant. (GrayWolf itself is web-admin-configured — OASIS binds only the
# feed's dongle, not GrayWolf's radio interface.)
#
# openwebrx is intentionally absent from SERVICE_UNITS/DEVICE_KIND_FOR_SERVICE:
# scripts/apply_hardware.py has no apply hook for it (its RTL-SDR is picked
# entirely inside OpenWebRX's own Admin -> SDR profiles UI), so an OASIS-level
# assignment would never do anything — it would only block START on a
# no-op gate. Same reasoning as GrayWolf above.
APRS_FEED_UNIT = "aprs-sdr-feed"


def service_units(inv, service):
    """The systemd unit(s) implementing `service` given the current inventory.
    Only `aprs` is mode-dependent: an rtl-sdr assignment prepends the RX feed."""
    base = list(SERVICE_UNITS.get(service, []))
    if service == "aprs":
        dev_id = inv.assignments.get("aprs")
        dev = inv.devices.get(dev_id) if dev_id else None
        if dev and dev.get("kind") == "rtl-sdr":
            return [APRS_FEED_UNIT] + base
    return base


def service_for_unit(inv, unit):
    """Reverse map: the logical service a systemd unit belongs to (or None),
    resolved against the current inventory."""
    for svc in SERVICE_UNITS:
        if unit in service_units(inv, svc):
            return svc
    return None


class Inventory:
    def __init__(self, devices, assignments, errors=None):
        self.devices = devices          # dict: device id -> device dict
        self.assignments = assignments  # dict: service -> device id
        self.errors = errors or []      # validation errors (non-fatal, surfaced)


def _empty_inventory():
    return Inventory(devices={}, assignments={})


def load(repo_root):
    """Load + validate configuration/hardware.json. Never raises — an absent or
    corrupt file returns an empty inventory (first-run / degraded state)."""
    path = config_paths.hardware_json(repo_root)
    if not os.path.exists(path):
        return _empty_inventory()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return _empty_inventory()

    devices = {}
    errors = []
    for d in raw.get("devices", []):
        did = d.get("id")
        kind = d.get("kind")
        if not did or did in devices:
            errors.append(f"skipped device with missing/duplicate id: {d!r}")
            continue
        if kind not in VALID_KINDS:
            errors.append(f"device {did!r}: unknown kind {kind!r}")
            continue
        devices[did] = d

    assignments = {}
    for service, device_id in raw.get("assignments", {}).items():
        if device_id not in devices:
            errors.append(f"service {service!r} assigned to unknown device {device_id!r}")
            assignments[service] = device_id  # keep it — can_start() distinguishes
            continue                          # "unassigned" from "device missing"
        allowed_kinds = DEVICE_KIND_FOR_SERVICE.get(service)
        if allowed_kinds is not None and devices[device_id]["kind"] not in allowed_kinds:
            errors.append(
                f"service {service!r} assigned to device {device_id!r} of kind "
                f"{devices[device_id]['kind']!r}, not in {sorted(allowed_kinds)!r}")
            continue  # kind mismatch: drop (can only happen via manual edit —
                      # can_assign(), built in a later task, prevents this
                      # through the API)
        assignments[service] = device_id

    return Inventory(devices=devices, assignments=assignments, errors=errors)


def save(repo_root, inv):
    """Write the inventory back to configuration/hardware.json (atomic replace)."""
    path = config_paths.hardware_json(repo_root)
    os.makedirs(config_paths.config_dir(repo_root), exist_ok=True)
    payload = {
        "version": 1,
        "devices": list(inv.devices.values()),
        "assignments": inv.assignments,
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def assignee(inv, device_id):
    """The single service a device is assigned to, or None."""
    for service, did in inv.assignments.items():
        if did == device_id:
            return service
    return None


def device_of(inv, service):
    return inv.assignments.get(service)


def _default_is_active(unit):
    """systemctl is-active <unit>.service. Non-Linux or errors -> False."""
    if sys.platform != "linux":
        return False
    try:
        r = subprocess.run(["systemctl", "is-active", f"{unit}.service"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def device_states(inv, is_active=_default_is_active):
    """[{id, label, kind, assignee, running}] — drives the dashboard allocation
    card. 🟢 free = assignee is None OR assigned-but-idle; 🔴 in use = the
    assigned service's unit(s) are systemctl is-active."""
    out = []
    for did, d in inv.devices.items():
        service = assignee(inv, did)
        running = any(is_active(u) for u in service_units(inv, service)) if service else False
        out.append({"id": did, "label": d.get("label", did), "kind": d["kind"],
                    "assignee": service, "running": running})
    return out


def can_start(inv, service, is_active=_default_is_active):
    """(ok, reason). reason in {"", "unassigned", "device-not-attached"}. Under
    exclusive allocation a device can never be held by ANOTHER service, so
    this can only fail for the requesting service's OWN assignment state."""
    device_id = inv.assignments.get(service)
    if device_id is None:
        return False, "unassigned"
    if device_id not in inv.devices:
        return False, "device-not-attached"
    return True, ""


def can_assign(inv, service, device_id):
    """(ok, holder). holder is the blocking service's name, or None. Refuses a
    device already assigned to a DIFFERENT service, or the wrong kind."""
    if device_id not in inv.devices:
        return False, None
    allowed_kinds = DEVICE_KIND_FOR_SERVICE.get(service)
    if allowed_kinds is not None and inv.devices[device_id]["kind"] not in allowed_kinds:
        return False, None
    holder = assignee(inv, device_id)
    if holder is not None and holder != service:
        return False, holder
    return True, None


def assign(repo_root, inv, service, device_id):
    """Assign device_id to service and persist. Raises ValueError if
    can_assign() refuses (caller should check can_assign() first to report a
    clean error instead of catching this)."""
    ok, holder = can_assign(inv, service, device_id)
    if not ok:
        raise ValueError(f"cannot assign {device_id!r} to {service!r} (holder={holder!r})")
    inv.assignments[service] = device_id
    save(repo_root, inv)
    return inv


def release(repo_root, inv, service, stop_fn=None):
    """Unassign service's device, persist. If stop_fn is given, it is called
    for each of the service's units (frees the device) BEFORE the assignment
    is cleared — no auto-restart of anything else."""
    if service in inv.assignments:
        if stop_fn is not None:
            for unit in SERVICE_UNITS.get(service, []):
                stop_fn(unit)
        del inv.assignments[service]
        save(repo_root, inv)
    return inv


def default_assign(repo_root, inv, service, allowed_kinds):
    """If `service` has no assignment yet, assign it the first free declared
    device (in hardware.json declaration order) whose kind is in
    `allowed_kinds`, and persist. No-op if `service` is already assigned or
    no free compatible device exists.

    specs/2026-07-15-hardware-conflict-resolution-v2-design.md §3: reverses
    the original design's "nothing auto-assigned, ever" stance for the
    common single-dongle case — the operator can still override via the
    per-card dropdown."""
    if service in inv.assignments:
        return inv
    for device_id, device in inv.devices.items():
        if device["kind"] not in allowed_kinds:
            continue
        if assignee(inv, device_id) is not None:
            continue
        inv.assignments[service] = device_id
        save(repo_root, inv)
        return inv
    return inv
