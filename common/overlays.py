"""
common/overlays.py — one mechanism for every device-tree overlay OASIS ships.

Three features need a .dtbo that the stock Raspberry Pi OS image does not
provide, or provides in a version that does not work: the DRAWS HAT (draws,
udrc) and the M5Stack CM4Stack panel (m5stack-cm4). Each had grown its own
lookup — draws asked only whether the OS happened to ship one, cm4stack carried
a private three-path candidate list — so "where does an overlay come from" had
two different answers and neither was written down.

One directory (`overlays/` at the repo root), one lookup, one install:

    overlays.install("draws")        # vendored -> /boot/firmware/overlays
    overlays.available("draws")      # loadable now, or installable from here?

Design notes:

* **Idempotent and never a downgrade.** install() copies only when the target is
  missing or its bytes differ, so re-running setup is free and a hand-placed
  overlay is replaced only by a genuinely different one.
* **It never raises.** A field box mid-install must not die because /boot is
  read-only; every entry point returns a (changed, reason) pair the caller can
  report. Setup scripts here are expected to degrade, not explode.
* **Boot directory varies by OS age.** Recent Pi OS uses /boot/firmware/overlays;
  older layouts use /boot/overlays. Probe rather than assume.

Provenance and rebuild steps for every vendored overlay live in overlays/SOURCE.md.
"""

import os
import shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

# Where a vendored overlay may live, in order. The repo-root `overlays/` is the
# canonical home; the feature-local paths are kept so a bundle built before this
# unification, or an operator's manual drop, still works.
def _vendor_dirs(repo_root):
    return (
        os.path.join(repo_root, "overlays"),
        os.path.join(repo_root, "displays", "cm4stack", "packages"),
        os.path.join(repo_root, "displays", "cm4stack", "overlays"),
        os.path.join(repo_root, "displays", "cm4stack"),
    )


# Newer Raspberry Pi OS mounts the firmware partition at /boot/firmware; older
# images put overlays straight in /boot. Checked in order, first existing wins.
BOOT_DIRS = ("/boot/firmware/overlays", "/boot/overlays")


def boot_dir(candidates=BOOT_DIRS):
    """The live overlay directory for this OS, or the modern default when none
    exists yet (a dev box, or a test with nothing mounted)."""
    for d in candidates:
        if os.path.isdir(d):
            return d
    return candidates[0]


def vendored_path(name, repo_root=None):
    """Absolute path to the .dtbo OASIS ships for `name`, or None.

    `name` is the overlay's base name without the extension ("draws"), the same
    token that goes into config.txt's `dtoverlay=` line.
    """
    filename = name if name.endswith(".dtbo") else name + ".dtbo"
    for d in _vendor_dirs(repo_root or REPO_ROOT):
        candidate = os.path.join(d, filename)
        if os.path.exists(candidate):
            return candidate
    return None


def installed(name, overlays_dir=None):
    """True when the OS can load this overlay right now."""
    filename = name if name.endswith(".dtbo") else name + ".dtbo"
    return os.path.exists(os.path.join(overlays_dir or boot_dir(), filename))


def available(name, overlays_dir=None, repo_root=None):
    """True when the overlay is loadable now OR we ship one we could install.

    The distinction matters for a clean failure message: "your OS is too old"
    and "we have it, run the installer" are different problems, and only the
    first is the operator's to solve.
    """
    return installed(name, overlays_dir) or vendored_path(name, repo_root) is not None


def _same_bytes(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def install(name, overlays_dir=None, repo_root=None, copy=shutil.copy2):
    """Put OASIS's vendored overlay in the boot overlay directory.

    Returns (changed, reason):
      (False, "no-vendored-copy")  we ship nothing for this name
      (False, "already-current")   the target is byte-identical — nothing to do
      (False, "<error text>")      the copy failed (read-only /boot, no root…)
      (True,  "installed")         it was missing
      (True,  "replaced")          it differed and was overwritten

    A kernel or firmware update can wipe a hand-copied overlay, so installers
    call this on EVERY run rather than only when the file is absent.
    """
    src = vendored_path(name, repo_root)
    if not src:
        return False, "no-vendored-copy"
    dest_dir = overlays_dir or boot_dir()
    dest = os.path.join(dest_dir, os.path.basename(src))
    existed = os.path.exists(dest)
    if existed and _same_bytes(src, dest):
        return False, "already-current"
    try:
        os.makedirs(dest_dir, exist_ok=True)
        copy(src, dest)
    except (OSError, PermissionError) as exc:
        return False, str(exc)
    return True, ("replaced" if existed else "installed")
