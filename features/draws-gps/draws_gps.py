"""DRAWS on-board GPS -> gpsd/chrony ('GPS-disciplined time'). Library half; the
CLI entry point is features/draws-gps/install-draws-gps.py. Mirrors the
features/gps and features/gps-L76X split; reuses common/gpsd_chrony.py for the
service config and common/nmea.py for the 'is it actually working?' check. GPS is
on the SC16IS752's /dev/ttySC0 (not the primary UART), so there is no
serial-console eviction — unlike gps-L76X."""

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.dirname(_os.path.abspath(__file__)))))
from common import draws as _draws          # shared DRAWS config.txt block

GPS_DEVICE = "/dev/ttySC0"
# Bench-confirmed on pi4draws (2026-08-06): the on-board receiver reports
# NMEA0183 at 9600. Overridable with --baud in case a board ships reconfigured.
DEFAULT_BAUD = 9600

# What to check when NOTHING arrives on the port. Deliberately different from
# gps-L76X's: the DRAWS GPS is on the board, so there is no TX/RX/5V/GND wiring
# to get wrong — the failure is upstream, in the overlay or the SC16IS752 bind.
NO_DATA_HINT = (
    "The GPS is on the board, so this is not a wiring fault. Check, in order:  "
    "(1) `dtoverlay=draws` is in config.txt and the box has rebooted since;  "
    "(2) `dmesg | grep -i sc16is` shows the UART bound (`1-0050: ttySC0 ... is a "
    "SC16IS752`);  (3) `i2cdetect -y 1` shows `0x50 UU`;  (4) nothing else "
    "already holds the port (gpsd).")


def removal_record(repo_root=None):
    """Teardown record (declarative — see common/removal.py). Strip the shared
    DRAWS overlay line; leave the gpsd/chrony reconfig in place with an advisory
    (it is shared with other GPS sources — no safe automatic undo). Reboot to drop
    the overlay. NOTE (P2): once draws-rtc/draws-audio also rely on
    dtoverlay=draws, this strip must become ref-safe (strip only when the last
    DRAWS feature is removed)."""
    return {"config_blocks": _draws.removal_config_blocks(),
            "notes": ["gpsd/chrony reconfig left in place (shared — no safe "
                      "automatic undo)."],
            "requires_reboot": True}


def decide_exit_code(overlay_changed, device_present):
    """10 = 'config written, reboot required' (_REBOOT_EXIT_CODE) when the overlay
    was just added or the GPS device has not enumerated yet; 0 when the device is
    live and nothing changed."""
    return 10 if (overlay_changed or not device_present) else 0
