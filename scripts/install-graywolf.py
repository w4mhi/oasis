#!/usr/bin/env python3
"""
install-graywolf.py
-------------------
Download and install GrayWolf APRS on Linux (Debian/Ubuntu/Raspberry Pi OS).
Fetches the latest release from GitHub, picks the right .deb for your
architecture, installs it with apt, and enables the systemd service.

GrayWolf runs on port 8080 and provides a browser-based APRS TNC,
iGate, digipeater, and live map. After install, open http://localhost:8080
to complete configuration (callsign, audio device, radio channel).

Usage:
  python3 scripts/install-graywolf.py
  python3 scripts/install-graywolf.py --version 0.13.16   # pin a version

Requires: Linux, apt, sudo, internet access.
Project:  https://github.com/chrissnell/graywolf
"""

import argparse
import os
import platform
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run,
    graywolf_find_local, graywolf_latest_release, graywolf_download_deb,
    deb_field, dpkg_installed_version, version_decision,
)

SERVICE  = "graywolf"
PORT     = 8080
REPO_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFLINE_DIR = os.path.join(REPO_ROOT, "offline-packages", "graywolf")

ARCH_MAP = {
    "aarch64": "arm64",
    "arm64":   "arm64",
    "armv7l":  "armhf",
    "armhf":   "armhf",
    "armv6l":  "armv7l",
    "x86_64":  "amd64",
    "amd64":   "amd64",
}


# ── Step 1: Platform check ─────────────────────────────────────────────────────
def check_platform():
    _step(1, "Checking platform")
    if sys.platform != "linux":
        _fail("GrayWolf Linux packages require a Linux system.\n"
              "     For macOS: download the .tar.gz manually from\n"
              "     https://github.com/chrissnell/graywolf/releases")
    if _run(["which", "apt"], check=False, capture_output=True).returncode != 0:
        _fail("apt not found. This script supports Debian/Ubuntu/Raspberry Pi OS.")

    machine  = platform.machine()
    deb_arch = ARCH_MAP.get(machine)
    _info(f"Architecture: {machine}")
    if not deb_arch:
        _fail(f"No GrayWolf .deb available for architecture \"{machine}\".")
    _ok(f"Architecture -> .deb suffix: {deb_arch}")
    return deb_arch


# ── Step 2: Resolve release ────────────────────────────────────────────────────
def resolve_release(pinned_version, deb_arch):
    _step(2, "Resolving GrayWolf release")
    release = graywolf_latest_release(pinned_version)
    tag     = release["tag_name"]
    version = tag.lstrip("v")
    _ok(f"Release: {tag}")

    want  = f"graywolf_{version}_{deb_arch}.deb"
    asset = next((a for a in release.get("assets", []) if a["name"] == want), None)
    if not asset:
        available = [a["name"] for a in release["assets"] if a["name"].endswith(".deb")]
        _fail(f"Expected asset \"{want}\" not found.\n"
              f"     Available .deb files: {available}")

    _ok(f"Asset: {asset['name']}  ({asset['size'] / 1_048_576:.1f} MB)")
    return asset["browser_download_url"], asset["name"]


# ── Step 3: Install ────────────────────────────────────────────────────────────
def install_deb(deb_path):
    _step(3, "Installing GrayWolf")

    pkg  = deb_field(deb_path, "Package") or SERVICE
    ourv = deb_field(deb_path, "Version")
    inst = dpkg_installed_version(pkg)
    if version_decision(pkg, ourv, inst) == "skip":
        return  # keep the newer/equal version already installed

    _info(f"Running: sudo apt install -y {deb_path}")
    _info("You may be prompted for your sudo password.")
    print()
    result = _run(["sudo", "apt", "install", "-y", deb_path], check=False)
    if result.returncode != 0:
        _fail("apt install failed. Check the output above for details.")
    _ok("GrayWolf installed")


# ── Step 4: Enable service ─────────────────────────────────────────────────────
def enable_service():
    _step(4, "Enabling and starting GrayWolf service")
    _run(["sudo", "systemctl", "daemon-reload"])
    _ok("systemctl daemon-reload")
    _run(["sudo", "systemctl", "enable", SERVICE])
    _ok(f"systemctl enable {SERVICE}")
    _run(["sudo", "systemctl", "restart", SERVICE])
    _ok(f"systemctl restart {SERVICE}")
    result = _run(
        ["sudo", "systemctl", "is-active", SERVICE],
        check=False, capture_output=True, text=True,
    )
    status = result.stdout.strip()
    if status == "active":
        _ok("Service is active")
    else:
        _warn(f"Service status: {status}")
        _info("Check logs with:  journalctl -u graywolf -f")


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Install GrayWolf APRS on Debian/Ubuntu/Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/install-graywolf.py                     # install latest\n"
            "  python3 scripts/install-graywolf.py --version 0.13.16  # pin a version\n"
        ),
    )
    parser.add_argument("--version", metavar="X.Y.Z",
                        help="Install a specific GrayWolf version instead of the latest.")
    args = parser.parse_args()

    print()
    print("  OASIS -- GrayWolf APRS Installer")
    _hr()
    _info("Project: https://github.com/chrissnell/graywolf")
    _info(f"Service port: {PORT}")

    deb_arch  = check_platform()
    local_deb = graywolf_find_local(OFFLINE_DIR, deb_arch)

    # Always resolve the target release from GitHub first so that an outdated
    # offline package does not silently prevent an upgrade.
    with tempfile.TemporaryDirectory() as tmp:
        url, filename = resolve_release(args.version, deb_arch)

        if local_deb and os.path.basename(local_deb) == filename:
            _info(f"Using offline package: {filename} (up to date)")
            install_deb(local_deb)
        else:
            if local_deb:
                _info(f"Offline package {os.path.basename(local_deb)} is outdated — downloading {filename} ...")
            else:
                _info("No offline package found -- downloading from GitHub ...")
                _warn("Run 'python3 scripts/create-oasis-offline.py' to build a bundle with all packages.")
            deb_path = graywolf_download_deb(url, filename, tmp)
            install_deb(deb_path)

    enable_service()

    print()
    print("  OASIS -- GrayWolf install complete.")
    _hr()
    _info(f"Open http://localhost:{PORT} to configure GrayWolf.")
    print()


if __name__ == "__main__":
    main()
