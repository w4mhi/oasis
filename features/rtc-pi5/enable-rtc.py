#!/usr/bin/env python3
"""
features/rtc-pi5/enable-rtc.py
------------------------------
Thin CLI for the **Raspberry Pi 5's built-in RTC** — the one in the SoC, with
its own two-pin battery header (J5). All logic lives in common/rtc.py; the board
preset is `pi5`.

Unlike the add-on boards this is NOT an i2c chip: there is no overlay, no bus,
and nothing ever appears on `i2cdetect`. The kernel offers it as rtc0 with no
help from us. What the Pi 5 does need is a decision this program cannot make for
you.

THE BATTERY DECISION
--------------------
The Pi 5 can trickle-charge its backup cell, and does not by default. Charging a
rechargeable ML2032/LIR2032 is correct. Applying the same voltage to a primary
CR2032 can vent or rupture it.

Nothing on the board can tell them apart — a coin cell has two contacts, no ID
pin, no thermistor. The one hint available (a LIR2032 reads ~3.6 V where a
CR2032 never does) cannot resolve the dangerous direction, because a
rechargeable ML2032 sits at ~3.0 V and reads exactly like a CR2032. So the
chemistry is ASKED, never inferred, and the default is off.

Leaving charging off is a mild choice, not a broken one: a CR2032 holds the RTC
for years without ever being charged.

Usage:
  python3 features/rtc-pi5/enable-rtc.py                 # RTC on, charging OFF
  python3 features/rtc-pi5/enable-rtc.py --rechargeable  # ML2032/LIR2032 fitted
  python3 features/rtc-pi5/enable-rtc.py --check         # report; change nothing

Requires: Linux (Raspberry Pi OS) on a Pi 5, sudo. REQUIRES A REBOOT.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from common import rtc  # noqa: E402

BOARD = "pi5"


def removal_record(repo_root=None):
    """Teardown record for the `rtc-pi5` feature — see common/rtc.removal_record().

    With charging off the feature owns no config.txt line at all, so teardown is
    a no-op on the block and simply restores hwclock-set. With charging on it
    owns exactly the one dtparam it added."""
    return rtc.removal_record(repo_root, BOARD)


def main():
    ap = argparse.ArgumentParser(
        description="Configure the Raspberry Pi 5's built-in hardware RTC.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/rtc-pi5/enable-rtc.py                 # charging OFF (safe default)\n"
                "  python3 features/rtc-pi5/enable-rtc.py --rechargeable  # ML2032/LIR2032 fitted\n"
                "  python3 features/rtc-pi5/enable-rtc.py --check         # report status, change nothing\n"
                "\n"
                "--rechargeable enables trickle charging. Use it ONLY if the cell\n"
                "on the J5 header is a rechargeable ML2032 or LIR2032. Charging a\n"
                "primary CR2032 can vent or rupture it, and nothing on the board\n"
                "can tell the two apart.\n"),
    )
    ap.add_argument("--check", action="store_true",
                    help="Report RTC/battery status; change nothing.")
    ap.add_argument("--rechargeable", action="store_true",
                    help="The fitted cell is a rechargeable ML2032/LIR2032 — enable "
                         "trickle charging. NEVER pass this for a primary CR2032.")
    ap.add_argument("--charge-uv", type=int, default=rtc.PI5_CHARGE_UV,
                    help=f"Trickle-charge voltage in microvolts (default {rtc.PI5_CHARGE_UV}, "
                         "i.e. 3.0 V). Only used with --rechargeable.")
    args = ap.parse_args()
    rtc.run(check_only=args.check, board_id=BOARD,
            charge_uv=args.charge_uv if args.rechargeable else None)


if __name__ == "__main__":
    main()
