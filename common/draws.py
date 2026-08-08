"""Shared foundation for the NW Digital Radio DRAWS HAT features (GPS, RTC,
audio). Owns the board-level `dtoverlay=draws` line and the device probes each
subsystem installer uses. Pure transforms do no I/O so they unit-test off-Pi;
the thin I/O wrappers are exercised on the Pi."""
import glob
import os
import subprocess
import tempfile

OVERLAY_LINE = "dtoverlay=draws"
CONFIG_CANDIDATES = ("/boot/firmware/config.txt", "/boot/config.txt")

# NW Digital Radio's DRAWS config.txt block is THREE lines, not one:
#   dtoverlay=      resets overlay PARAMETER SCOPE, so the draws params that
#                   follow apply to draws and not to whatever overlay came before
#   dtoverlay=draws loads the card
#   force_turbo=1   pins the core clock the codec's sample clock derives from;
#                   without it the audio rate drifts as the CPU scales
#
# They live inside BEGIN/END markers so install and uninstall are exactly
# symmetric — insert the block, remove the block. That matters more than it
# sounds: a bare `dtoverlay=` is a legal stock line that can already exist (this
# bench board had one), so managing it as a loose line would risk deleting
# someone else's on uninstall. Inside markers, ours is unambiguously ours.
BLOCK_BEGIN = "# --- OASIS DRAWS (managed by features/draws-audio) ---"
BLOCK_END   = "# --- end OASIS DRAWS ---"
BLOCK_LINES = ["dtoverlay=", OVERLAY_LINE, "force_turbo=1"]


DRA_PI_OVERLAY = "dtoverlay=audioinjector-wm8731-audio"


def conflicting_overlay(text):
    """True if config.txt already loads the DRA-Pi HAT.

    The two boards want the same 40-pin header and the same I2S bus, so they
    cannot coexist. The Setup page keeps the checkboxes mutually exclusive, but
    nothing stops a direct run of a draws installer — which is how this bench
    box ended up carrying both and losing every sound card. A commented-out
    line does not count."""
    return any(ln.strip() == DRA_PI_OVERLAY for ln in text.splitlines())


def add_overlay_block(text):
    """Return (new_text, changed): ensure the managed DRAWS block is present.
    Idempotent — an existing block is left byte-identical."""
    if BLOCK_BEGIN in text:
        return text, False
    block = "\n".join([BLOCK_BEGIN, *BLOCK_LINES, BLOCK_END])
    return text.rstrip("\n") + "\n\n" + block + "\n", True


def removal_config_blocks():
    """The (begin, end) pair a DRAWS feature's removal record should strip."""
    return [[BLOCK_BEGIN, BLOCK_END]]


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


def sound_card_present(match="draws", cards_path="/proc/asound/cards"):
    """True if the DRAWS ALSA card has enumerated. The plain `dtoverlay=draws`
    registers a simple-card named `draws` (n7nix's `alsaname=udrc` rename is not
    used here), so the default match is the card name `draws`. Only appears after
    the overlay is loaded (i.e. after a reboot)."""
    try:
        with open(cards_path) as fh:
            return match.lower() in fh.read().lower()
    except OSError:
        return False


def sysfs_gpio(bcm, chips_dir="/sys/class/gpio"):
    """Translate a BCM pin number into the sysfs GLOBAL gpio number Direwolf's
    `PTT GPIO n` wants — gpiochip base + BCM.

    Modern kernels base the 40-pin bank at a non-zero offset (512 on a Pi 4
    running 6.x), so BCM 12/23 become 524/535. Picks the widest bank whose label
    looks like the SoC pin controller, which excludes the small expanders
    (raspberrypi-exp-gpio = 8 lines, and the SC16IS752's own 8-line chip that
    the DRAWS overlay itself registers). Returns None when no bank matches —
    the caller must then ask the operator rather than guess.

    (services/winlink has its own equivalent with override/warning semantics for
    the DRA-Pi path; this is the DRAWS-side copy so features/ does not have to
    import from services/.)"""
    best = None                      # (base, ngpio)
    for chip in sorted(glob.glob(os.path.join(chips_dir, "gpiochip*"))):
        try:
            base = int(open(os.path.join(chip, "base")).read().strip())
            ngpio = int(open(os.path.join(chip, "ngpio")).read().strip())
            label = open(os.path.join(chip, "label")).read().strip().lower()
        except (OSError, ValueError):
            continue
        if ("bcm" in label or "rp1" in label or "pinctrl" in label) and 40 <= ngpio <= 80:
            if best is None or ngpio > best[1]:
                best = (base, ngpio)
    return None if best is None else best[0] + bcm


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


# The .dtbo blobs OASIS vendors for this board. `udrc` is the DRAWS's
# predecessor and ships alongside it; both are installed together because the
# overlay line can reference either depending on the board revision.
OVERLAY_BLOBS = ("draws", "udrc")


def ensure_overlay_blobs():
    """Put OASIS's vendored draws/udrc .dtbo in the boot overlay directory.

    MUST run before anything writes `dtoverlay=draws`, and every DRAWS feature
    has to call it — not just the one that happens to need the sound card.
    Pi OS Trixie ships a draws.dtbo that does NOT bring the HAT up on kernel
    6.18.34, so the file merely EXISTING is not enough: a feature that enables
    the overlay line without installing ours boots into the broken one, and the
    failure looks like a wiring or bind fault rather than a packaging one.

    Returns [(name, changed, reason)] for the caller to report. Never raises —
    see common/overlays.install(); whatever it displaces is kept as
    <name>.dtbo.oasis-orig and can be put back with overlays.restore().
    """
    from common import overlays
    return [(name, *overlays.install(name)) for name in OVERLAY_BLOBS]


def ensure_overlay(cfg_path=None):
    """Idempotently add the managed DRAWS block (see BLOCK_LINES) to config.txt.
    Returns True if the file changed (reboot needed to load the overlay), False
    if it was already present. Raises RuntimeError if no config.txt exists."""
    cfg = cfg_path or config_path()
    if not cfg:
        raise RuntimeError("no config.txt found (looked in %s)" % (CONFIG_CANDIDATES,))
    with open(cfg) as fh:
        text = fh.read()
    new_text, changed = add_overlay_block(text)
    if changed:
        _write_text(cfg, new_text)
    return changed
