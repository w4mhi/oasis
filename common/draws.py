"""Shared foundation for the NW Digital Radio DRAWS HAT features (GPS, RTC,
audio). Owns the board-level `dtoverlay=draws` line and the device probes each
subsystem installer uses. Pure transforms do no I/O so they unit-test off-Pi;
the thin I/O wrappers are exercised on the Pi."""
import os
import subprocess
import tempfile

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


def config_path():
    return next((p for p in CONFIG_CANDIDATES if os.path.exists(p)), None)


def _write_text(path, content):
    """Write via sudo tee when we don't own the file (the Pi's /boot); fall back
    to a direct write when the path is writable (unit tests, dry runs)."""
    if os.access(path, os.W_OK):
        with open(path, "w") as fh:
            fh.write(content)
        return
    fd, tmp = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        subprocess.run(["sudo", "cp", tmp, path], check=True)
    finally:
        os.unlink(tmp)


def ensure_overlay(cfg_path=None):
    """Idempotently add `dtoverlay=draws` to config.txt. Returns True if the file
    changed (reboot needed to load the overlay), False if it was already present.
    Raises RuntimeError if no config.txt exists."""
    cfg = cfg_path or config_path()
    if not cfg:
        raise RuntimeError("no config.txt found (looked in %s)" % (CONFIG_CANDIDATES,))
    with open(cfg) as fh:
        text = fh.read()
    new_text, changed = add_overlay_line(text)
    if changed:
        _write_text(cfg, new_text)
    return changed
