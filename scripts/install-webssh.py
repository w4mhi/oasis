#!/usr/bin/env python3
"""
install-webssh.py
-----------------
Install a browser-based SSH/terminal on Linux (Debian/Ubuntu/Raspberry Pi OS),
exposing a login shell in a browser over WebSocket. This installs `ttyd`, which
wraps a login shell in a pseudo-terminal and streams it to the browser over a
WebSocket (xterm.js front-end) — no PuTTY or SSH client needed.

The page prompts for a username and hands off to `ssh user@localhost`, so users
authenticate with their normal Raspberry Pi credentials (PAM, via sshd). We use
ssh rather than `/bin/login` because login's vhangup() breaks ttyd's pty and the
password prompt never shows. Requires sshd running. Optionally add an extra HTTP
Basic Auth gate in front with --basic-auth.

ttyd is NOT in the Debian/Raspberry Pi OS stable repos, so this installs the
upstream prebuilt static binary (a single self-contained file per architecture)
to /usr/local/bin/ttyd — no apt, no libwebsockets/libuv dependencies.

What this does:
  1. Detects platform/architecture
  2. Installs the `ttyd` static binary to /usr/local/bin/ttyd
     (from offline-packages/webssh/ttyd.<arch> if bundled, else downloads from GitHub)
  3. Creates a systemd service on port 7681 (ttyd default port)
  4. Enables and starts the service

After install, open http://<pi-ip>:7681 and log in.

Usage:
  python3 scripts/install-webssh.py
  python3 scripts/install-webssh.py --port 7681
  python3 scripts/install-webssh.py --basic-auth          # prompt for extra gate
  python3 scripts/install-webssh.py --basic-auth admin:s3cret
  python3 scripts/install-webssh.py --bind 127.0.0.1      # localhost only

Requires: Linux, sudo. Internet optional if bundled binary is present.
Security: serves plain HTTP. Keep it on your trusted LAN/Wi-Fi side, not on an
          open RF link or the public internet without a TLS reverse proxy.
Project:  https://github.com/tsl0922/ttyd
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import webssh as W

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(
        description="Install a browser SSH/terminal (ttyd) on Raspberry Pi OS / Debian.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/install-webssh.py                       # bundled binary if present, else download\n"
            "  python3 scripts/install-webssh.py --version 1.7.7       # pin a specific ttyd version\n"
            "  python3 scripts/install-webssh.py --dry-run             # preview helper + unit, change nothing\n"
            "  python3 scripts/install-webssh.py --verify              # check an existing install, change nothing\n"
            "  python3 scripts/install-webssh.py --port 8090           # use a different port\n"
            "  python3 scripts/install-webssh.py --bind 127.0.0.1      # localhost only\n"
            "  python3 scripts/install-webssh.py --basic-auth          # prompt for user:pass gate\n"
            "  python3 scripts/install-webssh.py --basic-auth admin:s3cret\n"
        ),
    )
    parser.add_argument(
        "--version", default=None, metavar="X.Y.Z",
        help="ttyd version to install (default: from manifest or 1.7.7). "
             "Bundled binary is used only if its version matches; otherwise downloads from GitHub.",
    )
    parser.add_argument(
        "--port", type=int, default=W.DEFAULT_PORT, metavar="N",
        help=f"Port to serve the web terminal on (default: {W.DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--bind", default=None, metavar="ADDR",
        help="Interface to bind (e.g. 127.0.0.1 for localhost-only). "
             "Default: all interfaces, reachable on your LAN.",
    )
    parser.add_argument(
        "--basic-auth", nargs="?", const="", default=None, metavar="USER:PASS",
        help="Add an HTTP Basic Auth gate in front of the login prompt. "
             "Pass USER:PASS, or use the flag alone to be prompted.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show exactly what would be written (login helper + systemd unit) "
             "and the commands that would run. Makes no changes.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Only verify an existing install (helper, live unit, services, "
             "port) against what this script would generate. Makes no changes.",
    )
    args = parser.parse_args()

    W.run(
        version=args.version,
        port=args.port,
        bind=args.bind,
        basic_auth_arg=args.basic_auth,
        dry_run_mode=args.dry_run,
        verify_mode=args.verify,
        repo_root=REPO_ROOT,
    )


if __name__ == "__main__":
    main()
