#!/usr/bin/env python3
"""
install-openwebrx.py
--------------------
Thin CLI wrapper around scripts/common/openwebrx.py.

Installs OpenWebRX+ (browser-based, receive-only SDR receiver/decoder) from its
upstream apt repository, and sets it OFF by default (it grabs the RTL-SDR, which
the APRS SDR feed + GrayWolf also use — start it on demand from the dashboard).
All logic lives in common/openwebrx.py.

Usage:
  python3 scripts/install-openwebrx.py
  python3 scripts/install-openwebrx.py --check    # report install/status only

Requires: Linux (Debian/Raspberry Pi OS bookworm/trixie), apt/dpkg, sudo, internet.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import openwebrx


def main():
    args = argparse.ArgumentParser(
        description="Install OpenWebRX+ (SDR monitoring) on Debian/Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 scripts/install-openwebrx.py          # add repo + install, off by default\n"
                "  python3 scripts/install-openwebrx.py --check   # report status, change nothing\n"),
    )
    args.add_argument("--check", action="store_true",
                      help="Report whether OpenWebRX is installed and its boot state; install nothing.")
    opts = args.parse_args()
    openwebrx.run(check_only=opts.check)


if __name__ == "__main__":
    main()
