#!/usr/bin/env python3
"""
services/graywolf/install.py
----------------------------
Service-owned entry point for the GrayWolf installer.
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from services.graywolf.common.graywolf import run


def main():
    parser = argparse.ArgumentParser(
        description="Install GrayWolf APRS on Debian/Ubuntu/Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 services/graywolf/install.py                     # install latest\n"
            "  python3 services/graywolf/install.py --version 0.13.16  # pin a version\n"
        ),
    )
    parser.add_argument("--version", metavar="X.Y.Z",
                        help="Install a specific GrayWolf version instead of the latest.")
    args = parser.parse_args()

    run(pinned_version=args.version, repo_root=_REPO_ROOT)


if __name__ == "__main__":
    main()
