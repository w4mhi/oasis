#!/usr/bin/env python3
"""
features/rtc-hat/enable-rtc.py
------------------------------
Thin CLI for the UUGear **Witty Pi 3** (Rev1/Rev2) hardware clock — a DS3231SN
@ 0x68 on the GPIO ARM I²C bus (i2c-1), so it needs dtparam=i2c_arm=on alongside
the i2c-rtc overlay. All logic lives in common/rtc.py; the board preset is
`wittypi`.

The `--board` flag is kept so the historical
`enable-rtc.py --board bigtreetech-7in` invocation still works, but that board
now has its own feature dir: features/rtc-raspad/enable-rtc.py.

Usage:
  python3 features/rtc-hat/enable-rtc.py           # Witty Pi 3 (DS3231)
  python3 features/rtc-hat/enable-rtc.py --check    # report status; change nothing

Requires: Linux (Raspberry Pi OS), sudo. REQUIRES A REBOOT.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from common import rtc  # noqa: E402

BOARD = "wittypi"


def removal_record(repo_root=None):
    """Teardown record for the `rtc` feature — see common/rtc.removal_record()."""
    return rtc.removal_record(repo_root, BOARD)


def main():
    ap = argparse.ArgumentParser(
        description="Configure the Witty Pi 3 (DS3231) hardware RTC on Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/rtc-hat/enable-rtc.py           # Witty Pi 3 (DS3231)\n"
                "  python3 features/rtc-hat/enable-rtc.py --check   # report status, change nothing\n"),
    )
    ap.add_argument("--board", choices=sorted(rtc.BOARDS), default=BOARD,
                    help=f"RTC board preset (default: {BOARD}).")
    ap.add_argument("--check", action="store_true",
                    help="Report RTC/overlay status; change nothing.")
    args = ap.parse_args()
    rtc.run(check_only=args.check, board_id=args.board)


if __name__ == "__main__":
    main()
