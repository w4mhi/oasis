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
    "aprs":      ["graywolf"],
    "winlink":   ["pat-direwolf"],
    "adsb":      ["dump1090-fa"],
    "openwebrx": ["openwebrx"],
}

# Which device kind(s) each logical service may be assigned.
DEVICE_KIND_FOR_SERVICE = {
    "aprs":      {"rtl-sdr", "digirig", "dra-pi"},
    "winlink":   {"digirig", "dra-pi"},
    "adsb":      {"rtl-sdr"},
    "openwebrx": {"rtl-sdr"},
}


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
