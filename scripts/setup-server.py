#!/usr/bin/env python3
"""
setup-server.py
---------------
Create the Python virtual environment and install all OASIS server
dependencies. Package list is read from scripts/offline-manifest.json
(server feature, type=pypi).

Install source is chosen automatically — wheels-first:
  • server/wheels/ populated -> install from wheels (PyPI as fallback per-package).
  • server/wheels/ empty, internet present -> install from PyPI.
  • server/wheels/ empty, no internet -> hard fail with instructions.

Dependency installs are best-effort: if a package is missing or fails to
install, the error is logged to the console and setup continues with the rest.

What this does:
  1. Verifies Python 3.9+
  2. Creates .venv in the repo root (if not already present)
  3. Installs the dependencies from the manifest (server feature)
  4. Installs system emoji + mono fonts (Raspberry Pi / Linux, online only)

Usage:
  python3 scripts/setup-server.py
  python3 scripts/setup-server.py --check     # report component status
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import server as S

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(
        description="Set up the OASIS Python server environment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/setup-server.py         # local wheels if present, else PyPI\n"
            "  python3 scripts/setup-server.py --check # report what is installed / missing\n"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether all components are ready; prompt to fix missing ones.",
    )
    args = parser.parse_args()

    S.run(check_mode=args.check, repo_root=REPO_ROOT)


if __name__ == "__main__":
    main()
