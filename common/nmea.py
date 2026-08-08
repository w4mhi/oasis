"""
nmea.py — shared "is this GPS actually working?" layer.

The companion to common/gpsd_chrony.py: that module *configures* gpsd + chrony,
this one *answers whether the receiver is talking and has a fix*. Both are used
by every GPS feature:
  - features/gps-L76X   (Waveshare L76X GPIO/UART HAT — ttyS0/serial0)
  - features/draws-gps  (DRAWS on-board GPS — SC16IS752 ttySC0)

The distinction it exists to draw, because it is the one that costs hours on a
bench: a device node existing proves NOTHING. A GPS can enumerate, gpsd can be
active and chrony can list the refclock while the receiver sends no bytes at
all, sends garbage (baud mismatch), or sends perfectly good sentences reporting
no fix (no sky view). Those three need completely different fixes, so every
installer should be able to tell them apart in one command.

Pure summarizers (summarize_nmea / summarize_gpsd_json) hold the logic so it
unit-tests off-Pi; the thin serial/gpspipe wrappers are exercised on the Pi.
"""

import errno
import json
import os
import re
import subprocess
import sys
import time
from shutil import which

from common.oasis_lib import _ok, _info, _warn, _run, sudo_apt_cmd

DEFAULT_BAUD = 9600


# ── Sentence parsing — pure, no hardware ─────────────────────────────────────────

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


def summarize_nmea(lines):
    """Reduce raw serial lines to the four facts an operator needs:

      lines     — how many lines arrived at all (0 = nothing on the wire)
      sentences — how many were VALID NMEA (arrived > 0 but valid == 0 is the
                  classic baud/framing mismatch: bytes flow, none of them parse)
      talking   — the receiver is emitting well-formed NMEA
      has_fix   — GGA quality > 0, or RMC status A

    The last valid GGA/RMC wins, so a fix acquired part-way through the read is
    what gets reported. Pure — no I/O."""
    gga = rmc = None
    sentences = 0
    for raw in lines:
        line = (raw or "").strip()
        if not nmea_checksum_ok(line):
            continue
        sentences += 1
        parsed = parse_gpgga(line)
        if parsed:
            gga = parsed
            continue
        parsed = parse_gprmc(line)
        if parsed:
            rmc = parsed
    has_fix = bool((gga and gga["fix_quality"] > 0) or (rmc and rmc["fix"]))
    return {"lines": len(lines), "sentences": sentences, "talking": sentences > 0,
            "has_fix": has_fix, "gga": gga, "rmc": rmc}


def summarize_gpsd_json(text):
    """Reduce a gpspipe -w stream to {fix, saw_sky, sats_used}. A TPV with a lat
    is a fix; SKY without one means 'receiving, but no sky view yet' — the
    distinction between a broken chain and a bad antenna position. Pure; skips
    any non-JSON noise gpspipe emits."""
    fix = None
    saw_sky = False
    sats_used = 0
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        cls = obj.get("class")
        if cls == "SKY":
            saw_sky = True
            sats_used = sum(1 for s in obj.get("satellites", []) if s.get("used"))
        elif cls == "TPV" and obj.get("lat") is not None:
            fix = obj
    return {"fix": fix, "saw_sky": saw_sky, "sats_used": sats_used}


def device_mismatch(configured, target):
    """True if gpsd is configured for a *different* serial device than the one a
    feature targets — the trap that silently breaks a GPS HAT when a previous GPS
    feature (e.g. features/gps on /dev/ttyUSB0) already claimed gpsd. Pure /
    os.path-only so it's unit-testable. Symlinks are resolved so /dev/serial0 and
    /dev/ttyS0 count as the same device."""
    if not configured or not target:
        return False
    return os.path.realpath(configured) != os.path.realpath(target)


# ── I/O wrappers — exercised on the Pi ───────────────────────────────────────────

def pyserial_importable():
    return subprocess.run([sys.executable, "-c", "import serial"],
                          capture_output=True).returncode == 0


def install_pyserial():
    if pyserial_importable():
        _ok("python3-serial already present.")
        return
    _run(sudo_apt_cmd("apt", "update", "-qq"), check=False)
    if _run(sudo_apt_cmd("apt", "install", "-y", "python3-serial"),
            check=False).returncode != 0 or not pyserial_importable():
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
        if getattr(exc, "errno", None) == errno.EACCES or "Permission denied" in str(exc):
            _warn(f"Permission denied opening {device} — your user isn't in the "
                  "'dialout' group, so this check can't read the raw port. Add it "
                  'with:  sudo usermod -aG dialout "$USER"   then log out/in (or '
                  "re-run with sudo). This is a check-only limitation: gpsd runs as "
                  "its own user and is unaffected.")
        else:
            _warn(f"Could not open {device} at {baud} baud: {exc}")
    return lines


def gpsd_active():
    return _run(["systemctl", "is-active", "--quiet", "gpsd"],
                check=False).returncode == 0


def verify_via_gpsd(device, timeout=8.0):
    """Read NMEA *through* gpsd (gpspipe) rather than opening the raw serial
    device gpsd already holds. Returns True if it reported a result (a fix, a
    satellites-but-no-fix state, or a "gpsd running but no data" warning),
    False only if gpspipe isn't installed so the caller can fall back."""
    if which("gpspipe") is None:
        return False
    _info(f"gpsd is active — reading through gpsd (gpspipe) for up to {timeout:.0f}s ...")
    out = ""
    try:
        r = _run(["gpspipe", "-w", "-n", "60"], check=False,
                 capture_output=True, text=True, timeout=timeout)
        out = getattr(r, "stdout", "") or ""
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        out = partial.decode("ascii", "replace") if isinstance(partial, bytes) else partial

    s = summarize_gpsd_json(out)
    if s["fix"]:
        fix = s["fix"]
        _ok(f"gpsd has a fix — mode={fix.get('mode')}  lat={fix.get('lat')}  "
            f"lon={fix.get('lon')}  time={fix.get('time')}")
        return True
    if s["saw_sky"]:
        _info(f"gpsd sees satellites ({s['sats_used']} in use) but no position fix yet — "
              "give the antenna a clear sky view for a minute, then re-check.")
        return True
    _warn("gpsd is running but produced no TPV/SKY data — it's likely pointed at "
          "the wrong device or getting no NMEA. Check:  gpspipe -w -n 20   and   "
          "sudo journalctl -u gpsd -n 40")
    return True


def report_nmea(summary, device, baud):
    """Narrate a summarize_nmea() result. Split from verify() so the wording is
    identical across features and testable without a serial port."""
    if not summary["lines"]:
        return
    if not summary["sentences"]:
        _warn(f"Received {summary['lines']} line(s) on {device} but NOT ONE valid "
              f"NMEA sentence — bytes are flowing and none of them parse, which "
              f"almost always means the wrong baud rate (trying {baud}). Check the "
              "receiver's configured rate, then re-run with --baud.")
        return
    _ok(f"Received {summary['lines']} line(s), {summary['sentences']} valid NMEA sentence(s).")
    gga, rmc = summary["gga"], summary["rmc"]
    if gga:
        alt = f"{gga['altitude_m']}m" if gga["altitude_m"] is not None else "n/a"
        _info(f"Fix quality={gga['fix_quality']}  satellites={gga['num_sats']}  "
              f"altitude={alt}")
    if rmc:
        status = "A (active)" if rmc["fix"] else "V (void — no fix yet)"
        _info(f"Status={status}  lat={rmc['lat']}  lon={rmc['lon']}  time={rmc['time']}")
    if summary["has_fix"]:
        _ok("GPS has a position fix.")
    elif gga or rmc:
        # The single most common "it's broken" that isn't: a healthy receiver
        # with no sky. Say so plainly so nobody re-debugs the software.
        _warn("The receiver is talking but has NO FIX yet. This is an ANTENNA/SKY "
              "problem, not a software one — the serial path, gpsd and chrony are "
              "all fine. Give the antenna a clear view of the sky (a window is "
              "often not enough) and allow up to 15 minutes for a cold start.")
    else:
        _warn("Got NMEA traffic but no RMC/GGA sentence parsed yet — give it a "
              "clear sky view and try again (cold fix can take a couple of minutes).")


def verify(device, baud=DEFAULT_BAUD, timeout=5.0, no_data_hint=None,
           configured_device=None):
    """The one-command answer: is this GPS talking, and does it have a fix?

    Prefers gpsd (gpspipe) once gpsd owns the port — fighting it for the raw
    device typically fails with a permission/busy error and produces a
    misleading "no data" result. Falls back to a raw serial read otherwise.
    `no_data_hint` is the feature-specific wiring advice printed when nothing
    arrives; `configured_device` (when given) is checked against `device` to
    surface a gpsd pointed at a different GPS feature."""
    if not os.path.exists(device):
        _warn(f"{device} does not exist yet — reboot, then re-run with --check.")
        return
    if device_mismatch(configured_device, device):
        _warn(f"gpsd is configured for {configured_device}, NOT this device "
              f"({device}). gpsd is reading the wrong port, so cgps sees no data. "
              "Re-run the installer with --force to retarget gpsd/chrony at "
              f"{device}.")
    if gpsd_active():
        if verify_via_gpsd(device, max(timeout, 8.0)):
            return
        _info("gpspipe unavailable — falling back to a raw serial read "
              "(stop gpsd first if this reports the port busy).")
    if not pyserial_importable():
        _warn("python3-serial not installed — run this script once without --check "
              "first, or `sudo apt install python3-serial`.")
        return
    _info(f"Listening on {device} at {baud} baud for {timeout:.0f}s ...")
    lines = read_nmea_lines(device, baud, timeout)
    if not lines:
        _warn("No data received. " + (no_data_hint or
              "Check the wiring and that no other process (gpsd) already has the "
              "port open."))
        return
    report_nmea(summarize_nmea(lines), device, baud)
