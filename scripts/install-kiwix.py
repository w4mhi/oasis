#!/usr/bin/env python3
"""
install-kiwix.py
----------------
Download and install kiwix-serve on Linux. Kiwix provides offline access
to Wikipedia, OpenStreetMap, Project Gutenberg, and other reference content
through a local web server on port 8081.

What this does:
  1. Detects your architecture
  2. Downloads kiwix-tools (contains kiwix-serve) from download.kiwix.org
  3. Installs kiwix-serve to /usr/local/bin/
  4. Creates and enables a systemd service on port 8081

After install, run scripts/download-wikipedia.py to get content.

Usage:
  python3 scripts/install-kiwix.py
  python3 scripts/install-kiwix.py --version 3.8.2   # pin a version
  python3 scripts/install-kiwix.py --zim-dir /mnt/ssd/zim

Requires: Linux, sudo, internet access (~5 MB download).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from services.kiwix.install import main as kiwix_main


if __name__ == "__main__":
    kiwix_main()
