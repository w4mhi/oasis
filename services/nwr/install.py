#!/usr/bin/env python3
"""services/nwr/install.py — entry point for the NOAA Weather Radio installer."""
import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
from services.nwr.common import nwr_install  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="Install NOAA Weather Radio (SAME/EAS) support, or run its watch daemon.",
    )
    parser.add_argument("--serve", action="store_true",
                        help="Run the always-on watch daemon (used by systemd).")
    args = parser.parse_args()

    if args.serve:
        from services.nwr.common import daemon  # noqa: E402
        daemon.serve(_REPO_ROOT)
        return 0

    result = nwr_install.run(repo_root=_REPO_ROOT, online=None)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
