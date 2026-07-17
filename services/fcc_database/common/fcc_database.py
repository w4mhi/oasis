#!/usr/bin/env python3
"""
services/fcc-database/common/fcc_database.py
-------------------------------------------
Service-owned implementation for the offline FCC database installer.
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _REPO_ROOT)

from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail,
    has_internet,
    fcc_download, fcc_build_index, fcc_build_zip_table, fcc_indexes_ready,
)

SERVER_DIR = os.path.join(_REPO_ROOT, "server")
DATA_DIR = os.path.join(_REPO_ROOT, "services", "fcc_database", "data")
EN_DAT = os.path.join(DATA_DIR, "EN.dat")
ZIP_CSV = os.path.join(DATA_DIR, "zipcodes.csv")
EN_IDX = os.path.join(DATA_DIR, "EN.idx")
EN_NAME_IDX = os.path.join(DATA_DIR, "EN_name.idx")
EN_GRID_IDX = os.path.join(DATA_DIR, "EN_grid.idx")


def _indexes_nonempty():
    for path in (EN_IDX, EN_NAME_IDX, EN_GRID_IDX):
        try:
            if os.path.getsize(path) <= 0:
                return False
        except OSError:
            return False
    return True


def _drop_bundle_zip():
    zip_path = os.path.join(DATA_DIR, "l_amat.zip")
    if not os.path.exists(zip_path):
        return
    if not fcc_indexes_ready(DATA_DIR):
        _info("Keeping l_amat.zip — indexes are not yet valid against EN.dat.")
        return
    try:
        os.remove(zip_path)
        _ok("Removed bundled l_amat.zip (indexes verified — freed ~80 MB on disk).")
    except OSError as e:
        _warn(f"Could not remove l_amat.zip: {e}")


def run(index_only=False, full_zip=False):
    print()
    print("  OASIS — FCC Offline Database Setup")
    _hr()
    _info(f"Data directory: {DATA_DIR}")
    # Announce the detection results up front — this is what decides
    # everything below (skip download? skip the index build?), and it's the
    # first thing worth seeing in a live/streamed log before any slow step.
    _info(f"EN.dat present: {'yes' if os.path.exists(EN_DAT) else 'no'}")
    _info(f"Prebuilt indexes valid against current EN.dat: {'yes' if fcc_indexes_ready(DATA_DIR) else 'no'}")

    if index_only:
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

    if not index_only and not full_zip and fcc_indexes_ready(DATA_DIR):
        _step(2, "Prebuilt indexes detected")
        _ok("Valid EN.idx / EN_name.idx / EN_grid.idx shipped with this bundle —")
        _ok("skipping the index build (no OOM risk on a Pi Zero).")
        _info("Force a rebuild with --index-only (or delete EN.idx.meta).")
        _drop_bundle_zip()
        print()
        print("  Setup complete.")
        _hr()
        return

    need_zip = full_zip or not os.path.exists(ZIP_CSV)
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

    if not index_only and _indexes_nonempty():
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

    if os.path.exists(os.path.join(DATA_DIR, "EN.idx")):
        _drop_bundle_zip()

    print()
    print("  Setup complete.")
    _hr()
