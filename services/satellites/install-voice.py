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

Idempotent + version-aware (apt is): safe to re-run; touches nothing already
current. Linux only — a no-op with a note off-Linux (e.g. the Mac dev box).
Needs internet (apt); if offline it skips with a note rather than failing, since
the voice is optional. Chromium picks the new voice up after a restart.

Usage:
  python3 services/satellites/install-voice.py
"""

import argparse
import os
import sys

# services/satellites/install-voice.py → repo root is three levels up.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
from common.oasis_lib import _hr, _ok, _info, _warn, _run, sudo_apt_cmd, has_internet  # noqa: E402

PACKAGES = ["speech-dispatcher", "speech-dispatcher-espeak-ng", "espeak-ng"]


def run():
    _hr()
    print("  ▶  Satellite pass-alert voice (text-to-speech)")
    _hr()
    if sys.platform != "linux":
        _warn("Not Linux — the voice needs a Pi/Debian browser stack. Nothing to do.")
        return 0
    if not has_internet():
        _warn("No internet — apt can't fetch the speech packages. Re-run this step "
              "online later; pass alerts still chime in the meantime.")
        return 0   # optional feature: skip cleanly rather than fail the setup run
    _info("Installing: " + ", ".join(PACKAGES))
    _run(sudo_apt_cmd("apt-get", "update", "-qq"), check=False)
    rc = _run(sudo_apt_cmd("apt-get", "install", "-y", *PACKAGES), check=False)
    if rc.returncode != 0:
        _warn("apt install failed — pass alerts will still chime, just no voice.")
        return 1
    _ok("Speech stack installed — Chromium gets an espeak-ng voice for pass alerts.")
    _info("Verify after a browser restart: on the Satellites page console, "
          "`speechSynthesis.getVoices().length` should be > 0.")
    return 0


def main():
    argparse.ArgumentParser(
        description="Install the text-to-speech stack for spoken satellite pass "
                    "alerts (speech-dispatcher + espeak-ng). Linux/apt; idempotent.",
    ).parse_args()
    sys.exit(run())


if __name__ == "__main__":
    main()
