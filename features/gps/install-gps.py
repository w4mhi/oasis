#!/usr/bin/env python3
"""
install-gps.py
--------------
Thin CLI wrapper around gps.py (same directory).

Sets up GPS-disciplined time (gpsd + chrony) so the station keeps an accurate
clock with no internet — for correct FT8/WSPR/SSTV decode windows and timestamps
in OpenWebRX. Installs gpsd/chrony, points gpsd at the GPS (with GPSD_OPTIONS=-n),
and adds the chrony SHM refclock that actually steers the clock from GPS.
All logic lives in gps.py (same directory).

Usage:
  python3 features/gps/install-gps.py                 # autodetect device
  python3 features/gps/install-gps.py --device /dev/ttyACM0
  python3 features/gps/install-gps.py --check         # report status only
  python3 features/gps/install-gps.py --assist-now    # (u-blox) load AssistNow Offline for fast cold fix

Requires: Linux (Debian/Raspberry Pi OS), apt/dpkg, sudo. apt step needs internet.
--assist-now also needs internet + OASIS_UBLOX_TOKEN (free u-blox/Thingstream token).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gps


def main():
    ap = argparse.ArgumentParser(
        description="Set up GPS-disciplined time (gpsd + chrony) on Debian/Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/gps/install-gps.py                  # autodetect GPS device\n"
                "  python3 features/gps/install-gps.py --device /dev/ttyACM0\n"
                "  python3 features/gps/install-gps.py --check          # report status, change nothing\n"
                "  OASIS_UBLOX_TOKEN=xxx python3 features/gps/install-gps.py --assist-now  # u-blox fast-fix\n"),
    )
    ap.add_argument("--device", help="GPS serial device (default: autodetect ttyACM0/ttyUSB0/…).")
    ap.add_argument("--check", action="store_true",
                    help="Report gpsd/chrony status; install/configure nothing.")
    ap.add_argument("--assist-now", dest="assist_now", action="store_true",
                    help="(u-blox) Fetch AssistNow Offline data (needs internet + "
                         "OASIS_UBLOX_TOKEN) and upload it for a fast cold fix. Run setup first.")
    args = ap.parse_args()
    gps.run(device=args.device, check_only=args.check, assist_now=args.assist_now)


if __name__ == "__main__":
    main()
