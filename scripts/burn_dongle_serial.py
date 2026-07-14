#!/usr/bin/env python3
"""scripts/burn_dongle_serial.py — validating wrapper around
common.hardware_detect.burn_serial, invoked with root via a single pinned
sudoers Cmnd_Alias (see enable-service-controls.py).

This script is the ONLY place a web-request-supplied argument reaches a
privileged command in the hardware-aware engine. Sudoers pins WHICH script may
run as root; this script's own strict validation controls WHAT it will do with
its argument — reject anything that isn't a plain RTL-SDR serial before ever
touching rtl_eeprom. Do not relax the pattern to "convenience" shell-friendly
characters; RTL-SDR serials are short alphanumeric strings, nothing else is
legitimate here.
"""
import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from common.hardware_detect import burn_serial

# \Z (not $) — Python's $ matches just before a single TRAILING newline, not
# only true end-of-string, so "1090\n" would otherwise slip past this check.
# \Z has no such exception: it matches only the absolute end of the string.
_SERIAL_RE = re.compile(r'^[A-Za-z0-9]{1,32}\Z')


def valid_serial(s):
    return bool(s) and bool(_SERIAL_RE.match(s))


def main(argv):
    if len(argv) != 1 or not valid_serial(argv[0]):
        print("usage: burn_dongle_serial.py <serial>  "
              "(alphanumeric, 1-32 chars)", file=sys.stderr)
        return 1
    burn_serial(argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
