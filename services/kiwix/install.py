#!/usr/bin/env python3
"""
services/kiwix/install.py
-------------------------
Service-owned entry point for the Kiwix installer.
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from services.kiwix.common.kiwix import DEFAULT_VERSION, DEFAULT_ZIM_DIR, run
from common.oasis_lib import _hr


def main():
    parser = argparse.ArgumentParser(
        description="Install kiwix-serve for offline Wikipedia/reference content.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 services/kiwix/install.py                        # install latest\n"
            "  python3 services/kiwix/install.py --version 3.8.2       # pin a version\n"
            "  python3 services/kiwix/install.py --zim-dir /mnt/ssd/zim  # custom ZIM dir\n"
        ),
    )
    parser.add_argument("--version", default=DEFAULT_VERSION, metavar="X.Y.Z",
                        help=f"kiwix-tools version to install (default: {DEFAULT_VERSION}).")
    parser.add_argument("--zim-dir", default=DEFAULT_ZIM_DIR, metavar="PATH",
                        help="Directory where ZIM files will be stored (default: ~/oasis-offline/zim).")
    args = parser.parse_args()

    print()
    print("  OASIS -- Kiwix Installer")
    _hr()

    run(version=args.version, zim_dir=args.zim_dir, repo_root=_REPO_ROOT)


if __name__ == "__main__":
    main()
