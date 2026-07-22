#!/usr/bin/env python3
"""
services/satellites/install-voice.py
------------------------------------
Install the text-to-speech stack the Satellites page uses for the *spoken* pass
alert — after the Morse-"V" chime at T-10, it announces which bird is coming
("<sat>, in ten minutes, maximum elevation NN degrees"). The voice is produced
by the browser's Web Speech API, which on Raspberry Pi OS / Debian needs
speech-dispatcher + the espeak-ng engine installed for Chromium to expose any
voice at all. Without them the page degrades gracefully to the chime alone —
this step just adds the voice.

Installs (apt): speech-dispatcher, speech-dispatcher-espeak-ng, espeak-ng.

Offline-first: prefers .debs bundled by create-oasis-offline.py into
services/satellites/packages/satellites-voice/<suite>/ (manifest feature
'satellites-voice'); falls back to apt when the bundle is absent and there's
internet. Idempotent + version-aware — never downgrades an already-newer package,
safe to re-run. Linux only — a no-op with a note off-Linux (e.g. the Mac dev
box). If neither a bundle nor internet is available it skips with a note rather
than failing, since the voice is optional. Chromium picks it up after a restart.

Usage:
  python3 services/satellites/install-voice.py
"""

import argparse
import os
import platform
import subprocess
import sys

# services/satellites/install-voice.py → repo root is three levels up.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
import common.manifest as M  # noqa: E402
from common.oasis_lib import (  # noqa: E402
    _hr, _ok, _info, _warn, _run, sudo_apt_cmd, has_internet,
    deb_field, dpkg_installed_version, version_decision,
)

PACKAGES = ["speech-dispatcher", "speech-dispatcher-espeak-ng", "espeak-ng"]
FEATURE = "satellites-voice"

# platform.machine() → Debian .deb arch used in the bundle filenames.
_ARCH_MAP = {
    "aarch64": "arm64", "arm64": "arm64",
    "armv7l": "armhf", "armhf": "armhf", "armv6l": "armhf",
    "x86_64": "amd64", "amd64": "amd64",
}


def _deb_arch():
    """This host's .deb architecture, or None if unmapped."""
    return _ARCH_MAP.get(platform.machine())


def _detect_suite():
    """Return the Debian suite codename ('bookworm', 'trixie', …), Ubuntu mapped
    to its Debian equivalent, or 'other' when it can't be determined."""
    try:
        out = subprocess.run(["lsb_release", "-cs"], capture_output=True,
                             text=True, check=True)
        suite = out.stdout.strip().lower()
    except Exception:
        suite = "other"
    return {"jammy": "bookworm", "noble": "bookworm", "focal": "bullseye"}.get(suite, suite)


def _bundle_debs(suite, deb_arch):
    """.debs for *deb_arch* in the satellites-voice bundle dir for *suite*, or []."""
    root = os.path.join(REPO_ROOT, "offline-packages")
    bundle = M.bundle_dir(root, FEATURE, suite=suite)
    if not os.path.isdir(bundle):
        return []
    return [os.path.join(bundle, f) for f in sorted(os.listdir(bundle))
            if f.endswith(f"_{deb_arch}.deb") and not f.startswith("._")]


def _install_offline(deb_paths):
    """Install bundled .debs, skipping any not newer than what's installed
    (never downgrade). `apt install ./file.deb` resolves deps from the set.
    Returns True on success (including nothing-to-do)."""
    to_install = []
    for p in deb_paths:
        pkg = deb_field(p, "Package") or os.path.basename(p)
        inst = dpkg_installed_version(pkg) if deb_field(p, "Package") else None
        if version_decision(pkg, deb_field(p, "Version"), inst) in ("install", "upgrade"):
            to_install.append(p)
    if not to_install:
        _ok("Speech stack already current from the bundle — nothing to install.")
        return True
    _info("Installing from the offline bundle:")
    for p in to_install:
        _info(f"  {os.path.basename(p)}")
    rc = _run(sudo_apt_cmd("apt", "install", "--no-install-recommends", "-y", *to_install),
              check=False)
    if rc.returncode == 0:
        return True
    _warn("Offline .deb install failed (likely a missing dependency). Try:  sudo apt-get install -f")
    return False


def _install_online():
    """Install the speech stack from apt (needs internet). Returns True on success."""
    _info("Installing via apt (internet): " + ", ".join(PACKAGES))
    _run(sudo_apt_cmd("apt-get", "update", "-qq"), check=False)
    rc = _run(sudo_apt_cmd("apt-get", "install", "-y", *PACKAGES), check=False)
    if rc.returncode == 0:
        return True
    _warn("apt install failed — pass alerts will still chime, just no voice.")
    return False


def run():
    _hr()
    print("  ▶  Satellite pass-alert voice (text-to-speech)")
    _hr()
    if sys.platform != "linux":
        _warn("Not Linux — the voice needs a Pi/Debian browser stack. Nothing to do.")
        return 0

    suite, deb_arch = _detect_suite(), _deb_arch()
    _info(f"Debian suite: {suite} · arch: {deb_arch or platform.machine()}")

    ok = False
    debs = _bundle_debs(suite, deb_arch) if deb_arch else []
    if debs:
        _info(f"Found {len(debs)} bundled package(s) — installing offline.")
        ok = _install_offline(debs)
    elif has_internet():
        _info("No offline bundle for this suite/arch — falling back to apt.")
        ok = _install_online()
    else:
        _warn("No offline bundle and no internet — can't fetch the speech packages. "
              "Re-run online (or with the bundle) later; pass alerts still chime.")
        return 0   # optional feature: skip cleanly rather than fail the setup run

    if not ok:
        return 1
    _ok("Speech stack installed — Chromium gets an espeak-ng voice for pass alerts.")
    _info("Verify after a browser restart: on the Satellites page console, "
          "`speechSynthesis.getVoices().length` should be > 0.")
    return 0


def main():
    argparse.ArgumentParser(
        description="Install the text-to-speech stack for spoken satellite pass "
                    "alerts (speech-dispatcher + espeak-ng). Offline-first; Linux/apt; "
                    "idempotent.",
    ).parse_args()
    sys.exit(run())


if __name__ == "__main__":
    main()
