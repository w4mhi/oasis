#!/usr/bin/env python3
"""services/nwr/install.py — entry point for the NOAA Weather Radio installer."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
from services.nwr.common import nwr_install  # noqa: E402


def main():
    result = nwr_install.run(repo_root=_REPO_ROOT, online=None)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
