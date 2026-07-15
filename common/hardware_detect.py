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


def parse_lsusb(output):
    """Parse `lsusb` output into [{"bus", "device", "vendor_id", "product_id",
    "description"}, ...]. Matches lines like
    'Bus 001 Device 004: ID 10c4:ea60 Silicon Labs CP210x UART Bridge'."""
    devices = []
    for line in output.splitlines():
        m = re.match(r'Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s*(.*)', line)
        if m:
            devices.append({
                "bus": m.group(1),
                "device": m.group(2),
                "vendor_id": m.group(3),
                "product_id": m.group(4),
                "description": m.group(5).strip(),
            })
    return devices


# ── Known-signature classification ──────────────────────────────────────────
# A bare lsusb count ("7 USB devices") isn't actionable — the operator can't
# tell which entries are radio peripherals they care about vs. hub/controller
# noise. This is a small, explicit allowlist of the USB vendor:product IDs
# OASIS actually knows how to use, so the Hardware Snapshot can say "1
# RTL-SDR + 1 DigiRig, plus 4 other USB devices" instead of a raw total.
# Extend this table as new supported peripherals are added.
KNOWN_USB_SIGNATURES = {
    ("0bda", "2832"): {"role": "rtl_sdr", "label": "RTL-SDR (RTL2832U)"},
    ("0bda", "2838"): {"role": "rtl_sdr", "label": "RTL-SDR (RTL2838)"},
    ("10c4", "ea60"): {"role": "digirig", "label": "DigiRig (CP210x USB-serial)"},
    # CM108 is a generic USB audio+PTT chip used by many cheap ham-radio USB
    # interfaces ("USB DRA" variants included) — it is NOT this repo's
    # DRA-Pi-Zero, which is an I2C/I2S HAT with no USB signature at all (see
    # docs/graywolf-dra-pi.md). Surface it honestly as "a CM108 fob is
    # present", not as DRA-Pi confirmation.
    ("0d8c", "013c"): {"role": "cm108", "label": "CM108 USB audio/PTT interface"},
}


def digirig_candidates(serial_by_id):
    """Return every /dev/serial/by-id entry as a DigiRig candidate.

    This is the primary, robust DigiRig signal: it only needs a directory
    listing under /dev/serial/by-id, so it keeps working on images that don't
    ship the `lsusb` binary (usbutils isn't installed by default on some
    Raspberry Pi OS Lite images) — unlike classify_usb_devices(), which
    depends on `lsusb` actually running and returning output. Deliberately not
    filtered by CP210x label text — real by-id labels vary enough (CP2102 vs
    CP2104 vs CP2102N, vendor string casing, etc.) that a substring filter was
    dropping real DigiRig adapters to 0; any USB-serial adapter under
    /dev/serial/by-id on this suite's supported hardware is the DigiRig."""
    return list(serial_by_id)


def classify_usb_devices(devices):
    """Sort parse_lsusb() output into recognized ham-radio peripherals vs.
    everything else (hubs, controllers, unrelated dongles), using
    KNOWN_USB_SIGNATURES. Returns {"rtl_sdr": [...], "digirig": [...],
    "cm108": [...], "other": [...]} — "other" is what's left over, so the UI
    can show a plain count instead of implying every USB device matters."""
    out = {"rtl_sdr": [], "digirig": [], "cm108": [], "other": []}
    for dev in devices:
        sig = ((dev.get("vendor_id") or "").lower(), (dev.get("product_id") or "").lower())
        known = KNOWN_USB_SIGNATURES.get(sig)
        if known:
            out[known["role"]].append({**dev, "label": known["label"]})
        else:
            out["other"].append(dev)
    return out


def dra_pi_present(alsa_cards):
    """True if `aplay -l` shows the DRA-Pi-Zero's ALSA card. The DRA-Pi is a
    WM8731 HAT addressed over I2C (control) — not USB — so it can never show
    up in lsusb (see docs/graywolf-dra-pi.md); GrayWolf/Direwolf address it by
    the ALSA card name "audioinjectorpi" (it's electrically compatible with
    the AudioInjector Zero card), which is the only reliable "is it actually
    wired up and is the overlay loaded" signal. (Any OTHER ALSA card — onboard
    HDMI/analog audio, a USB sound fob — must NOT count as DRA-Pi present.)"""
    return any("audioinjector" in ((c.get("id") or "") + (c.get("description") or "")).lower()
               for c in alsa_cards)


_TTY_SERIAL_RE = re.compile(r'^(ttyUSB\d+|ttyACM\d+|ttyAMA\d+|ttyS\d+|serial0|serial1)$')
_TTY_USB_RE = re.compile(r'^(ttyUSB|ttyACM)')


def list_tty_serial_devices(directory="/dev"):
    """List serial-capable device nodes under /dev — both USB-serial adapters
    (ttyUSB*/ttyACM*) and the Pi's onboard GPIO UART (serial0/serial1/ttyAMA*/
    ttyS*), as [{"path", "label", "kind": "usb"|"onboard"}, ...] sorted by
    label. Onboard UART never appears under /dev/serial/by-id (no USB
    descriptor), so this is the only way to see it. Missing directory -> []."""
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if not _TTY_SERIAL_RE.match(name):
            continue
        kind = "usb" if _TTY_USB_RE.match(name) else "onboard"
        out.append({"path": os.path.join(directory, name), "label": name, "kind": kind})
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
        return {"rtl_sdr": [], "alsa": [], "serial": [], "usb": [], "serial_ports": []}
    return {
        "rtl_sdr": parse_rtl_test_devices(_run_text(["rtl_test", "-t"])),
        "alsa": parse_aplay_cards(_run_text(["aplay", "-l"])),
        "serial": list_serial_by_id(),
        "usb": parse_lsusb(_run_text(["lsusb"])),
        "serial_ports": list_tty_serial_devices(),
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
