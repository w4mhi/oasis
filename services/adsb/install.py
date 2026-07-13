#!/usr/bin/env python3
"""services/adsb/install.py — entry point for the ADS-B installer and API service."""
import argparse, os, sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
from services.adsb.common import adsb

def main():
    p = argparse.ArgumentParser(description="Install ADS-B (dump1090-fa) + run its recorder/API.")
    p.add_argument("--serve", action="store_true", help="Run the recorder/API service (used by systemd).")
    args = p.parse_args()
    if args.serve:
        adsb.serve()
    else:
        adsb.run(repo_root=_REPO_ROOT)

if __name__ == "__main__":
    main()
