#!/usr/bin/env python3
"""
features/rtc-raspad/enable-rtc.py
---------------------------------
Thin CLI for the **BigTreeTech 7" touchscreen** hardware clock (the Raspad-style
DSI panel) — a PCF8563 @ 0x51 on the DSI ribbon's I²C bus (i2c-10 /
i2c_csi_dsi), NOT the GPIO header, which is why it never shows on
`i2cdetect -y 1`. All logic lives in common/rtc.py; the board preset is
`bigtreetech-7in`, which writes:

    dtoverlay=vc4-kms-dsi-7inch,dsi1        (the DSI panel — a PREREQUISITE)
    dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi   (the clock -> /dev/rtc0 at boot)

Only the i2c-rtc line is owned: it goes inside this feature's config.txt
BEGIN/END block, which is what an uninstall strips. The display overlay IS the
Raspad's screen, not an RTC artifact, so it is added when missing but always
written outside the block and never removed — whether it was already there (the
usual case: next to the stock vc4-kms-v3d line) or this installer added it.

Usage:
  python3 features/rtc-raspad/enable-rtc.py           # BigTreeTech 7" (PCF8563)
  python3 features/rtc-raspad/enable-rtc.py --check    # report status; change nothing

Requires: Linux (Raspberry Pi OS), sudo. REQUIRES A REBOOT.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from common import rtc  # noqa: E402

BOARD = "bigtreetech-7in"


def removal_record(repo_root=None):
    """Teardown record for the `rtc-raspad` feature — see
    common/rtc.removal_record(). Strips only this feature's config.txt block, so
    the DSI display overlay always survives the uninstall (deleting it would
    blank the screen)."""
    return rtc.removal_record(repo_root, BOARD)


def main():
    ap = argparse.ArgumentParser(
        description='Configure the BigTreeTech 7" (PCF8563) hardware RTC on Raspberry Pi OS.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                '  python3 features/rtc-raspad/enable-rtc.py           # BigTreeTech 7" (PCF8563)\n'
                "  python3 features/rtc-raspad/enable-rtc.py --check   # report status, change nothing\n"),
    )
    ap.add_argument("--check", action="store_true",
                    help="Report RTC/overlay status; change nothing.")
    args = ap.parse_args()
    rtc.run(check_only=args.check, board_id=BOARD)


if __name__ == "__main__":
    main()
