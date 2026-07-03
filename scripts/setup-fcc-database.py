#!/usr/bin/env python3
"""
setup-fcc-database.py
---------------------
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
  python3 scripts/setup-fcc-database.py              # steps 1 + 2 + 3
  python3 scripts/setup-fcc-database.py --full-zip   # force refresh zipcodes.csv
  python3 scripts/setup-fcc-database.py --index-only # re-index from existing EN.dat
  python3 scripts/setup-fcc-database.py --help

Re-run any time the FCC publishes new data (they refresh every Sunday):
  python3 scripts/setup-fcc-database.py
"""

import argparse
import os
import sys

# ── Shared library ─────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail,
    has_internet,
    fcc_download, fcc_build_index, fcc_build_zip_table, fcc_indexes_ready,
)

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(REPO_ROOT, "server")
DATA_DIR   = os.path.join(REPO_ROOT, "fcc-offline-database", "data")
EN_DAT     = os.path.join(DATA_DIR, "EN.dat")
ZIP_CSV    = os.path.join(DATA_DIR, "zipcodes.csv")
EN_IDX      = os.path.join(DATA_DIR, "EN.idx")
EN_NAME_IDX = os.path.join(DATA_DIR, "EN_name.idx")
EN_GRID_IDX = os.path.join(DATA_DIR, "EN_grid.idx")


def _indexes_nonempty():
    """True only when all three call-sign indexes exist and are non-empty."""
    for path in (EN_IDX, EN_NAME_IDX, EN_GRID_IDX):
        try:
            if os.path.getsize(path) <= 0:
                return False
        except OSError:
            return False
    return True


def _drop_bundle_zip():
    """Remove the bundled l_amat.zip once EN.dat is extracted and indexed.

    An offline bundle ships l_amat.zip (~80 MB); after extraction the target has
    EN.dat/HD.dat + the indexes, so the zip is pure bloat on a Raspberry Pi.
    Only deletes when EN.dat is present (safe — a future FCC refresh re-downloads
    the zip). The online install path never writes a zip, so this is a no-op there.
    """
    zip_path = os.path.join(DATA_DIR, "l_amat.zip")
    if os.path.exists(zip_path) and os.path.exists(EN_DAT):
        try:
            os.remove(zip_path)
            _ok("Removed bundled l_amat.zip (extracted — freed ~80 MB on disk).")
        except OSError as e:
            _warn(f"Could not remove l_amat.zip: {e}")


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Set up the OASIS offline FCC callsign database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/setup-fcc-database.py                  # download FCC + index + ZIP table\n"
            "  python3 scripts/setup-fcc-database.py --index-only     # re-index from existing EN.dat\n"
            "  python3 scripts/setup-fcc-database.py --full-zip       # force refresh zipcodes.csv\n"
        ),
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="Skip download; rebuild EN.idx from existing EN.dat + HD.dat.",
    )
    parser.add_argument(
        "--full-zip",
        action="store_true",
        help="Force download/rebuild of zipcodes.csv even if it already exists.",
    )
    args = parser.parse_args()

    print()
    print("  OASIS — FCC Offline Database Setup")
    _hr()
    _info(f"Data directory: {DATA_DIR}")

    if args.index_only:
        _info("--index-only: skipping download, rebuilding index from local data.")
        if not os.path.exists(EN_DAT):
            _fail(
                f"EN.dat not found in {DATA_DIR}.\n"
                "     Remove --index-only to download FCC data first."
            )
    else:
        if os.path.exists(EN_DAT):
            _info("EN.dat already present — skipping FCC download.")
        else:
            _info("EN.dat not found — downloading from FCC (requires internet) ...")
            if not has_internet():
                _fail(
                    "No internet access and EN.dat not found.\n"
                    "     Connect to the internet and re-run, or copy EN.dat to:\n"
                    f"     {DATA_DIR}"
                )
            _step(1, "Downloading FCC amateur license data")
            _info("Complete ULS file — EN.dat + HD.dat (~160 MB zipped).")
            fcc_download(DATA_DIR)

    # Fast path: an offline bundle (built on a capable machine by
    # create-oasis-offline.py) may ship prebuilt indexes. If they are valid for
    # this EN.dat, skip the memory-heavy rebuild that OOMs a 512 MB Pi Zero.
    # --index-only / --full-zip force the normal rebuild path.
    if not args.index_only and not args.full_zip and fcc_indexes_ready(DATA_DIR):
        _step(2, "Prebuilt indexes detected")
        _ok("Valid EN.idx / EN_name.idx / EN_grid.idx shipped with this bundle —")
        _ok("skipping the index build (no OOM risk on a Pi Zero).")
        _info("Force a rebuild with --index-only (or delete EN.idx.meta).")
        _drop_bundle_zip()
        print()
        print("  Setup complete.")
        _hr()
        return

    # Build the ZIP->grid table BEFORE the index. The call-sign index step also
    # builds the secondary grid index (EN_grid.idx), which requires zipcodes.csv
    # to exist. If the table were built afterwards, the grid index would be
    # skipped on a fresh machine and only appear on a second run.
    need_zip = args.full_zip or not os.path.exists(ZIP_CSV)
    if need_zip:
        if not has_internet():
            if os.path.exists(ZIP_CSV):
                _step(2, "ZIP->grid table")
                _info("Offline — keeping existing zipcodes.csv.")
            else:
                _step(2, "ZIP->grid table")
                _warn(
                    "No internet access and zipcodes.csv not found —\n"
                    "     the grid index (EN_grid.idx) will be skipped.\n"
                    "     Re-run online to build it."
                )
        else:
            _step(2, "Building ZIP->grid table from GeoNames")
            _info("Downloads ~1 MB from GeoNames (CC BY 4.0) and produces")
            _info("a ~40,000-row zipcodes.csv used for Maidenhead grid lookup.")
            fcc_build_zip_table(DATA_DIR)
    else:
        _step(2, "ZIP->grid table")
        _info("zipcodes.csv already present — skipping (use --full-zip to refresh).")

    # Existing, non-empty indexes: don't silently rebuild. The rebuild reads
    # EN.dat/HD.dat and is memory-heavy (can OOM a Pi Zero), so ask first —
    # default No keeps what's there. --index-only is an explicit rebuild request
    # and bypasses the prompt; a non-interactive run keeps existing indexes.
    if not args.index_only and _indexes_nonempty():
        rebuild = False
        if sys.stdin.isatty():
            try:
                ans = input(
                    "\n  EN.idx / EN_name.idx / EN_grid.idx already exist and are "
                    "non-empty.\n  Rebuild them from EN.dat? (y/N): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            rebuild = ans in ("y", "yes")
        if not rebuild:
            _step(3, "Call-sign index")
            _info("Keeping existing EN.idx / EN_name.idx / EN_grid.idx — skipping rebuild.")
            _drop_bundle_zip()
            print()
            print("  Setup complete.")
            _hr()
            return

    _step(3, "Building call-sign index")
    _info("Reads EN.dat + HD.dat once; writes EN.idx, EN_name.idx, EN_grid.idx.")
    _info("On a Pi this may take a couple of minutes.")
    fcc_build_index(DATA_DIR, SERVER_DIR)

    # Everything built OK — drop the bundled zip so it doesn't sit as ~80 MB of
    # bloat on the Pi (EN.dat/HD.dat + indexes are all in place now).
    if os.path.exists(os.path.join(DATA_DIR, "EN.idx")):
        _drop_bundle_zip()

    print()
    print("  Setup complete.")
    _hr()


if __name__ == "__main__":
    main()
