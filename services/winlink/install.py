#!/usr/bin/env python3
"""
services/winlink/install.py
---------------------------
Service-owned entry point for the Winlink/Pat installer.
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from services.winlink.common.winlink import DEFAULT_PORT, run
from common.oasis_lib import _hr


def main():
    parser = argparse.ArgumentParser(
        description="Install the Pat Winlink client + web UI on Raspberry Pi OS / Debian.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 services/winlink/install.py                      # bundled .deb if present, else download\n"
            "  python3 services/winlink/install.py --callsign W4MHI\n"
            "  python3 services/winlink/install.py --version 1.0.0      # pin a version (online)\n"
            "  python3 services/winlink/install.py --no-service         # install + config only\n"
        ),
    )
    parser.add_argument("--version", metavar="X.Y.Z",
                        help="Install a specific Pat version (always resolved from GitHub).")
    parser.add_argument("--callsign", default=None, metavar="CALL",
                        help="Winlink callsign for the config + modem MYCALL (default: from station.json, else W4MHI).")
    parser.add_argument("--locator", default=None, metavar="GRID",
                        help="Maidenhead grid square (e.g. FM18) — optional.")
    parser.add_argument("--password", default=None, metavar="PW",
                        help="Winlink password. Omit to be prompted; see --no-password.")
    parser.add_argument("--no-password", action="store_true",
                        help="Don't prompt for / set a Winlink password now.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, metavar="N",
                        help=f"Port for the Pat web UI (default: {DEFAULT_PORT}).")
    parser.add_argument("--no-service", action="store_true",
                        help="Install Pat and write config, but don't create the systemd service.")
    parser.add_argument("--no-modem", action="store_true",
                        help="Skip the Direwolf RF modem (telnet-only Winlink).")
    parser.add_argument("--ptt-gpio", type=int, default=None, metavar="N",
                        help="Override the RF modem's sysfs PTT GPIO number.")
    parser.add_argument("--modem-interface", choices=["dra", "digirig"], default="dra",
                        help="RF interface the pat-direwolf service points at.")
    parser.add_argument("--modem-only", action="store_true",
                        help="Skip the Pat install/config; only (re)write the modem configs and re-point the service.")
    parser.add_argument("--modem-ptt-serial", default=None, metavar="PATH",
                        help="Override the DigiRig PTT serial by-id path.")
    parser.add_argument("--modem-adevice", default=None, metavar="DEV",
                        help="Override the ALSA device for the active RF interface.")
    parser.add_argument("--modem-callsign", default=None, metavar="CALL",
                        help="Direwolf MYCALL for the RF modem (default: --callsign).")
    args = parser.parse_args()

    callsign = args.callsign or run.__globals__["station_callsign"](_REPO_ROOT) or "W4MHI"

    print()
    print("  OASIS -- Winlink Installer")
    _hr()

    run(
        pinned_version=args.version,
        callsign=callsign,
        locator=args.locator,
        password=args.password,
        no_password=args.no_password,
        port=args.port,
        no_service=args.no_service,
        repo_root=_REPO_ROOT,
        no_modem=args.no_modem,
        ptt_gpio=args.ptt_gpio,
        modem_adevice=args.modem_adevice,
        modem_callsign=args.modem_callsign,
        modem_interface=args.modem_interface,
        modem_only=args.modem_only,
        modem_ptt_serial=args.modem_ptt_serial,
    )


if __name__ == "__main__":
    main()
