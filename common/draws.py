"""Shared foundation for the NW Digital Radio DRAWS HAT features (GPS, RTC,
audio). Owns the board-level `dtoverlay=draws` line and the device probes each
subsystem installer uses. Pure transforms do no I/O so they unit-test off-Pi;
the thin I/O wrappers are exercised on the Pi."""
import os

OVERLAY_LINE = "dtoverlay=draws"
CONFIG_CANDIDATES = ("/boot/firmware/config.txt", "/boot/config.txt")


def add_overlay_line(text):
    """Return (new_text, changed): ensure an active `dtoverlay=draws` line is
    present. A commented-out copy does not count (matches gps-L76X's has_active
    convention). Idempotent."""
    for line in text.splitlines():
        if line.strip() == OVERLAY_LINE:
            return text, False
    sep = "" if text.endswith("\n") or text == "" else "\n"
    return text + sep + OVERLAY_LINE + "\n", True


def overlay_available(overlays_dir="/boot/firmware/overlays"):
    """True if the OS ships the DRAWS overlay (draws.dtbo). The clean-fail guard
    for an image too old to include it."""
    return os.path.exists(os.path.join(overlays_dir, "draws.dtbo"))


def gps_device_present(dev="/dev/ttySC0"):
    """True if the DRAWS GPS serial device (SC16IS752 UART) has enumerated. Only
    appears after the overlay is loaded (i.e. after a reboot)."""
    return os.path.exists(dev)


def pps_present(dev="/dev/pps0"):
    """True if a PPS device node exists — the 'this box has PPS' hint
    common/gpsd_chrony.configure_chrony() keys its boot-safe SHM refclock on."""
    return os.path.exists(dev)
