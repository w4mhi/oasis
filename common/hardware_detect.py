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
