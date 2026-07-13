#!/usr/bin/env python3
"""
services/kiwix/download-wikipedia.py
----------------------------------
Service-owned entry point for downloading Kiwix ZIM content.
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from scripts.download_wikipedia import *  # noqa: F401,F403


if __name__ == "__main__":
    main()
