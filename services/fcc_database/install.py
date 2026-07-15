#!/usr/bin/env python3
"""Service entry point for the FCC database installer."""

from services.fcc_database.common.fcc_database import run


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Install or refresh the offline FCC database")
    parser.add_argument("--index-only", action="store_true", help="Rebuild indexes from local data")
    parser.add_argument("--full-zip", action="store_true", help="Rebuild the ZIP table")
    args = parser.parse_args()
    run(index_only=args.index_only, full_zip=args.full_zip)
