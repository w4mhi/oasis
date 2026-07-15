#!/usr/bin/env python3
"""
install-gps-l76x.py
--------------------
Thin CLI wrapper around gps_l76x.py (same directory).

Enables the Raspberry Pi's hardware UART for a Waveshare L76X GPS HAT
(Quectel L76 GNSS — GPS/BDS/QZSS, wired onto the 40-pin header: TX/RX/5V/GND,
no USB), per the vendor's setup notes:
  https://github.com/waveshare/L76X-GPS-Module/tree/master/python
  https://www.waveshare.com/wiki/L76X_GPS_Module

Adds `enable_uart=1`, removes the serial console so it stops stealing bytes
from the GPS, installs python3-serial, verifies NMEA output, and (unless
--no-gpsd) wires the device into gpsd + chrony for GPS-disciplined time. All
logic lives in gps_l76x.py (same directory); the gpsd/chrony parts are shared
with features/gps via common/gpsd_chrony.py.

Only one GPS device can discipline gpsd/chrony at a time — this feature and
features/gps (generic USB GPS) are mutually exclusive alternatives, not
additive. Running one after the other warns and refuses to retarget gpsd
unless you pass --force.

Usage:
  python3 features/gps-L76X/install-gps-l76x.py                # enable UART, verify, wire up gpsd/chrony
  python3 features/gps-L76X/install-gps-l76x.py --pps          # also add 1PPS overlay (GPIO4)
  python3 features/gps-L76X/install-gps-l76x.py --device /dev/ttyAMA0
  python3 features/gps-L76X/install-gps-l76x.py --no-gpsd      # UART + NMEA verify only, skip gpsd/chrony
  python3 features/gps-L76X/install-gps-l76x.py --force        # retarget gpsd from features/gps
  python3 features/gps-L76X/install-gps-l76x.py --check        # report status only
  python3 features/gps-L76X/install-gps-l76x.py --keep-console # don't disable the serial login shell

Exit codes: 0 = done · 10 = done, reboot required · 1 = error.
Requires: Linux (Raspberry Pi OS), sudo. The python3-serial and gpsd/chrony
apt installs need internet (or a local apt cache/mirror).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gps_l76x


def main():
    ap = argparse.ArgumentParser(
        description="Enable the Pi's hardware UART for a Waveshare L76X GPS HAT.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/gps-L76X/install-gps-l76x.py                 # enable UART, verify, wire gpsd/chrony\n"
                "  python3 features/gps-L76X/install-gps-l76x.py --pps           # + 1PPS overlay (GPIO4)\n"
                "  python3 features/gps-L76X/install-gps-l76x.py --no-gpsd       # UART + NMEA verify only\n"
                "  python3 features/gps-L76X/install-gps-l76x.py --force         # retarget gpsd from features/gps\n"
                "  python3 features/gps-L76X/install-gps-l76x.py --check         # report status, change nothing\n"),
    )
    ap.add_argument("--device", help="Serial device (default: autodetect ttyS0/serial0/ttyAMA0).")
    ap.add_argument("--baud", type=int, default=gps_l76x.DEFAULT_BAUD,
                    help=f"Baud rate for NMEA verification (default: {gps_l76x.DEFAULT_BAUD}).")
    ap.add_argument("--pps", action="store_true",
                    help="Also add the pps-gpio overlay (GPIO4) for HAT revisions with a 1PPS wire.")
    ap.add_argument("--keep-console", action="store_true",
                    help="Don't disable the serial login shell (it will contend with the GPS).")
    ap.add_argument("--no-gpsd", action="store_true",
                    help="Only enable the UART + verify NMEA; skip gpsd/chrony configuration.")
    ap.add_argument("--force", action="store_true",
                    help="Retarget gpsd/chrony even if features/gps already configured a "
                         "different device (they are mutually exclusive).")
    ap.add_argument("--check", action="store_true",
                    help="Report UART/NMEA/gpsd status; install/configure nothing.")
    args = ap.parse_args()
    sys.exit(gps_l76x.run(device=args.device, baud=args.baud, pps=args.pps,
                          keep_console=args.keep_console, no_gpsd=args.no_gpsd,
                          force=args.force, check_only=args.check))


if __name__ == "__main__":
    main()
