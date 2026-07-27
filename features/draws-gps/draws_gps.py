"""DRAWS on-board GPS -> gpsd/chrony ('GPS-disciplined time'). Library half; the
CLI entry point is features/draws-gps/install-draws-gps.py. Mirrors the
features/gps and features/gps-L76X split; reuses common/gpsd_chrony.py. GPS is on
the SC16IS752's /dev/ttySC0 (not the primary UART), so there is no serial-console
eviction — unlike gps-L76X."""

GPS_DEVICE = "/dev/ttySC0"


def removal_record(repo_root=None):
    """Teardown record (declarative — see common/removal.py). Strip the shared
    DRAWS overlay line; leave the gpsd/chrony reconfig in place with an advisory
    (it is shared with other GPS sources — no safe automatic undo). Reboot to drop
    the overlay. NOTE (P2): once draws-rtc/draws-audio also rely on
    dtoverlay=draws, this strip must become ref-safe (strip only when the last
    DRAWS feature is removed)."""
    return {"config_lines": ["dtoverlay=draws"],
            "notes": ["gpsd/chrony reconfig left in place (shared — no safe "
                      "automatic undo)."],
            "requires_reboot": True}


def decide_exit_code(overlay_changed, device_present):
    """10 = 'config written, reboot required' (_REBOOT_EXIT_CODE) when the overlay
    was just added or the GPS device has not enumerated yet; 0 when the device is
    live and nothing changed."""
    return 10 if (overlay_changed or not device_present) else 0
