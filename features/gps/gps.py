#!/usr/bin/env python3
"""
gps.py  (library — CLI entry point is features/gps/install-gps.py)
------------------------------------------------------------
Set up GPS-disciplined time (gpsd + chrony) so the station keeps an accurate
clock with NO internet — needed for correct FT8/FT4/WSPR/SSTV decode windows and
trustworthy spot timestamps in OpenWebRX, and a sane clock suite-wide.

What it does (idempotent, safe to re-run):
  1. apt-installs gpsd, gpsd-clients, python3-gps, chrony
  2. points gpsd at the GPS in /etc/default/gpsd (START_DAEMON, DEVICES,
     GPSD_OPTIONS="-n", USBAUTO) — note the correct var is GPSD_OPTIONS, not OPTIONS
  3. adds a chrony SHM refclock (+ PPS if /dev/pps0 exists) so chrony actually
     STEERS the clock from GPS — the step most setups miss
  4. enables + restarts gpsd and chrony, then reports status

A position fix needs sky view; verify with `cgps -s` (fix) and
`chronyc sources -v` (a '#* GPS' line). The WittyPi/hwclock RTC bridges time
across reboots when GPS isn't locked — set that up separately.

The actual gpsd/chrony setup (steps 2-4) lives in common/gpsd_chrony.py, shared
with features/gps-L76X (Waveshare L76X GPIO/UART GPS HAT). Only one GPS device
can discipline the clock at a time — the two features are mutually exclusive;
running this after gps-L76X (or vice versa) will warn and refuse to retarget
gpsd unless you pass --force.

Usage:
  python3 features/gps/install-gps.py                 # autodetect device
  python3 features/gps/install-gps.py --device /dev/ttyACM0
  python3 features/gps/install-gps.py --force         # retarget gpsd from another GPS feature
  python3 features/gps/install-gps.py --check         # report status only

Requires: Linux (Debian/Raspberry Pi OS), apt/dpkg, sudo. apt step needs internet.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root (features/gps → features → repo)

from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run, has_internet, download_bytes
from common.gpsd_chrony import (install_packages, configure_gpsd, configure_chrony,
                          restart_services, verify, check_exclusive)

CANDIDATES   = ["/dev/ttyACM0", "/dev/ttyUSB0", "/dev/ttyAMA0", "/dev/serial0"]

# u-blox AssistNow Offline: multi-week predicted orbits (MGA-ANO) for fast cold
# fixes while disconnected. Needs a u-blox/Thingstream token (OASIS_UBLOX_TOKEN);
# endpoint + params overridable via env. Strictly opt-in (--assist-now).
ASSISTNOW_URL_DEFAULT    = "https://offline-live1.services.u-blox.com/GetOfflineData.ashx"
ASSISTNOW_PARAMS_DEFAULT = "gnss=gps,glo,gal;period=5;resolution=1"


def check_platform():
    if sys.platform != "linux":
        _fail("GPS time setup uses gpsd + chrony on Linux only.")
    if _run(["which", "apt"], check=False, capture_output=True).returncode != 0:
        _fail("apt not found — this script supports Debian / Raspberry Pi OS.")


def detect_device(preferred):
    if preferred:
        if not os.path.exists(preferred):
            _warn(f"{preferred} not present yet — writing it anyway (USBAUTO will pick it up).")
        return preferred
    for dev in CANDIDATES:
        if os.path.exists(dev):
            _ok(f"GPS device detected: {dev}")
            return dev
    _warn("No GPS unit present.")
    _info("No GPS device found at the usual paths ("
          + ", ".join(CANDIDATES) + "). gpsd + chrony will still be installed and "
          "pointed at /dev/ttyACM0 — gpsd's USBAUTO attaches the receiver "
          "automatically when you plug it in. Verify later with:\n"
          "       python3 features/gps/install-gps.py --check")
    return "/dev/ttyACM0"


def do_assistnow(device):
    """Fetch u-blox AssistNow Offline data (while online) and upload it to the
    receiver so a later cold start fixes in seconds instead of minutes. Best-
    effort: paced raw UBX write over the (USB CDC) device — no ACK handshake."""
    import os
    import tempfile

    token = os.environ.get("OASIS_UBLOX_TOKEN", "").strip()
    if not token:
        _warn("AssistNow needs a u-blox service token (free u-blox / Thingstream account).")
        _info("Then:  export OASIS_UBLOX_TOKEN=<token>   and re-run:  features/gps/install-gps.py --assist-now")
        return
    if not has_internet():
        _fail("AssistNow needs internet to fetch the offline assistance pack.")

    base   = os.environ.get("OASIS_ASSISTNOW_URL", ASSISTNOW_URL_DEFAULT)
    params = os.environ.get("OASIS_ASSISTNOW_PARAMS", ASSISTNOW_PARAMS_DEFAULT)
    url = f"{base}?token={token};{params}"
    _info("Fetching AssistNow Offline data (multi-week orbit predictions) ...")
    try:
        data = download_bytes(url)
    except Exception as exc:
        _fail(f"Could not fetch AssistNow data: {exc}")

    if not data or len(data) < 8 or data[:2] != b"\xb5\x62":   # UBX sync: B5 62
        _warn(f"Got {len(data) if data else 0} bytes, but not UBX-MGA (expected a "
              "B5 62 sync) — check the token/endpoint. Not uploading.")
        return
    _ok(f"Fetched {len(data)} bytes of MGA assistance data.")

    dev = device or detect_device(None)
    if not os.path.exists(dev):
        _fail(f"GPS device {dev} not present — cannot upload.")

    fd, tmp = tempfile.mkstemp(suffix=".ubx")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        _info(f"Stopping gpsd and uploading to {dev} (paced UBX write) ...")
        _run(["sudo", "systemctl", "stop", "gpsd", "gpsd.socket"], check=False)
        # Write in small paced chunks (run as root) so the receiver's input
        # buffer doesn't overflow. USB CDC ignores baud, so a raw write is fine.
        push = ("import sys,time\n"
                "d=open(sys.argv[1],'rb').read()\n"
                "f=open(sys.argv[2],'wb',buffering=0)\n"
                "for i in range(0,len(d),512):\n"
                "    f.write(d[i:i+512]); f.flush(); time.sleep(0.05)\n")
        rc = _run(["sudo", "python3", "-c", push, tmp, dev], check=False).returncode
        _run(["sudo", "systemctl", "start", "gpsd"], check=False)
        if rc == 0:
            _ok("AssistNow uploaded — cold first-fix should drop toward seconds (valid ~weeks).")
            _info("Best effect needs the receiver to know approx. time (RTC backup or a quick fix).")
        else:
            _warn("Upload may be incomplete — re-try when online, or rely on RTC + backup power.")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def run(device=None, check_only=False, assist_now=False, force=False):
    tag = ("  [--assist-now]" if assist_now else "") + ("  [--check]" if check_only else "")
    print("\n  OASIS — install-gps" + tag)
    _hr()
    check_platform()
    if check_only:
        verify()
        print()
        return False
    if assist_now:
        # Standalone refresh: assumes gpsd is already set up (run plain
        # features/gps/install-gps.py first). Re-run this whenever you're online to refresh.
        _step(1, "AssistNow Offline — fetch + upload to the u-blox")
        do_assistnow(device)
        print()
        return False

    _info("GPS-disciplined time (gpsd + chrony) for offline decode timing.")
    print()
    _step(1, "Installing gpsd + chrony")
    install_packages()
    dev = detect_device(device)
    _step(2, f"Pointing gpsd at the GPS ({dev})")
    if not check_exclusive(dev, force=force):
        _fail("Refusing to retarget gpsd — pass --force to override (see warning above).")
    configure_gpsd(dev)
    _step(3, "Adding the chrony GPS refclock")
    configure_chrony()
    _step(4, "Enabling + restarting services")
    restart_services()
    _step(5, "Verifying")
    gpsd_ok = verify()

    _hr()
    print("\n  GPS time setup complete.")
    _info("Give it sky view and a minute, then: cgps -s  /  chronyc tracking")
    if not gpsd_ok:
        _warn("gpsd did not come up active after configuration — a reboot is "
              "recommended to bring the GPS/serial device online.")
    print()
    return not gpsd_ok
