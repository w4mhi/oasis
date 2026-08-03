#!/usr/bin/env python3
"""services/rtl-feed/install.py — entry point for the RTL-SDR → GrayWolf APRS feed.

Thin wrapper around common/feed.py (loaded by file path — the parent dir is
hyphenated, so it is not an importable package). Tests the dongle and wires the
aprs-sdr-feed.service audio feed into GrayWolf. Depends on the `rtl-sdr` tools
feature (features/rtl-sdr/install-rtl-sdr.py) for rtl_fm/socat + the DVB blacklist.

Usage:
  python3 services/rtl-feed/install.py                 # test + enable + instructions
  python3 services/rtl-feed/install.py --check         # test the SDR only, no changes
  python3 services/rtl-feed/install.py --no-enable     # write the unit, don't start it
  python3 services/rtl-feed/install.py --freq 144.800M --gain 28 --ppm 12
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEED = os.path.join(_HERE, "common", "feed.py")
_spec = importlib.util.spec_from_file_location("rtl_feed", _FEED)
_feed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_feed)


if __name__ == "__main__":
    _feed.main()
