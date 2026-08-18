#!/usr/bin/env python3
"""
openwebrx.py  (library — CLI entry point is services/openwebrx/install.py)
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
demand with `sudo systemctl start openwebrx`.

OASIS installs it and then leaves it alone (3.92.0): no service card, no health
tile, no dongle assignment. It picks its SDR inside its own Admin -> SDR devices
UI, so any OASIS-side record of that choice described a decision OASIS had not
made. What OASIS still does is evidence-based — `openwebrx` stays in
hardware_detect.SDR_CONSUMING_UNITS, so a tuner it holds reads busy, and the
dashboard offers to stop it before starting one of its own SDR services.

Usage:
  python3 services/openwebrx/install.py
  python3 services/openwebrx/install.py --check    # report install/status only

Requires: Linux (Debian/Raspberry Pi OS bookworm/trixie), apt/dpkg, sudo, internet.
"""

import platform
import shutil
import sys

from common.oasis_lib import (_hr, _step, _ok, _info, _warn, _fail, _run,
                        dpkg_installed_version, has_internet, sudo_apt_cmd)

PACKAGE   = "openwebrx"
# OASIS blesses FlightAware's dump1090-fa (services/adsb) as THE Mode S decoder;
# the OpenWebRX+ repo's own dump1090-fa-minimal collides with it. See
# _apt_install_argv().
BLESSED_DUMP1090      = "dump1090-fa"
CONFLICTING_RECOMMENDS = "dump1090-fa-minimal"
KEY_URL   = "https://luarvique.github.io/ppa/openwebrx-plus.gpg"
KEYRING   = "/etc/apt/trusted.gpg.d/openwebrx-plus.gpg"
LIST_PATH = "/etc/apt/sources.list.d/openwebrx-plus.list"


def removal_record(repo_root=None):
    """Teardown record for the openwebrx feature: stop/disable the service and
    remove the OASIS-added apt repo (keyring + list). The apt-installed openwebrx
    package itself is left in place, matching remove-oasis's leave-apt policy."""
    return {"services": [PACKAGE], "files": [KEYRING, LIST_PATH]}
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


def _apt_install_argv():
    """Build the apt argv that installs openwebrx, vetoing a broken recommends.

    OpenWebRX+ *Recommends* dump1090-fa-minimal — a 2023 PPA build that ships
    /usr/bin/dump1090-fa and declares no Conflicts/Replaces/Provides against
    FlightAware's real dump1090-fa, which OASIS's ADS-B feature installs and
    which owns that exact path. dpkg therefore refuses the unpack ("trying to
    overwrite '/usr/bin/dump1090-fa', which is also in package dump1090-fa")
    and apt exits non-zero, taking a perfectly good OpenWebRX install down over
    one optional decoder. When the blessed dump1090-fa is already present, veto
    the minimal variant with apt's trailing-'-' syntax; OpenWebRX shells out to
    /usr/bin/dump1090-fa by path, so the real package serves its ADS-B mode.

    Mirror of services/adsb/common/adsb.py::_remove_conflicting_dump1090_minimal(),
    which guards the same collision from the other install order.
    """
    argv = list(sudo_apt_cmd("apt", "install", "-y", PACKAGE))
    if dpkg_installed_version(BLESSED_DUMP1090):
        _info(f"{BLESSED_DUMP1090} already installed — vetoing the conflicting "
              f"{CONFLICTING_RECOMMENDS} recommends.")
        argv.append(f"{CONFLICTING_RECOMMENDS}-")
    return argv


def install():
    _run(sudo_apt_cmd("apt", "update", "-qq"), check=False)
    if _run(_apt_install_argv(), check=False).returncode != 0:
        # apt exits non-zero when ANY package in the transaction fails — including
        # an optional Recommends that OpenWebRX runs fine without. Don't abort the
        # whole installer in that case: bailing here skips set_default_disabled()
        # below and leaves openwebrx ENABLED at boot, where it seizes the RTL-SDR
        # away from GrayWolf / the APRS feed / ADS-B on every reboot.
        if not dpkg_installed_version(PACKAGE):
            _fail("apt could not install openwebrx — check the repo entry and internet.")
        _warn("apt reported an error, but openwebrx itself installed — one of its "
              "optional decoders (Recommends) failed. Continuing.")
        return
    _ok("openwebrx installed.")


def set_default_disabled():
    # OASIS policy: OpenWebRX is OFF by default (exclusive RTL-SDR use) — the
    # operator starts it on demand with systemctl. OASIS does not manage it
    # beyond this install (no card, no dongle assignment; 3.92.0), so this
    # disable is the ONLY thing standing between a fresh install and an ORX that
    # takes the tuner at every boot before anything else can. Verify it actually
    # took: reporting success while ORX stays boot-enabled is how it ends up
    # fighting for the dongle.
    _run(["sudo", "systemctl", "disable", "--now", "openwebrx"], check=False)
    state = _run(["systemctl", "is-enabled", "openwebrx.service"],
                 check=False, capture_output=True, text=True)
    if (getattr(state, "stdout", "") or "").strip() == "enabled":
        _warn("openwebrx is STILL enabled at boot — disable it by hand "
              "(`sudo systemctl disable --now openwebrx`) or it will grab the "
              "RTL-SDR at every boot.")
        return
    _ok("openwebrx set OFF by default (`sudo systemctl start openwebrx` when needed).")


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
    _info("Off by default and unmanaged: `sudo systemctl start openwebrx`, then :8073.")
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
    _info("Start it yourself: sudo systemctl start openwebrx  (OASIS does not "
          "manage it — no dashboard card, no dongle assignment).")
    _info("Stop the APRS SDR feed / ADS-B / weather watch first if they hold the "
          "dongle you want.")
    _info("Then open http://<host>:8073/ and set Admin → SDR devices (see SETUP.md "
          "for recommended EmComm monitoring bands).")
    print()
