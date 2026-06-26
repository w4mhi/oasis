#!/usr/bin/env python3
"""
wsjtx.py  (library — CLI entry point is scripts/install-wsjtx.py)
----------------------------------------------------------------
Install WSJT-X (FT8 / FT4 / JT weak-signal digital modes) plus the supporting
stack on Raspberry Pi OS / Debian / Ubuntu:

  • wsjtx            — the FT8/FT4/JT modes application
  • libhamlib-utils  — rigctld, for CAT control / PTT of the radio
  • gpsd, gpsd-clients, chrony — offline time discipline from a GPS receiver.
    FT8 needs the system clock within ~1 s and OASIS has no internet NTP, so the
    clock is disciplined from GPS (USB or an I2C HAT) via gpsd + chrony.

Target hardware: Raspberry Pi 4 / 4 GB or CM4 stack — the Zero 2 W is too
constrained for the jt9 decoder plus a GUI.

Offline-first: if a complete bundled package set is present under
offline-packages/wsjtx/<suite>/ it is installed offline (apt resolves the local
dependency closure); otherwise it falls back to apt (internet required). All
package names come from the manifest (scripts/offline-manifest.json).

This installs the *software* only. Rig wiring, audio-device selection, GPS/chrony
configuration, the browser/kiosk UI, and the dashboard time-sync indicator are
separate steps — see tsk.md.

Usage:
  python3 scripts/install-wsjtx.py
  python3 scripts/install-wsjtx.py --check    # report what's installed / missing

Requires: Linux, apt/dpkg, sudo. Internet optional if a matching bundle is present.
"""

import os
import platform
import shutil
import subprocess
import sys

from .oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run, dpkg_installed_version
from . import manifest as M

FEATURE = "wsjtx"

# common/wsjtx.py → repo root is three levels up (scripts/common → scripts → repo).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKG_ROOT  = os.path.join(REPO_ROOT, "offline-packages")

ARCH_MAP = {
    "aarch64": "arm64",   # Pi 4 / CM4 / Pi 5 (64-bit OS) — the recommended target
    "arm64":   "arm64",
    "armv7l":  "armhf",   # 32-bit Pi OS
    "armhf":   "armhf",
    "x86_64":  "amd64",
    "amd64":   "amd64",
}

# Each apt package we install, paired with the command that proves it works.
TOOLS = [
    ("wsjtx",   "wsjtx"),            # the application
    ("rigctld", "libhamlib-utils"),  # CAT / PTT
    ("gpsd",    "gpsd"),             # GPS daemon (time source)
    ("chronyc", "chrony"),           # clock discipline
]

# Fallback package list if the manifest is older than this feature.
_FALLBACK_PKGS = ["wsjtx", "libhamlib-utils", "gpsd", "gpsd-clients", "chrony"]


def _packages(suite):
    """Apt package names for this feature, from the manifest (with a fallback)."""
    try:
        pkgs = M.apt_packages(FEATURE, suite)
        return pkgs or _FALLBACK_PKGS
    except (KeyError, ValueError):
        _warn("Manifest has no 'wsjtx' feature yet — using the built-in list.")
        return _FALLBACK_PKGS


# ── Step 1: Platform check ──────────────────────────────────────────────────────
def check_platform():
    _step(1, "Checking platform")
    if sys.platform != "linux":
        _fail("WSJT-X is installed from apt here — this script needs Linux.\n"
              "       On macOS/Windows, install WSJT-X from https://wsjt.sourceforge.io/.")
    if _run(["which", "apt"], check=False, capture_output=True).returncode != 0:
        _fail("apt not found. This script supports Debian/Ubuntu/Raspberry Pi OS.")

    machine  = platform.machine()
    deb_arch = ARCH_MAP.get(machine)
    if not deb_arch:
        _fail(f"No package mapping for architecture '{machine}'.")
    _ok("Debian/apt-based Linux detected")
    _ok(f"Architecture → .deb arch: {deb_arch}")

    # FT8 decoding is heavy; flag the under-powered boards rather than fail.
    try:
        with open("/proc/device-tree/model", encoding="utf-8", errors="ignore") as fh:
            model = fh.read().strip("\x00").strip()
        if model:
            _info(f"Board: {model}")
            if "Zero" in model or "Pi 3" in model:
                _warn("This board is light for FT8 (jt9 decode + GUI). "
                      "A Pi 4 / 4 GB or CM4 is recommended.")
    except OSError:
        pass
    return deb_arch


# ── Step 2: Detect Debian suite ──────────────────────────────────────────────────
def detect_suite():
    """Return 'bookworm' / 'trixie' / ... (Ubuntu LTS codenames mapped to Debian)."""
    try:
        out = subprocess.run(["lsb_release", "-cs"], capture_output=True,
                             text=True, check=True).stdout.strip().lower()
    except Exception:
        out = "other"
    out = {"jammy": "bookworm", "noble": "bookworm", "focal": "bullseye"}.get(out, out)
    _info(f"Debian suite: {out}")
    return out


# ── Step 3: Install ──────────────────────────────────────────────────────────────
def _bundled_debs(deb_dir, deb_arch):
    """Every installable .deb in the suite bundle dir (closure vendored by the
    builder), matching this arch or 'all'. macOS AppleDouble sidecars ignored."""
    if not os.path.isdir(deb_dir):
        return []
    return [
        os.path.join(deb_dir, f)
        for f in sorted(os.listdir(deb_dir))
        if not f.startswith("._") and f.endswith(".deb")
        and (f.endswith(f"_{deb_arch}.deb") or f.endswith("_all.deb"))
    ]


def install_offline(debs):
    """Install the bundled .deb closure; apt resolves local deps. Returns bool."""
    _info(f"Installing {len(debs)} bundled package(s) offline ...")
    cmd = ["sudo", "apt", "install", "--no-install-recommends", "-y"] + debs
    if _run(cmd, check=False).returncode == 0:
        _ok("Bundled packages installed.")
        return True
    _warn("Offline install failed (try: sudo apt-get install -f).")
    return False


def install_online(pkgs):
    """apt-install the package list (internet). Returns bool."""
    _info("Installing via apt (internet): " + ", ".join(pkgs))
    _run(["sudo", "apt", "update", "-qq"], check=False)
    cmd = ["sudo", "apt", "install", "--no-install-recommends", "-y", *pkgs]
    if _run(cmd, check=False).returncode == 0:
        _ok("Packages installed via apt.")
        return True
    _warn("apt could not install the WSJT-X stack.")
    return False


def install(suite, deb_arch):
    _step(3, "Installing WSJT-X + Hamlib + GPS time stack")
    pkgs    = _packages(suite)
    deb_dir = M.bundle_dir(PKG_ROOT, FEATURE, suite)
    _info(f"Offline dir: {os.path.relpath(deb_dir)}")

    debs = _bundled_debs(deb_dir, deb_arch)
    ok = False
    if debs:
        _ok(f"Found {len(debs)} bundled .deb(s) — trying offline first.")
        ok = install_offline(debs)
        if not ok:
            _warn("Falling back to apt.")
            ok = install_online(pkgs)
    else:
        _info("No bundled packages — using apt (internet required).")
        ok = install_online(pkgs)

    if not ok:
        _fail("WSJT-X stack could not be installed from the bundle or apt.\n"
              "       Check the errors above; rebuild the bundle with:\n"
              "         python3 scripts/create-oasis-offline.py")
    return ok


# ── Step 4: Verify ───────────────────────────────────────────────────────────────
def verify(check_only=False):
    _step(4, "Verifying" if not check_only else "Checking installed components")
    all_ok = True
    for cmd, pkg in TOOLS:
        if shutil.which(cmd):
            _ok(f"{cmd:<8} present   ({pkg} {dpkg_installed_version(pkg) or 'version?'})")
        else:
            all_ok = False
            _warn(f"{cmd:<8} MISSING   (package {pkg})")
    return all_ok


# ── Entry point (called by the thin CLI scripts/install-wsjtx.py) ────────────────
def run(check_only=False):
    print("\n  OASIS — install-wsjtx" + ("  [--check]" if check_only else ""))
    _hr()
    if check_only:
        verify(check_only=True)
        print()
        return

    _info("Installs WSJT-X (FT8/FT4) + Hamlib CAT + GPS time discipline.")
    _info("Recommended target: Raspberry Pi 4 / 4 GB or CM4.")
    print()

    deb_arch = check_platform()
    suite    = detect_suite()
    install(suite, deb_arch)
    ready = verify()

    _hr()
    print("\n  WSJT-X install complete." if ready else
          "\n  WSJT-X install finished with warnings (see above).")
    _info("Next (see tsk.md): point gpsd at your GPS + discipline chrony,")
    _info("then configure WSJT-X audio + CAT for your radio (IC-705 / FT-857D).")
    print()
