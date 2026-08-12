#!/usr/bin/env python3
"""
services/satellites/install-dsp.py
----------------------------------
Install the DSP stack that Doppler-tracked satellite capture needs: csdr +
libcsdr0 (the signal-processing library), python3-csdr (the pycsdr bindings) and
rtl-connector (the daemon that holds the dongle and serves IQ on a socket).

WHAT THIS BUYS, AND WHAT WORKS WITHOUT IT
-----------------------------------------
Nothing here is required to use the Satellites page. The roster, passes, map,
alerts, horizon mask and the DOPPLER readout all work with no SDR at all, and
recording still works through the plain rtl_fm path — just uncorrected. This
stack adds the *tracked* capture path, which is the only thing that makes
narrowband CW/SSB usable: at 435 MHz a LEO pass sweeps about +/-10 kHz, and a
12 kHz SSB window loses the signal for most of the pass without correction.

Same shape as install-predict.py: an optional dependency of the CAPTURE, not of
the feature, installed when the operator asks for it rather than bloating every
station.

WHY NOT openwebrx ITSELF
------------------------
These packages come from the same luarvique repo that OASIS already trusts for
OpenWebRX+, but we deliberately install ONLY the DSP pieces. openwebrx itself
seizes the RTL-SDR exclusively, which is why OASIS installs it disabled; taking
the library without the server means that never applies.

Online-only for now: the offline bundler does no dependency resolution, and
these packages pull libfftw3/libsamplerate, so vendoring them needs a per-suite
curated list. Tracked capture therefore needs one online install; everything
else about the station stays offline-first.

Usage:
  python3 services/satellites/install-dsp.py
  python3 services/satellites/install-dsp.py --check    # report only, change nothing
"""

import argparse
import os
import platform
import shutil
import sys

# services/satellites/install-dsp.py -> repo root is three levels up.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
from common.oasis_lib import (_hr, _step, _ok, _info, _warn, _fail, _run,  # noqa: E402
                              dpkg_installed_version, has_internet, sudo_apt_cmd)

PACKAGES = ["csdr", "libcsdr0", "python3-csdr", "rtl-connector"]

KEY_URL = "https://luarvique.github.io/ppa/openwebrx-plus.gpg"
BASE_URL = "https://luarvique.github.io/ppa"
# Deliberately NOT the same keyring/list the openwebrx feature writes, even
# though it is the same repo and the same key. Sharing them would mean removing
# either feature silently breaks the other's apt source; separate files keep each
# feature's removal record self-contained. apt will note the repo is configured
# twice when both are installed — cosmetic, and worth it.
KEYRING = "/etc/apt/trusted.gpg.d/oasis-sdr-dsp.gpg"
LIST_PATH = "/etc/apt/sources.list.d/oasis-sdr-dsp.list"

REPO_PATHS = {"bookworm": "bookworm", "trixie": "trixie", "bullseye": "debian"}

# The real success signal. Not "is the package installed" — probe the capability,
# because the two come apart here in a way that is invisible otherwise (see
# verify_import).
PYCSDR_IMPORT = "from pycsdr import modules; modules.version"


def removal_record(repo_root=None):
    """Teardown for the sdr-dsp feature: drop the OASIS-added apt repo. The
    apt-installed packages themselves are left in place, matching remove-oasis's
    leave-apt policy."""
    return {"services": [], "files": [KEYRING, LIST_PATH]}


def suite():
    try:
        with open("/etc/os-release") as fh:
            for line in fh:
                if line.startswith("VERSION_CODENAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def venv_python(repo_root):
    """The interpreter the OASIS server actually runs, which is the only one
    whose view of pycsdr matters."""
    for name in ("python", "python3"):
        p = os.path.join(repo_root, ".venv", "bin", name)
        if os.path.exists(p):
            return p
    return None


def verify_import(python_bin):
    """(ok, detail) — can the SERVER's interpreter import pycsdr?

    THE trap this feature has, and the reason it gets its own step rather than
    trusting apt's exit code. python3-csdr is a dpkg package that installs into
    /usr/lib/python3/dist-packages, and the OASIS venv is created by a bare
    `python3 -m venv`, so include-system-site-packages is false and the venv
    cannot see it. apt reports complete success and `import pycsdr` still fails.

    connector.enable_dist_packages() is what fixes it at runtime, so this check
    runs the import THROUGH that shim — testing the real code path rather than a
    convenient approximation. A second failure mode survives the shim: the C
    extension is built for the system Python (3.11 on bookworm, 3.13 on trixie)
    and will not load into a venv built on a different one. Only the import can
    tell the two apart, which is why this is an import and not a dpkg query."""
    if not python_bin:
        return False, "no OASIS venv found — run scripts/setup-server.py first"
    code = (f"import sys; sys.path.append({connector_dir()!r}); "
            f"import connector; connector.enable_dist_packages(); "
            f"{PYCSDR_IMPORT}; print(modules.version)")
    r = _run([python_bin, "-c", code], check=False, capture_output=True, text=True)
    if r.returncode == 0:
        return True, (r.stdout or "").strip()
    tail = [ln for ln in ((r.stderr or "").splitlines()) if ln.strip()]
    return False, tail[-1] if tail else "import failed with no message"


def connector_dir():
    return os.path.dirname(os.path.abspath(__file__))


def installed():
    """{pkg: version or None} for the DSP packages."""
    return {p: dpkg_installed_version(p) for p in PACKAGES}


def report():
    _step(1, "Installed packages")
    for pkg, ver in installed().items():
        (_ok if ver else _warn)(f"{pkg}: {ver or 'not installed'}")
    _step(2, "Binaries")
    for b in ("rtl_connector", "csdr"):
        p = shutil.which(b)
        (_ok if p else _warn)(f"{b}: {p or 'not on PATH'}")
    _step(3, "Can the OASIS server import pycsdr?")
    ok, detail = verify_import(venv_python(REPO_ROOT))
    if ok:
        _ok(f"yes — pycsdr {detail}. Tracked capture is available.")
    else:
        _warn(f"NO — {detail}")
        _info("Capture will silently fall back to the uncorrected rtl_fm path.")
    return 0 if ok else 1


def add_repo(code):
    repo_path = REPO_PATHS.get(code)
    if not repo_path:
        _warn(f"Unknown suite '{code}' — assuming repo path '{code}'. "
              "If this fails, check https://luarvique.github.io/ppa/.")
        repo_path = code
    _info("Installing the repository signing key ...")
    key_cmd = f"curl -fsSL {KEY_URL} | sudo gpg --batch --yes --dearmor -o {KEYRING}"
    if _run(["bash", "-c", key_cmd], check=False).returncode != 0:
        _fail("Could not install the repo signing key (needs internet + curl).")
    line = f"deb [signed-by={KEYRING}] {BASE_URL}/{repo_path} ./"
    if _run(["bash", "-c", f'echo "{line}" | sudo tee {LIST_PATH}'],
            check=False).returncode != 0:
        _fail("Could not write the apt sources entry.")
    _ok(f"Repository added: {BASE_URL}/{repo_path}")


def run(check_only=False):
    _hr()
    print("  OASIS — satellite DSP stack (Doppler-tracked capture)")
    _hr()
    if platform.system() != "Linux":
        _warn("Linux/Debian only — nothing to do on this platform.")
        return 0
    if check_only:
        return report()
    if not shutil.which("apt-get"):
        _fail("apt-get not found — this installer targets Debian / Raspberry Pi OS.")
    if not has_internet():
        _fail("No internet. These packages come from a third-party apt repo and "
              "are not in the offline bundle; the uncorrected rtl_fm capture path "
              "keeps working without them.")

    code = suite()
    _info(f"Debian suite: {code or 'unknown'}")
    _step(1, "Adding the DSP apt repository")
    add_repo(code)

    _step(2, "Installing the DSP packages")
    if _run(sudo_apt_cmd("update"), check=False).returncode != 0:
        _warn("apt update reported an error — continuing to the install anyway.")
    # openwebrx is deliberately NOT in PACKAGES: it seizes the dongle.
    if _run(sudo_apt_cmd("install", "-y", *PACKAGES), check=False).returncode != 0:
        _fail("apt could not install the DSP packages. See the errors above.")
    for pkg, ver in installed().items():
        (_ok if ver else _warn)(f"{pkg} {ver or 'MISSING'}")

    _step(3, "Verifying the OASIS server can import pycsdr")
    ok, detail = verify_import(venv_python(REPO_ROOT))
    if not ok:
        _warn(f"pycsdr is NOT importable by the OASIS venv: {detail}")
        _info("The packages installed, but the server cannot use them. Tracked")
        _info("capture will silently fall back to uncorrected rtl_fm — which is")
        _info("why this is checked loudly here instead of being discovered as")
        _info("'Doppler never turns on'.")
        _info("Most likely the venv was built on a different Python than the one")
        _info("python3-csdr was compiled for. Rebuild the venv with the system")
        _info("python3 (delete .venv and re-run scripts/setup-server.py).")
        return 1
    _ok(f"pycsdr {detail} is importable — tracked capture is available.")
    _info("Restart the OASIS server to pick it up if it was already running.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Install the csdr/pycsdr/rtl-connector DSP stack for "
                    "Doppler-tracked satellite capture. Online-only, idempotent.")
    ap.add_argument("--check", action="store_true",
                    help="Report what is installed and whether the server can "
                         "import pycsdr; change nothing.")
    opts = ap.parse_args()
    sys.exit(run(check_only=opts.check))


if __name__ == "__main__":
    main()
