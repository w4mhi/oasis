#!/usr/bin/env python3
"""
services/openwebrx/install.py
------------------------------
Service-owned entry point for the OpenWebRX+ installer.

Installs OpenWebRX+ (browser-based, receive-only SDR receiver/decoder) from its
upstream apt repository, and sets it OFF by default (it grabs the RTL-SDR, which
the APRS SDR feed + GrayWolf also use — start it on demand from the dashboard).
All logic lives in services/openwebrx/common/openwebrx.py.

Usage:
  python3 services/openwebrx/install.py
  python3 services/openwebrx/install.py --check    # report install/status only

Requires: Linux (Debian/Raspberry Pi OS bookworm/trixie), apt/dpkg, sudo, internet.
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from services.openwebrx.common import openwebrx


def main():
    parser = argparse.ArgumentParser(
        description="Install OpenWebRX+ (SDR monitoring) on Debian/Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 services/openwebrx/install.py          # add repo + install, off by default\n"
                "  python3 services/openwebrx/install.py --check   # report status, change nothing\n"),
    )
    parser.add_argument("--check", action="store_true",
                        help="Report whether OpenWebRX is installed and its boot state; install nothing.")
    opts = parser.parse_args()
    openwebrx.run(check_only=opts.check)


if __name__ == "__main__":
    main()
