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
    # openwebrx's assignment is advisory (no apply hook — it's configured in
    # OpenWebRX's own Admin → SDR profiles UI). It's tracked only so the
    # start-time shared-dongle check knows which RTL-SDR it "wants"; empty
    # unit list mirrors aprs's soundcard-only base. See v2 design §2.
    "openwebrx": [],
}

# Which device kind(s) each logical service may be assigned.
DEVICE_KIND_FOR_SERVICE = {
    "aprs":      {"rtl-sdr", "digirig", "dra-pi"},
    "winlink":   {"digirig", "dra-pi"},
    "adsb":      {"rtl-sdr"},
    "openwebrx": {"rtl-sdr"},
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
    """The FIRST service a device is assigned to, or None. rtl-sdr devices are
    a shared resource (§2) — several services may point at the same dongle;
    use assignees() for the full list. Retained single-valued for exclusive
    (digirig/dra-pi) kinds where at most one service can ever hold a device."""
    for service, did in inv.assignments.items():
        if did == device_id:
            return service
    return None


def assignees(inv, device_id):
    """Every service pointing at a device, in assignment (dict) order. Under
    the shared-rtl-sdr model a dongle can be assigned to aprs/adsb/openwebrx
    at once — assignment is advisory bookkeeping; exclusivity is acquired at
    start time, not here."""
    return [service for service, did in inv.assignments.items() if did == device_id]


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
    card. 🟢 free = no assignees, or assigned-but-idle (shared default); 🔴 in
    use = one of its assigned services' unit(s) is systemctl is-active. A
    shared rtl-sdr may have several assignees (advisory); `assignee` names the
    running claimant when one is active, else lists all who default to it, so
    the operator can see the dongle is spoken-for and reassign via the
    dropdown."""
    out = []
    for did, d in inv.devices.items():
        services = assignees(inv, did)
        running_svc = next(
            (svc for svc in services
             if any(is_active(u) for u in service_units(inv, svc))),
            None)
        label = running_svc or (", ".join(services) if services else None)
        out.append({"id": did, "label": d.get("label", did), "kind": d["kind"],
                    "assignee": label, "running": running_svc is not None})
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
    """(ok, holder). holder is the blocking service's name, or None. Refuses the
    wrong kind, and — for exclusive (digirig/dra-pi) devices only — a device
    already assigned to a DIFFERENT service. rtl-sdr devices are shared and
    never refused on holder grounds (§2)."""
    if device_id not in inv.devices:
        return False, None
    kind = inv.devices[device_id]["kind"]
    allowed_kinds = DEVICE_KIND_FOR_SERVICE.get(service)
    if allowed_kinds is not None and kind not in allowed_kinds:
        return False, None
    # rtl-sdr is a SHARED resource: aprs/adsb/openwebrx may all be assigned the
    # same dongle (advisory bookkeeping — §2). Exclusivity is acquired at start
    # time, not here. digirig/dra-pi (winlink) stay exclusive: one holder only.
    if kind != "rtl-sdr":
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
        # rtl-sdr is shared: a dongle already assigned to another service is
        # still a valid default here (all three RTL consumers converge on the
        # first dongle out of the box — the operator spreads them across
        # dongles via the dropdown). Exclusive kinds skip already-claimed ones.
        if device["kind"] != "rtl-sdr" and assignee(inv, device_id) is not None:
            continue
        inv.assignments[service] = device_id
        save(repo_root, inv)
        return inv
    return inv


def auto_declare_rtl_sdrs(repo_root, inv, detected_serials):
    """Declare every detected RTL-SDR whose serial is UNIQUE among the detected
    set and not already declared (auto id `rtl-sdr-<serial>`), and persist.
    Persist once if anything was added; no-op otherwise.

    Dongles that share a serial — the ubiquitous factory `00000001` duplicate —
    are indistinguishable to software, so they're skipped: the operator burns a
    unique serial onto one first (hardware_detect.can_burn_serial / the burn
    flow), after which the next detect declares both.

    Supersedes the original single-dongle-only auto-declare: a second,
    uniquely-serialed dongle was detected but left undeclared, so it never
    appeared in the assignment dropdowns (which list DECLARED devices). Still
    matches the design's "list detected devices, that's it" intent — just for
    N unambiguous dongles instead of exactly one."""
    counts = {}
    for s in detected_serials:
        counts[s] = counts.get(s, 0) + 1
    declared_serials = {d.get("serial") for d in inv.devices.values()
                        if d.get("kind") == "rtl-sdr"}
    changed = False
    for serial in detected_serials:
        if counts[serial] != 1:          # ambiguous duplicate — needs a burn first
            continue
        if serial in declared_serials:
            continue
        device_id = f"rtl-sdr-{serial}"
        if device_id in inv.devices:      # id already taken (non-rtl or manual)
            continue
        inv.devices[device_id] = {"id": device_id, "kind": "rtl-sdr", "serial": serial,
                                   "label": f"RTL-SDR ({serial})"}
        declared_serials.add(serial)
        changed = True
    if changed:
        save(repo_root, inv)
    return inv
