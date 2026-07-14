#!/usr/bin/env python3
"""scripts/apply_hardware.py — re-template each hardware-claiming service's device
config from configuration/hardware.json, then one `systemctl daemon-reload`.

The engine (common/hardware.py) is pure and never imports services; THIS module
owns the service -> apply-hook registry (it may import services/*). Each hook has
signature apply(repo_root, device_dict_or_None): bind when a device dict is
given, clear the pinning when None.
"""
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from common import hardware as HW
from services.adsb.common import adsb as _adsb

# service name -> apply(repo_root, device_or_None). Grows in a later slice
# (aprs, winlink). OpenWebRX is intentionally absent (not device-bound).
DEFAULT_HOOKS = {
    "adsb": _adsb.apply,
}


def _daemon_reload():
    if sys.platform != "linux":
        return
    try:
        subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"],
                       capture_output=True, text=True, timeout=30)
    except Exception:
        pass


def apply_all(repo_root, hooks=None, load_fn=None, reload_fn=None):
    """Apply device bindings for every catalogued service from the current
    inventory. Assigned + present -> hook(device dict); unassigned or device
    detached -> hook(None) (clears any stale pinning). One reload at the end.
    Returns the list of service names whose hook was called."""
    hooks = DEFAULT_HOOKS if hooks is None else hooks
    load_fn = HW.load if load_fn is None else load_fn
    reload_fn = _daemon_reload if reload_fn is None else reload_fn

    inv = load_fn(repo_root)
    applied = []
    for service, hook in hooks.items():
        device_id = inv.assignments.get(service)
        device = inv.devices.get(device_id) if device_id else None
        hook(repo_root, device)
        applied.append(service)
    reload_fn()
    return applied


def main():
    applied = apply_all(_REPO_ROOT)
    print("applied device bindings for:", ", ".join(applied) or "(none)")


if __name__ == "__main__":
    main()
