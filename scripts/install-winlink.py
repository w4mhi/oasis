#!/usr/bin/env python3
"""
install-winlink.py
------------------
Install the Winlink client **Pat** on Linux (Debian/Ubuntu/Raspberry Pi OS) and
expose its browser UI as an OASIS service. Pat is a cross-platform Winlink client
with a built-in web interface — compose, read, and send Winlink (radio email)
from a browser.

What this does (same workflow as the other install-* scripts):
  1. Detects architecture and picks the right Pat .deb
  2. Installs Pat — always checks GitHub for the latest release first (so a
     re-run updates, like install-graywolf), installing the bundled .deb from
     offline-packages/pat/ when it matches; if there's no internet it falls back
     to the bundle (offline), and only downloads when the bundle is stale/absent
  3. Writes a starter config (~/.config/pat/config.json) — callsign, Winlink
     password (optional prompt), and the web UI bound to the LAN on port 8082
  4. Creates + enables a systemd service that runs `pat http`

Phase 1 = Telnet (internet gateway): works as soon as your Winlink password is
set. RF transports (packet via GrayWolf's KISS TNC) are Phase 2 — see
docs/plan-winlink.md.

Pat runs on port 8082 (GrayWolf already owns 8080). After install, open
http://<pi-ip>:8082 to compose/send.

Usage:
  python3 scripts/install-winlink.py
  python3 scripts/install-winlink.py --callsign W4MHI
  python3 scripts/install-winlink.py --version 1.0.0      # pin a version (online)
  python3 scripts/install-winlink.py --no-password        # skip the password prompt
  python3 scripts/install-winlink.py --no-service         # install + config only
  python3 scripts/install-winlink.py --port 8082

Requires: Linux, apt, sudo. Internet used to check for the latest release (a
re-run updates); falls back to a bundled .deb in offline-packages/pat/ when
offline.
Security: Pat serves plain HTTP and config.json holds your Winlink password
          (mode 600). Keep it on your trusted LAN, not the public internet.
Project:  https://getpat.io  ·  https://github.com/la5nta/pat
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import winlink as W

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(
        description="Install the Pat Winlink client + web UI on Raspberry Pi OS / Debian.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/install-winlink.py                      # bundled .deb if present, else download\n"
            "  python3 scripts/install-winlink.py --callsign W4MHI\n"
            "  python3 scripts/install-winlink.py --version 1.0.0      # pin a version (online)\n"
            "  python3 scripts/install-winlink.py --no-service         # install + config only\n"
        ),
    )
    parser.add_argument("--version", metavar="X.Y.Z",
                        help="Install a specific Pat version (always resolved from GitHub).")
    parser.add_argument("--callsign", default="W4MHI", metavar="CALL",
                        help="Winlink callsign for the starter config (default: W4MHI).")
    parser.add_argument("--locator", default=None, metavar="GRID",
                        help="Maidenhead grid square (e.g. FM18) — optional.")
    parser.add_argument("--password", default=None, metavar="PW",
                        help="Winlink password. Omit to be prompted; see --no-password.")
    parser.add_argument("--no-password", action="store_true",
                        help="Don't prompt for / set a Winlink password now.")
    parser.add_argument("--port", type=int, default=W.DEFAULT_PORT, metavar="N",
                        help=f"Port for the Pat web UI (default: {W.DEFAULT_PORT}).")
    parser.add_argument("--no-service", action="store_true",
                        help="Install Pat and write config, but don't create the systemd service.")
    args = parser.parse_args()

    # Resolve the Winlink password: explicit flag, interactive prompt, or skip.
    if args.no_password:
        password = None
    elif args.password is not None:
        password = args.password
    elif sys.stdin.isatty():
        password = getpass.getpass(
            f"    Winlink password for {args.callsign} (blank to skip): ") or None
    else:
        password = None

    W.run(
        pinned_version=args.version,
        callsign=args.callsign,
        locator=args.locator,
        password=password,
        no_password=args.no_password,
        port=args.port,
        no_service=args.no_service,
        repo_root=REPO_ROOT,
    )


if __name__ == "__main__":
    main()
