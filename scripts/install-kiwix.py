#!/usr/bin/env python3
"""
install-kiwix.py
----------------
Download and install kiwix-serve on Linux. Kiwix provides offline access
to Wikipedia, OpenStreetMap, Project Gutenberg, and other reference content
through a local web server on port 8081.

What this does:
  1. Detects your architecture
  2. Downloads kiwix-tools (contains kiwix-serve) from download.kiwix.org
  3. Installs kiwix-serve to /usr/local/bin/
  4. Creates and enables a systemd service on port 8081

After install, run scripts/download-wikipedia.py to get content.

Usage:
  python3 scripts/install-kiwix.py
  python3 scripts/install-kiwix.py --version 3.8.2   # pin a version
  python3 scripts/install-kiwix.py --zim-dir /mnt/ssd/zim

Requires: Linux, sudo, internet access (~5 MB download).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import kiwix as K
from common.oasis_lib import _hr

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(
        description="Install kiwix-serve for offline Wikipedia/reference content.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/install-kiwix.py                        # install latest\n"
            "  python3 scripts/install-kiwix.py --version 3.8.2       # pin a version\n"
            "  python3 scripts/install-kiwix.py --zim-dir /mnt/ssd/zim  # custom ZIM dir\n"
        ),
    )
    parser.add_argument("--version", default=K.DEFAULT_VERSION, metavar="X.Y.Z",
                        help=f"kiwix-tools version to install (default: {K.DEFAULT_VERSION}).")
    parser.add_argument("--zim-dir", default=K.DEFAULT_ZIM_DIR, metavar="PATH",
                        help="Directory where ZIM files will be stored (default: ~/oasis-offline/zim).")
    args = parser.parse_args()

    print()
    print("  OASIS -- Kiwix Installer")
    _hr()

    K.run(version=args.version, zim_dir=args.zim_dir, repo_root=REPO_ROOT)


if __name__ == "__main__":
    main()
