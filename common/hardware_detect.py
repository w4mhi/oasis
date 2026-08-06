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
import glob
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


def draws_present(alsa_cards):
    """True if `aplay -l` shows the DRAWS HAT's ALSA card. Like the DRA-Pi this
    is an I2C-controlled HAT (TLV320AIC3204), never visible in lsusb; the plain
    `dtoverlay=draws` registers a simple-card named exactly "draws", which is
    the reliable "HAT wired up and overlay loaded" signal. Matched on the card
    ID as a whole word-ish token so an unrelated card whose description merely
    mentions the word cannot trigger a false positive."""
    return any((c.get("id") or "").strip().lower() == "draws" for c in alsa_cards)


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


def rtl_sdr_usb_count():
    """Cheap RTL-SDR presence count via `lsusb` only — NO exclusive `rtl_test`
    probe. Used to decide whether a full scan()+declare is worth running on a
    poll (rtl_test is slow and needs exclusive dongle access; lsusb is a few ms
    and works even while a dongle is in use). Linux only; 0 elsewhere."""
    if sys.platform != "linux":
        return 0
    return len(classify_usb_devices(parse_lsusb(_run_text(["lsusb"])))["rtl_sdr"])


_USB_PORT_RE = re.compile(r'^\d+-[\d.]+$')   # e.g. '1-1.4' — a device, not an interface


def rtl_sdr_usb_ports():
    """Map each present RTL-SDR's serial -> its physical USB port path (e.g.
    '1-1.4'), read from sysfs. Lets the UI say WHICH dongle by port, not just a
    count — and it works even for a dongle a service is actively using (USB
    enumeration is independent of who holds the device open, unlike rtl_test).
    Linux only; {} elsewhere or if sysfs is unavailable.

    BENCH-VERIFY: sysfs layout is stable on Pi OS, but confirm the port-path
    format and that the 'serial' attribute is readable (it's normally 0444) on
    the target image."""
    out = {}
    root = "/sys/bus/usb/devices"
    if sys.platform != "linux" or not os.path.isdir(root):
        return out
    for name in os.listdir(root):
        if not _USB_PORT_RE.match(name):   # skip interfaces ('1-1.4:1.0') and roots ('usb1')
            continue
        dev = os.path.join(root, name)
        try:
            with open(os.path.join(dev, "idVendor")) as f:
                vid = f.read().strip().lower()
            with open(os.path.join(dev, "idProduct")) as f:
                pid = f.read().strip().lower()
        except OSError:
            continue
        sig = KNOWN_USB_SIGNATURES.get((vid, pid))
        if not sig or sig.get("role") != "rtl_sdr":
            continue
        try:
            with open(os.path.join(dev, "serial")) as f:
                serial = f.read().strip()
        except OSError:
            serial = ""
        if serial:
            out[serial] = name
    return out


# DigiRig PTT is a CP210x USB-serial bridge (its RTS line keys PTT). These
# by-id globs mirror services/winlink/common/winlink.py's — the by-id path is a
# stable per-chip identity that survives replug, and it's CP210x-specific so a
# u-blox/CH340/PL2303 GPS adapter can never be mistaken for the DigiRig.
_DIGIRIG_BYID_GLOB     = "/dev/serial/by-id/usb-Silicon_Labs_CP210*-if00-port0"
_DIGIRIG_BYID_GLOB_ANY = "/dev/serial/by-id/*CP210*-if00*"
_DIGIRIG_SERIAL_RE     = re.compile(r'_([0-9a-fA-F]{8,})-if\d')


def detect_digirig():
    """Detect a lone, unambiguous DigiRig by its CP210x PTT serial-by-id path.
    Returns {'ptt': <by-id path>, 'serial': <chip hex or ''>}, or None when none
    is present OR when 2+ CP210x adapters are found (ambiguous — fall back to the
    manual declare form). Linux only.

    Only the PTT identity is resolved here; the USB sound card (ADEVICE) is
    autodetected at apply time by winlink.radio_port_config, so a bare
    {kind:'digirig', ptt:…} declaration is enough to drive direwolf's config."""
    if sys.platform != "linux":
        return None
    cands = []
    for pattern in (_DIGIRIG_BYID_GLOB, _DIGIRIG_BYID_GLOB_ANY):
        cands = sorted(set(glob.glob(pattern)))
        if cands:
            break
    if len(cands) != 1:
        return None
    ptt = cands[0]
    m = _DIGIRIG_SERIAL_RE.search(ptt)
    return {"ptt": ptt, "serial": m.group(1) if m else ""}


def detect_dra_pi():
    """True if the DRA-Pi HAT is present — its `audioinjectorpi` ALSA card shows,
    i.e. the overlay is loaded. Self-contained (runs `aplay -l`) so the poll can
    call it like detect_digirig(). The DRA-Pi is fully deterministic (card
    present ⇒ fixed audioinjectorpi ALSA + GPIO-12 PTT), so a boolean is all
    auto-declare needs. Linux only."""
    if sys.platform != "linux":
        return False
    return dra_pi_present(parse_aplay_cards(_run_text(["aplay", "-l"])))


def detect_draws():
    """True if the DRAWS HAT is present — its `draws` ALSA card shows, i.e. the
    overlay is loaded. Deterministic like the DRA-Pi (card present ⇒ fixed ALSA
    name + fixed PTT GPIOs on both ports), so a boolean is all auto-declare
    needs; it then declares BOTH ports. Linux only."""
    if sys.platform != "linux":
        return False
    return draws_present(parse_aplay_cards(_run_text(["aplay", "-l"])))


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

    rtl_eeprom prints "Write new configuration to device [y/n]?" and blocks on
    a stdin read before committing the EEPROM write — there is no non-
    interactive flag in mainline rtl_eeprom. Invoked from the web route there
    is no TTY, so we feed "y\\n" on stdin to confirm; without it the read hits
    EOF (write silently skipped) or blocks until the timeout.

    BENCH-VERIFY: confirm on real hardware that a single "y" answers the prompt
    on this rtl_eeprom build and that the write actually commits (some builds
    ask a second time / require the dongle be re-plugged for the new serial to
    take effect)."""
    if sys.platform != "linux":
        return
    subprocess.run(["sudo", "rtl_eeprom", "-s", new_serial],
                   input="y\n", capture_output=True, text=True, timeout=15)
