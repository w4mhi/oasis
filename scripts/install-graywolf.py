#!/usr/bin/env python3
"""
install-graywolf.py
-------------------
Compatibility wrapper for the service-owned GrayWolf installer.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from services.graywolf.install import main


if __name__ == "__main__":
    main()
