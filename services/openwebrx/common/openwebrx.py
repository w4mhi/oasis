#!/usr/bin/env python3
"""
openwebrx.py  (library — CLI entry point is scripts/install-openwebrx.py)
------------------------------------------------------------------------
Install OpenWebRX+ — a browser-based, receive-only SDR receiver/decoder — as the
OASIS SIGINT/monitoring front end. It decodes a wide range of modes (voice,
RTTY, CW, FT8/FT4/WSPR, SSTV, ACARS, AIS, ADS-B, POCSAG, …) from an RTL-SDR and
serves a waterfall + audio in the browser on port 8073.

It is installed from the OpenWebRX+ upstream apt repository (luarvique ppa), so
this step needs internet — it is NOT vendored into the offline bundle (the repo
is third-party; offline bundling is a separate future task). The maintainer's
station provisions online; teammates get it the same way (online), or you extend
create-oasis-offline.py to vendor it.

By OASIS policy OpenWebRX is installed **disabled** (off at boot): it grabs the
RTL-SDR exclusively, which the APRS SDR feed + GrayWolf also use. Start it on
demand from the dashboard (which stops GrayWolf + the feed first); stopping it
from the dashboard restores them.

Usage:
  python3 scripts/install-openwebrx.py
  python3 scripts/install-openwebrx.py --check    # report install/status only

Requires: Linux (Debian/Raspberry Pi OS bookworm/trixie), apt/dpkg, sudo, internet.
"""

import platform
import shutil
import sys

from common.oasis_lib import (_hr, _step, _ok, _info, _warn, _fail, _run,
                        dpkg_installed_version, has_internet, sudo_apt_cmd)

PACKAGE   = "openwebrx"
KEY_URL   = "https://luarvique.github.io/ppa/openwebrx-plus.gpg"
KEYRING   = "/etc/apt/trusted.gpg.d/openwebrx-plus.gpg"
LIST_PATH = "/etc/apt/sources.list.d/openwebrx-plus.list"
BASE_URL  = "https://luarvique.github.io/ppa"

# OpenWebRX+ repo path per Debian suite. bookworm confirmed; trixie assumed to
# follow the same scheme; bullseye uses 'debian' (and also needs the upstream
# OpenWebRX repo for deps — out of scope here, we target bookworm/trixie).
REPO_PATHS = {"bookworm": "bookworm", "trixie": "trixie", "bullseye": "debian"}

ARCH_MAP = {"aarch64": "arm64", "arm64": "arm64", "armv7l": "armhf",
            "armhf": "armhf", "x86_64": "amd64", "amd64": "amd64"}


def check_platform():
    _step(1, "Checking platform")
    if sys.platform != "linux":
        _fail("OpenWebRX+ installs from an apt repo — Linux only.\n"
              "       See https://www.openwebrx.de/ for other platforms.")
    if _run(["which", "apt"], check=False, capture_output=True).returncode != 0:
        _fail("apt not found. This script supports Debian / Raspberry Pi OS.")
    if not ARCH_MAP.get(platform.machine()):
        _fail(f"Unsupported architecture '{platform.machine()}'.")
    _ok("Debian/apt-based Linux detected")


def detect_suite():
    """Autodetect the Debian suite (bookworm / trixie / …). Tries lsb_release,
    then falls back to /etc/os-release VERSION_CODENAME so it still works on a
    minimal image without lsb_release."""
    suite = ""
    try:
        import subprocess
        suite = subprocess.run(["lsb_release", "-cs"], capture_output=True,
                               text=True).stdout.strip().lower()
    except Exception:
        pass
    if not suite or suite == "n/a":
        try:
            with open("/etc/os-release", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VERSION_CODENAME="):
                        suite = line.split("=", 1)[1].strip().strip('"').lower()
                        break
        except OSError:
            pass
    suite = {"jammy": "bookworm", "noble": "bookworm", "focal": "bullseye"}.get(suite, suite) or "other"
    _info(f"Debian suite: {suite}")
    return suite


def add_repo(suite):
    repo_path = REPO_PATHS.get(suite)
    if not repo_path:
        _warn(f"Unknown suite '{suite}' — assuming repo path '{suite}'. "
              "If install fails, check https://luarvique.github.io/ppa/.")
        repo_path = suite

    _info("Installing the OpenWebRX+ signing key ...")
    key_cmd = f"curl -fsSL {KEY_URL} | sudo gpg --batch --yes --dearmor -o {KEYRING}"
    if _run(["bash", "-c", key_cmd], check=False).returncode != 0:
        _fail("Could not install the repo signing key (need internet + curl).")

    line = f"deb [signed-by={KEYRING}] {BASE_URL}/{repo_path} ./"
    if _run(["bash", "-c", f'echo "{line}" | sudo tee {LIST_PATH}'], check=False).returncode != 0:
        _fail("Could not write the apt sources entry.")
    _ok(f"Repository added: {BASE_URL}/{repo_path}")


def install():
    _run(sudo_apt_cmd("apt", "update", "-qq"), check=False)
    if _run(sudo_apt_cmd("apt", "install", "-y", PACKAGE), check=False).returncode != 0:
        _fail("apt could not install openwebrx — check the repo entry and internet.")
    _ok("openwebrx installed.")


def set_default_disabled():
    # OASIS policy: OpenWebRX is OFF by default (exclusive RTL-SDR use). Start it
    # on demand from the dashboard, which stops GrayWolf + the SDR feed first.
    _run(["sudo", "systemctl", "disable", "--now", "openwebrx"], check=False)
    _ok("openwebrx set OFF by default (start it from the dashboard when needed).")


def verify():
    if shutil.which("openwebrx") or dpkg_installed_version("openwebrx"):
        _ok(f"openwebrx present ({dpkg_installed_version('openwebrx') or 'installed'})")
    else:
        _warn("openwebrx not found in PATH/dpkg — install may have failed.")


def run(check_only=False):
    print("\n  OASIS — install-openwebrx" + ("  [--check]" if check_only else ""))
    _hr()
    if sys.platform != "linux":
        if check_only:
            _warn("Linux only — nothing to check here.")
            print(); return
        _fail("OpenWebRX+ installs from an apt repo — Linux only.")

    if check_only:
        verify()
        en = _run(["systemctl", "is-enabled", "openwebrx.service"],
                  check=False, capture_output=True, text=True)
        _info(f"boot state: {(getattr(en, 'stdout', '') or '').strip() or 'unknown'} "
              "(off by default is expected)")
        print(); return

    _info("Browser-based SDR receiver/decoder (RX only) on port 8073.")
    _info("Off by default; start on demand from the dashboard.")
    print()

    check_platform()
    suite = detect_suite()
    if not has_internet():
        _warn("No internet detected — OpenWebRX+ installs from its upstream apt "
              "repo, so a connection is required (it is not vendored offline).")

    _step(2, "Adding the OpenWebRX+ apt repository")
    add_repo(suite)
    _step(3, "Installing OpenWebRX")
    install()
    _step(4, "Setting OpenWebRX off by default")
    set_default_disabled()
    _step(5, "Verifying")
    verify()

    _hr()
    print("\n  OpenWebRX install complete.")
    _info("Start it from the OASIS dashboard (the OpenWebRX card) — it stops "
          "GrayWolf + the APRS SDR feed first.")
    _info("Then open http://<host>:8073/ and set Admin → SDR profiles (see SETUP.md "
          "for recommended EmComm monitoring bands).")
    print()
