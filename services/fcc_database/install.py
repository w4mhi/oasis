#!/usr/bin/env python3
"""
services/fcc_database/install.py
---------------------------------
One-shot setup for the OASIS offline FCC callsign database.

What this does (in order):
  1. Downloads EN.dat + HD.dat from the FCC (~160 MB total) — skipped if
     EN.dat already exists in the data directory.
  2. Downloads and builds the ZIP->grid table from GeoNames (zipcodes.csv)
     if it is missing or --full-zip is passed.
  3. Builds the binary-search indexes (EN.idx + EN_name.idx + EN_grid.idx).
     The grid index needs zipcodes.csv, which is why step 2 runs first.

Install source is chosen automatically:
  - If EN.dat already exists, the download is skipped and only the index
    is rebuilt (fast re-index).
  - If EN.dat is absent, the FCC data is downloaded from the internet.
    The script exits with an error if the download fails and no local data
    is available.

Usage:
  python3 services/fcc_database/install.py              # steps 1 + 2 + 3
  python3 services/fcc_database/install.py --full-zip   # force refresh zipcodes.csv
  python3 services/fcc_database/install.py --index-only # re-index from existing EN.dat
  python3 services/fcc_database/install.py --help

Re-run any time the FCC publishes new data (they refresh every Sunday):
  python3 services/fcc_database/install.py
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from services.fcc_database.common.fcc_database import run


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Set up the OASIS offline FCC callsign database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 services/fcc_database/install.py                  # download FCC + index + ZIP table\n"
            "  python3 services/fcc_database/install.py --index-only     # re-index from existing EN.dat\n"
            "  python3 services/fcc_database/install.py --full-zip       # force refresh zipcodes.csv\n"
        ),
    )
    parser.add_argument("--index-only", action="store_true", help="Skip download; rebuild EN.idx from existing EN.dat + HD.dat.")
    parser.add_argument("--full-zip", action="store_true", help="Force download/rebuild of zipcodes.csv even if it already exists.")
    args = parser.parse_args()
    run(index_only=args.index_only, full_zip=args.full_zip)


if __name__ == "__main__":
    main()
