#!/usr/bin/env python3
"""install-draws-gps.py — enable the DRAWS overlay and discipline chrony from the
on-board GPS (/dev/ttySC0). Mirrors features/gps-L76X; reuses common/gpsd_chrony
for the service config and common/nmea for verification.

Two-phase, exit-10 reboot convention: first run writes config + gpsd/chrony and
exits 10 (device only appears after reboot); after reboot, re-run verifies NMEA
and exits 0.

--check answers the whole question in one command — device nodes, whether the
receiver is TALKING (valid NMEA vs. bytes that don't parse vs. silence), whether
it has a FIX (GGA quality + satellite count), and whether gpsd/chrony are
actually steering the clock. That matters because `/dev/ttySC0` existing proves
nothing: a dead antenna, a baud mismatch and a gpsd still pointed at another GPS
feature all look identical at the device-node level.

Usage:
  python3 features/draws-gps/install-draws-gps.py            # autodetect phase
  python3 features/draws-gps/install-draws-gps.py --force    # retarget gpsd from another GPS feature
  python3 features/draws-gps/install-draws-gps.py --check    # status only (NMEA + fix + gpsd/chrony)
  python3 features/draws-gps/install-draws-gps.py --baud 38400   # non-default NMEA rate
  python3 features/draws-gps/install-draws-gps.py --dry-run  # preview config.txt change

Exit codes: 0 = done · 10 = done, reboot required · 1 = error.
Requires: Linux (Raspberry Pi), sudo. apt step needs internet (like features/gps).
The NMEA read needs python3-serial when gpsd is not running (gpspipe is used
when it is)."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import draws
from common import overlays
from common import gpsd_chrony
from common import nmea
from common.oasis_lib import _section, _step, _ok, _info, _warn, _fail

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "draws_gps", os.path.join(os.path.dirname(os.path.abspath(__file__)), "draws_gps.py"))
draws_gps = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(draws_gps)


def build_parser():
    p = argparse.ArgumentParser(description="Enable DRAWS GPS-disciplined time.")
    p.add_argument("--device", default=draws_gps.GPS_DEVICE,
                   help="GPS serial device (default: /dev/ttySC0)")
    p.add_argument("--baud", type=int, default=draws_gps.DEFAULT_BAUD,
                   help=f"Baud rate for NMEA verification (default: {draws_gps.DEFAULT_BAUD}).")
    p.add_argument("--force", action="store_true",
                   help="retarget gpsd from another GPS feature (gps / gps-l76x)")
    p.add_argument("--check", action="store_true",
                   help="report status only: device nodes, NMEA/fix, gpsd + chrony")
    p.add_argument("--dry-run", action="store_true",
                   help="preview the config.txt change without writing")
    return p


def verify_gps(device, baud):
    """Answer the question the device node cannot: is the receiver TALKING, and
    does it have a FIX? Shared with features/gps-L76X via common/nmea.py, then
    the gpsd/chrony half via common/gpsd_chrony.verify(). Without this, a dead
    antenna, a baud mismatch and a gpsd pointed at a different GPS all look
    identical — `/dev/ttySC0` exists in every one of them."""
    nmea.verify(device, baud=baud, no_data_hint=draws_gps.NO_DATA_HINT,
                configured_device=gpsd_chrony.configured_device())
    gpsd_chrony.verify()


def main(argv=None):
    args = build_parser().parse_args(argv)
    _section("DRAWS GPS-disciplined time")

    if sys.platform != "linux":
        _fail("This installer requires Linux (Raspberry Pi).")
        return 1
    # Fill a GAP only. If the OS ships an overlay we leave it alone: Raspberry Pi
    # OS ships a working draws.dtbo on Pi 5 (verified, 6.18.39+rpt-rpi-2712), and
    # replacing it with our Pi-4-built copy would be a downgrade for nothing. If
    # the one that loads turns out NOT to bring the HAT up, the repair happens
    # further down, on evidence rather than on assumption.
    for _name, _changed, _why in draws.install_missing_overlays():
        if _changed:
            _ok("%s.dtbo: installed (the OS ships none)" % _name)
        elif _why == "os-provides":
            _info("%s.dtbo: using the OS copy" % _name)
        elif _why == "no-vendored-copy":
            _info("%s.dtbo: not shipped by the OS and none vendored" % _name)
        else:
            _warn("could not install %s.dtbo: %s" % (_name, _why))
    if not draws.overlay_available():
        _fail("draws.dtbo is not in /boot/firmware/overlays and OASIS has none "
              "vendored to install. See overlays/SOURCE.md for how to build one.")
        return 1

    if args.check:
        _info("overlay dtbo: present")
        _cfg = draws.config_path()
        _info("config.txt has dtoverlay=draws: %s"
              % (bool(_cfg) and draws.OVERLAY_LINE in open(_cfg).read()))
        _info("GPS device %s present: %s" % (args.device, draws.gps_device_present(args.device)))
        _info("PPS (/dev/pps0) present: %s" % draws.pps_present())
        verify_gps(args.device, args.baud)
        return 0

    if args.dry_run:
        cfg = draws.config_path()
        _, changed = draws.add_overlay_line(open(cfg).read()) if cfg else ("", False)
        _info("would add dtoverlay=draws: %s" % changed)
        return 0

    _step(1, "Enable the DRAWS overlay")
    _cfg = draws.config_path()
    if _cfg and draws.conflicting_overlay(open(_cfg).read()):
        _fail("This box already loads the DRA-Pi HAT (%s). The DRA-Pi and "
              "DRAWS are different boards for the same 40-pin header and the "
              "same I2S bus — installing both leaves the Pi with no working "
              "sound card.\n  Remove the dra-pi feature first (Setup, or "
              "scripts/remove-oasis.py), reboot, then re-run this."
              % draws.DRA_PI_OVERLAY)
        return 1
    overlay_changed = draws.ensure_overlay()
    _ok("dtoverlay=draws %s" % ("added" if overlay_changed else "already present"))

    _step(2, "Guard against clobbering another GPS feature")
    if not gpsd_chrony.check_exclusive(args.device, force=args.force):
        return 1  # check_exclusive already warned how to proceed (--force)

    _step(3, "Install gpsd + chrony and point them at the GPS")
    gpsd_chrony.install_packages()
    gpsd_chrony.configure_gpsd(args.device)
    gpsd_chrony.configure_chrony()
    gpsd_chrony.restart_services()

    device_present = draws.gps_device_present(args.device)
    code = draws_gps.decide_exit_code(overlay_changed, device_present)
    if code == 10:
        # The box has already booted with dtoverlay=draws and the UART still is
        # not there: the overlay that loads is the suspect, so swap in ours.
        if draws.overlay_fallback_needed(overlay_changed, device_present):
            repaired = [r for r in draws.replace_overlays() if r[1]]
            if repaired:
                for _name, _changed, _why in repaired:
                    _warn("%s.dtbo did not bring the HAT up — installed OASIS's "
                          "known-good overlay (%s; original kept as %s.dtbo%s)."
                          % (_name, _why, _name, overlays.BACKUP_SUFFIX))
                _info("Reboot once more and re-run; if it still does not "
                      "enumerate, the overlay is not the problem.")
                return code
            # Ours is already in place and it STILL did not enumerate, so the
            # overlay is not the fault. Say so instead of asking for another
            # reboot that will change nothing.
            _warn("The DRAWS overlay is already OASIS's and the UART still has "
                  "not enumerated, so this is not an overlay problem.")
            _info(draws_gps.NO_DATA_HINT)
        _warn("Reboot required: the GPS device appears only after the overlay loads.")
        _info("After rebooting:  python3 features/draws-gps/install-draws-gps.py --check")
        return code

    # The device is live, so prove it works rather than declaring success on the
    # existence of a node — exit 0 here means verified.
    _step(4, "Verifying NMEA output and GPS-disciplined time")
    verify_gps(args.device, args.baud)
    return code


if __name__ == "__main__":
    sys.exit(main())
