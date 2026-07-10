#!/usr/bin/env python3
"""
install-rtl-sdr.py
------------------
Thin CLI wrapper around scripts/common/rtl_sdr.py.

Installs RTL-SDR tools, the GrayWolf feed tools (socat/tcpdump), the multimon-ng
bench decoder, and the DVB-driver blacklist on Raspberry Pi OS / Debian / Ubuntu.
Source selection is suite-aware and newest-source-wins (apt vs bundle), with a
capability gate that warns when librtlsdr is too old for the RTL-SDR Blog V4
(needs >= 2.0). All logic lives in common/rtl_sdr.py; see docs/offline-architecture.md.

Usage:
  python3 features/rtl-sdr/install-rtl-sdr.py

Requires: Linux, apt/dpkg, sudo. Internet optional if a matching bundle is present.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rtl_sdr


def main():
    argparse.ArgumentParser(
        description="Install RTL-SDR tools on Debian/Ubuntu/Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/rtl-sdr/install-rtl-sdr.py   # suite-aware; bundle or apt, newest wins\n"),
    ).parse_args()
    rtl_sdr.run()


if __name__ == "__main__":
    main()
