"""common/hardware_detect.py — parse hardware-enumeration tool output into
candidate lists for the hardware-aware conflict engine's assignment editor.

Pure parsers only (this file). The subprocess calls that produce the raw
output live in the scan functions appended by a later task in this same
file — kept separate so every parser here is testable with fixture strings,
no Linux/root/hardware required.

BENCH-VERIFY: `rtl_test -t` and `aplay -l` are standard librtlsdr/ALSA-utils
output formats; the parsers below match the well-established shape, but
neither can be exercised against real output on this dev machine — confirm on
the target Pi against actual connected hardware.
"""
import os
import re
import subprocess
import sys


def parse_rtl_test_devices(output):
    """Parse `rtl_test -t` output into [{"index": int, "serial": str}, ...].
    Matches lines like '  0:  Realtek, RTL2838UHIDIR, SN: 00000001'."""
    devices = []
    for line in output.splitlines():
        m = re.match(r'\s*(\d+):\s*.*?SN:\s*(\S+)', line)
        if m:
            devices.append({"index": int(m.group(1)), "serial": m.group(2)})
    return devices


def parse_aplay_cards(output):
    """Parse `aplay -l` output into [{"card": int, "id": str, "description": str}, ...].
    Matches lines like 'card 0: audioinjectorpi [AudioInjector Pi], device 0: ...'."""
    cards = []
    for line in output.splitlines():
        m = re.match(r'card\s+(\d+):\s*(\S+)\s*\[([^\]]*)\]', line)
        if m:
            cards.append({"card": int(m.group(1)), "id": m.group(2),
                         "description": m.group(3)})
    return cards


def list_serial_by_id(directory="/dev/serial/by-id"):
    """List /dev/serial/by-id/* entries as [{"path": str, "label": str}, ...],
    sorted by filename. Used to surface Digirig (CP210x) PTT serial candidates.
    Missing directory (no USB-serial devices present) -> []."""
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        out.append({"path": os.path.join(directory, name), "label": name})
    return out


def _run_text(argv, timeout=10):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")  # rtl_test writes to stderr
    except Exception:
        return ""


def scan():
    """Enumerate attached hardware for the assignment editor. Linux only —
    returns empty candidate lists elsewhere (mirrors the no-op pattern used by
    every apply()/install writer in this project)."""
    if sys.platform != "linux":
        return {"rtl_sdr": [], "alsa": [], "serial": []}
    return {
        "rtl_sdr": parse_rtl_test_devices(_run_text(["rtl_test", "-t"])),
        "alsa": parse_aplay_cards(_run_text(["aplay", "-l"])),
        "serial": list_serial_by_id(),
    }


# ── rtl_eeprom "name this dongle" helper ───────────────────────────────────────
# Burning a unique serial requires EXCLUSIVE USB access to the target dongle.
# Cheap RTL-SDR dongles frequently ship with the same factory serial
# (00000001), so this refuses to run unless the operator's situation is
# unambiguous: nothing that consumes an SDR is currently running, and there is
# exactly one detected candidate (or a caller-supplied explicit target — left
# for the route/CLI layer to resolve, not this pure guard).
SDR_CONSUMING_UNITS = ["dump1090-fa", "aprs-sdr-feed", "openwebrx"]


def sdr_services_active(is_active):
    return [u for u in SDR_CONSUMING_UNITS if is_active(u)]


def can_burn_serial(candidates, is_active):
    """(ok, reason). Refuses if any SDR-consuming service is active (exclusive
    access required), if there's no detected dongle, or if there's more than
    one (ambiguous — the operator must unplug the others or the caller must
    pass an explicit target, which is out of scope for this guard)."""
    active = sdr_services_active(is_active)
    if active:
        return False, f"stop these first: {', '.join(active)}"
    if not candidates:
        return False, "no RTL-SDR detected"
    if len(candidates) > 1:
        return False, "ambiguous — more than one RTL-SDR detected; unplug the others"
    return True, ""


def burn_serial(new_serial):
    """Run `rtl_eeprom -s <new_serial>` against the (assumed sole-connected)
    dongle. Linux/root only. Callers MUST have already confirmed
    can_burn_serial() — this function does not re-check.

    BENCH-VERIFY: rtl_eeprom's exact interactive confirmation prompt (it may
    ask for y/n on stdin) — this may need `-y`/non-interactive handling that
    can't be confirmed without the real tool and hardware.
    """
    if sys.platform != "linux":
        return
    subprocess.run(["sudo", "rtl_eeprom", "-s", new_serial],
                   capture_output=True, text=True, timeout=15)
