#!/usr/bin/env python3
"""
gps_l76x.py  (library — CLI entry point is features/gps-L76X/install-gps-l76x.py)
----------------------------------------------------------------------------
Enable the Raspberry Pi's hardware UART for a Waveshare **L76X GPS HAT**
(Quectel L76 GNSS, GPS+BDS+QZSS) wired onto the 40-pin header (TX/RX/5V/GND —
no USB), based on the vendor's own setup notes:
  https://github.com/waveshare/L76X-GPS-Module/tree/master/python
  https://www.waveshare.com/wiki/L76X_GPS_Module  ("Enable UART Port" FAQ)

This feature does two jobs:
  1. get the Pi's serial port talking to the module at 9600 baud with no
     login shell fighting over it, and confirm it's producing NMEA sentences
  2. wire that device into gpsd + chrony ('GPS-disciplined time' — NO
     internet, needed for correct FT8/FT4/WSPR/SSTV decode windows and
     trustworthy spot timestamps) — the same gpsd/chrony logic
     features/gps uses, shared via common/gpsd_chrony.py

Only one GPS device can discipline gpsd/chrony at a time — features/gps
(generic USB GPS) and features/gps-L76X are mutually exclusive alternatives,
not additive. Running this after features/gps (or vice versa) warns and
refuses to retarget gpsd unless you pass --force.

What it does (idempotent, safe to re-run):
  1. adds `enable_uart=1` to config.txt (hardware UART on)
  2. removes the serial console (`console=serial0,115200` / `ttyAMA0,115200`)
     from cmdline.txt and masks the serial-getty units — a login shell on the
     port otherwise steals every byte the GPS sends
  3. optionally adds a `pps-gpio` overlay (`--pps`) for the 1PPS wire some
     L76X HAT revisions expose (solder pin 4 on Q1 to GPIO4 — see the
     vendor FAQ)
  4. installs python3-serial (apt) and reads a few seconds of raw NMEA to
     confirm the module talks (fix status / lat / lon / sats)
  5. unless --no-gpsd: installs gpsd + chrony, points gpsd at the device,
     adds the chrony GPS (+ PPS, if wired) refclock, and restarts both

Steps 1-2 need a reboot to take effect (the console is still live on the
current boot) — this mirrors the exit-code-10 convention used by
features/dra-audio-interface/enable-dra-pi.py. gpsd/chrony (step 5) are
configured in the same pass since they don't need the device present yet.

Requires: Linux (Raspberry Pi OS), sudo. Steps 4-5's apt installs need
internet (or a local apt cache/mirror).
"""

import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run
from common.gpsd_chrony import (install_packages as install_gpsd_chrony_packages,
                          configure_gpsd, configure_chrony, restart_services,
                          verify as verify_gpsd_chrony, check_exclusive)

CONFIG_CANDIDATES  = ("/boot/firmware/config.txt", "/boot/config.txt")
CMDLINE_CANDIDATES = ("/boot/firmware/cmdline.txt", "/boot/cmdline.txt")
UART_PARAM   = "enable_uart=1"
PPS_OVERLAY  = "dtoverlay=pps-gpio,gpiopin=4"    # vendor FAQ: wire pin 4 (Q1) -> GPIO4
SERIAL_GETTY_UNITS = ["serial-getty@ttyS0.service", "serial-getty@ttyAMA0.service",
                      "serial-getty@serial0.service"]
DEVICE_CANDIDATES  = ["/dev/ttyS0", "/dev/serial0", "/dev/ttyAMA0"]
DEFAULT_BAUD = 9600
REBOOT_EXIT  = 10


# ── config.txt / cmdline.txt — pure transforms (unit-testable, no I/O) ──────────

def transform_config_txt(text):
    """Ensure `enable_uart=1` (and optionally the PPS overlay) is present.
    Returns (new_text, changed). Idempotent — a line already present, active
    (not commented), is left alone."""
    changed = False
    lines = text.splitlines()

    def has_active(needle):
        return any(ln.strip() == needle for ln in lines if not ln.strip().startswith("#"))

    if not has_active(UART_PARAM):
        lines.append(UART_PARAM)
        changed = True

    new_text = "\n".join(lines).rstrip("\n") + "\n"
    return new_text, changed


def transform_config_txt_pps(text, enable=True):
    """Same shape as transform_config_txt(), but for the opt-in PPS overlay."""
    changed = False
    lines = text.splitlines()
    has_it = any(ln.strip() == PPS_OVERLAY for ln in lines if not ln.strip().startswith("#"))
    if enable and not has_it:
        lines.append(PPS_OVERLAY)
        changed = True
    new_text = "\n".join(lines).rstrip("\n") + "\n"
    return new_text, changed


CONSOLE_TOKEN_RE = re.compile(r"console=(?:serial0|ttyAMA0|ttyS0),\d+\s*")


def transform_cmdline_txt(text):
    """Strip any `console=serial0,115200` / `console=ttyAMA0,...` / `console=ttyS0,...`
    token from the single-line cmdline.txt so the kernel stops handing a login
    shell to the port the GPS is wired to. Returns (new_text, changed)."""
    new_text, n = CONSOLE_TOKEN_RE.subn("", text)
    new_text = re.sub(r"[ \t]+", " ", new_text).strip() + "\n"
    return new_text, n > 0


# ── NMEA parsing — pure, no hardware ─────────────────────────────────────────────

def nmea_checksum_ok(sentence):
    """Validate the trailing `*hh` XOR checksum over everything between $ and *."""
    s = sentence.strip()
    m = re.match(r"^\$([^*]*)\*([0-9A-Fa-f]{2})$", s)
    if not m:
        return False
    body, expected = m.group(1), m.group(2)
    got = 0
    for ch in body:
        got ^= ord(ch)
    return f"{got:02X}" == expected.upper()


def _nmea_coord_to_decimal(value, hemi):
    """NMEA ddmm.mmmm (or dddmm.mmmm) -> signed decimal degrees."""
    if not value:
        return None
    dot = value.find(".")
    deg_len = dot - 2 if dot != -1 else len(value) - 2
    degrees = float(value[:deg_len])
    minutes = float(value[deg_len:])
    decimal = degrees + minutes / 60.0
    return -decimal if hemi in ("S", "W") else decimal


def parse_gprmc(sentence):
    """$--RMC,time,status(A/V),lat,N/S,lon,E/W,speed,track,date,... -> dict or None."""
    f = sentence.strip().split("*")[0].split(",")
    if len(f) < 10 or not f[0].endswith("RMC"):
        return None
    return {
        "time": f[1],
        "fix": f[2] == "A",
        "lat": _nmea_coord_to_decimal(f[3], f[4]),
        "lon": _nmea_coord_to_decimal(f[5], f[6]),
        "speed_knots": float(f[7]) if f[7] else None,
        "date": f[9],
    }


def parse_gpgga(sentence):
    """$--GGA,time,lat,N/S,lon,E/W,quality,numsats,hdop,alt,... -> dict or None."""
    f = sentence.strip().split("*")[0].split(",")
    if len(f) < 10 or not f[0].endswith("GGA"):
        return None
    return {
        "time": f[1],
        "lat": _nmea_coord_to_decimal(f[2], f[3]),
        "lon": _nmea_coord_to_decimal(f[4], f[5]),
        "fix_quality": int(f[6]) if f[6] else 0,
        "num_sats": int(f[7]) if f[7] else 0,
        "altitude_m": float(f[9]) if f[9] else None,
    }


# ── Platform / paths ─────────────────────────────────────────────────────────────

def check_platform():
    if sys.platform != "linux":
        _fail("The L76X GPS HAT is enabled on Raspberry Pi OS (Linux) only.")


def config_path():
    return next((p for p in CONFIG_CANDIDATES if os.path.exists(p)), None)


def cmdline_path():
    return next((p for p in CMDLINE_CANDIDATES if os.path.exists(p)), None)


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except (PermissionError, OSError):
        r = subprocess.run(["sudo", "cat", path], capture_output=True, text=True)
        if r.returncode != 0:
            _fail(f"Cannot read {path}: {r.stderr.strip()}")
        return r.stdout


def write_text(path, content):
    """Write via a temp file + `sudo tee`, backing up the original once."""
    bak = path + ".oasis-bak"
    if not os.path.exists(bak):
        if subprocess.run(["sudo", "cp", path, bak]).returncode != 0:
            _warn("Could not create a backup — aborting to be safe.")
            return False
        _ok(f"Backed up {os.path.basename(path)} -> {os.path.basename(bak)}")
    fd, tmp = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        return _run(["bash", "-c", f"sudo tee {path} < {tmp} >/dev/null"],
                    check=False).returncode == 0
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Steps ─────────────────────────────────────────────────────────────────────────

def enable_uart(cfg):
    text = read_text(cfg)
    new_text, changed = transform_config_txt(text)
    if not changed:
        _ok(f"{UART_PARAM} already in {cfg}.")
        return False
    if not write_text(cfg, new_text):
        _fail(f"Could not write {cfg}.")
    _ok(f"Added '{UART_PARAM}' to {cfg}.")
    return True


def add_pps_overlay(cfg):
    text = read_text(cfg)
    new_text, changed = transform_config_txt_pps(text, enable=True)
    if not changed:
        _ok(f"{PPS_OVERLAY} already in {cfg}.")
        return False
    if not write_text(cfg, new_text):
        _fail(f"Could not write {cfg}.")
    _ok(f"Added '{PPS_OVERLAY}' to {cfg} (needs pin 4 on Q1 wired to GPIO4).")
    return True


def disable_serial_console(cmdline):
    text = read_text(cmdline)
    new_text, changed = transform_cmdline_txt(text)
    if not changed:
        _ok(f"No serial console token in {cmdline} — already clear.")
        return False
    if not write_text(cmdline, new_text):
        _fail(f"Could not write {cmdline}.")
    _ok(f"Removed the serial console from {cmdline}.")
    return True


def mask_serial_getty():
    any_masked = False
    for unit in SERIAL_GETTY_UNITS:
        r = _run(["sudo", "systemctl", "is-enabled", unit], check=False,
                 capture_output=True, text=True)
        state = (getattr(r, "stdout", "") or "").strip()
        if state in ("", "not-found", "masked"):
            continue
        _run(["sudo", "systemctl", "disable", "--now", unit], check=False)
        _run(["sudo", "systemctl", "mask", unit], check=False)
        _ok(f"Masked {unit} (was {state}).")
        any_masked = True
    if not any_masked:
        _ok("No active serial-getty units to mask.")
    return any_masked


def detect_device(preferred):
    if preferred:
        if not os.path.exists(preferred):
            _warn(f"{preferred} not present yet — writing it anyway.")
        return preferred
    for dev in DEVICE_CANDIDATES:
        if os.path.exists(dev):
            _ok(f"Serial device detected: {dev}")
            return dev
    _warn("No serial device found at the usual paths ("
          + ", ".join(DEVICE_CANDIDATES) + ") — a reboot is likely needed "
          "after enabling the UART. Defaulting to /dev/ttyS0.")
    return "/dev/ttyS0"


def _pyserial_importable():
    return subprocess.run([sys.executable, "-c", "import serial"],
                          capture_output=True).returncode == 0


def install_pyserial():
    if _pyserial_importable():
        _ok("python3-serial already present.")
        return
    _run(["sudo", "apt", "update", "-qq"], check=False)
    if _run(["sudo", "apt", "install", "-y", "python3-serial"],
           check=False).returncode != 0 or not _pyserial_importable():
        _warn("Could not install python3-serial (needs internet / apt cache). "
              "NMEA verification will be skipped; the rest of the setup still applies.")
        return
    _ok("Installed python3-serial.")


def read_nmea_lines(device, baud=DEFAULT_BAUD, timeout=5.0):
    """Read raw NMEA lines off `device` for up to `timeout` seconds.
    Returns a list of decoded lines (possibly empty). Needs pyserial;
    returns [] (with a warning) if it isn't importable."""
    try:
        import serial
    except ImportError:
        _warn("pyserial not available — install python3-serial to verify NMEA output.")
        return []
    lines = []
    try:
        with serial.Serial(device, baudrate=baud, timeout=1) as ser:
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = ser.readline()
                if raw:
                    try:
                        lines.append(raw.decode("ascii", errors="replace").strip())
                    except Exception:
                        pass
    except Exception as exc:
        _warn(f"Could not open {device} at {baud} baud: {exc}")
    return lines


def verify(device, baud=DEFAULT_BAUD, timeout=5.0):
    if not os.path.exists(device):
        _warn(f"{device} does not exist yet — reboot, then re-run with --check.")
        return
    if not _pyserial_importable():
        _warn("python3-serial not installed — run this script once without --check "
              "first, or `sudo apt install python3-serial`.")
        return
    _info(f"Listening on {device} at {baud} baud for {timeout:.0f}s ...")
    lines = read_nmea_lines(device, baud, timeout)
    if not lines:
        _warn("No data received. Check wiring (TX->RxD, RX->TxD, 5V, GND), that "
              "the console is disabled (needs a reboot after enabling), and that "
              "no other process (gpsd) already has the port open.")
        return
    nmea = [ln for ln in lines if ln.startswith("$")]
    _ok(f"Received {len(lines)} line(s), {len(nmea)} NMEA sentence(s).")
    rmc = next((parse_gprmc(ln) for ln in reversed(nmea) if "RMC" in ln[:6]), None)
    gga = next((parse_gpgga(ln) for ln in reversed(nmea) if "GGA" in ln[:6]), None)
    if gga:
        _info(f"Fix quality={gga['fix_quality']}  satellites={gga['num_sats']}  "
              f"altitude={gga['altitude_m']}m")
    if rmc:
        fix = "A (active)" if rmc["fix"] else "V (void — no fix yet)"
        _info(f"Status={fix}  lat={rmc['lat']}  lon={rmc['lon']}  time={rmc['time']}")
    if not gga and not rmc:
        _warn("Got NMEA traffic but no RMC/GGA sentence parsed yet — give it a "
              "clear sky view and try again (cold fix can take a couple of minutes).")


def run(device=None, baud=DEFAULT_BAUD, pps=False, keep_console=False,
       no_gpsd=False, force=False, check_only=False):
    tag = "  [--check]" if check_only else ""
    print("\n  OASIS — install-gps-l76x  (Waveshare L76X GPS HAT)" + tag)
    _hr()
    check_platform()
    cfg = config_path()
    cmdline = cmdline_path()
    if not cfg or not cmdline:
        _fail("config.txt / cmdline.txt not found — is this Raspberry Pi OS?")

    if check_only:
        dev = detect_device(device)
        verify(dev, baud)
        if not no_gpsd:
            verify_gpsd_chrony()
        print()
        return 0

    _info("Enables the Pi's hardware UART for a GPIO-wired L76X GPS HAT "
          "(TX/RX/5V/GND — no USB), confirms it talks NMEA, and wires it "
          "into gpsd + chrony for GPS-disciplined time.")
    print()

    n = 0
    n += 1
    _step(n, "Enabling the hardware UART")
    changed = enable_uart(cfg)
    if not keep_console:
        changed = disable_serial_console(cmdline) or changed
        changed = mask_serial_getty() or changed
    else:
        _info("--keep-console: leaving the serial login shell enabled "
              "(it will contend with the GPS for the port).")

    if pps:
        n += 1
        _step(n, "Adding the 1PPS overlay")
        changed = add_pps_overlay(cfg) or changed

    n += 1
    _step(n, "Installing python3-serial")
    install_pyserial()

    dev = detect_device(device)

    if no_gpsd:
        _info("--no-gpsd: leaving gpsd/chrony untouched.")
    else:
        n += 1
        _step(n, f"Pointing gpsd + chrony at the GPS ({dev})")
        if not check_exclusive(dev, force=force):
            _fail("Refusing to retarget gpsd — pass --force to override (see warning above).")
        install_gpsd_chrony_packages()
        configure_gpsd(dev)
        configure_chrony()
        restart_services()

    if changed:
        _hr()
        print("\n  UART configured — REBOOT required before the GPS talks NMEA.")
        _info("After rebooting:  python3 features/gps-L76X/install-gps-l76x.py --check")
        print()
        return REBOOT_EXIT

    n += 1
    _step(n, "Verifying NMEA output")
    verify(dev, baud)
    if not no_gpsd:
        verify_gpsd_chrony()
    _hr()
    print("\n  L76X GPS HAT configured.")
    if no_gpsd:
        _info(f"Wire up gpsd/chrony next:  python3 features/gps-L76X/install-gps-l76x.py --device {dev}  (drop --no-gpsd)")
    print()
    return 0

