#!/usr/bin/env python3
"""
install-graywolf.py
-------------------
Download and install GrayWolf APRS on Linux (Debian/Ubuntu/Raspberry Pi OS).
Fetches the latest release from GitHub, picks the right .deb for your
architecture, installs it with apt, and enables the systemd service.

GrayWolf runs on port 8080 and provides a browser-based APRS TNC,
iGate, digipeater, and live map. After install, open http://localhost:8080
to complete configuration (callsign, audio device, radio channel).

Also enables the GrayWolf APRS History API on port 8085 — it feeds the OASIS
APRS map and dashboard from graywolf's history DB. This delegates to
scripts/enable-graywolf-api.py (the API server + its systemd enabler in one).
Runs under the repo's .venv (Flask + psutil).

Usage:
  python3 scripts/install-graywolf.py
  python3 scripts/install-graywolf.py --version 0.13.16   # pin a version

Requires: Linux, apt, sudo, internet access.
Project:  https://github.com/chrissnell/graywolf
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import graywolf as G

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(
        description="Install GrayWolf APRS on Debian/Ubuntu/Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/install-graywolf.py                     # install latest\n"
            "  python3 scripts/install-graywolf.py --version 0.13.16  # pin a version\n"
        ),
    )
    parser.add_argument("--version", metavar="X.Y.Z",
                        help="Install a specific GrayWolf version instead of the latest.")
    args = parser.parse_args()

    G.run(pinned_version=args.version, repo_root=REPO_ROOT)


if __name__ == "__main__":
    main()
