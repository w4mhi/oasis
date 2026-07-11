#!/usr/bin/env python3
"""
common/graywolf.py
------------------
Install logic for GrayWolf APRS (TNC / iGate / digipeater).

Public API
----------
  check_platform()              -> deb_arch str
  resolve_release(pinned, arch) -> (url, filename)
  install_deb(deb_path)
  enable_service()
  enable_api_service()
  run(args)                     # entry point for the thin CLI wrapper
"""

import os
import platform
import sys
import tempfile

# ── Shared helpers ───────────────────────────────────────────────────────────
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS_DIR)

from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run,
    graywolf_find_local, graywolf_latest_release, graywolf_download_deb,
    deb_field, dpkg_installed_version, version_decision,
)
from common import manifest as M

# ── Feature metadata from manifest ──────────────────────────────────────────────
def _feature():
    return M.get_feature("graywolf")


def _offline_dir(repo_root):
    """Bundle dir for graywolf (non-apt, no suite)."""
    return M.bundle_dir(os.path.join(repo_root, "offline-packages"), "graywolf")


SERVICE  = "graywolf"
PORT     = 8080

# GrayWolf APRS History API enabler — a sibling script, not duplicated here.
API_SERVICE = "graywolf-api"
API_PORT    = 8085

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
    """Resolve the target release from GitHub. Returns (url, filename)."""
    _step(2, "Resolving GrayWolf release")

    feat    = _feature()
    repo    = feat.get("repo", "chrissnell/graywolf")
    pattern = feat.get("asset_pattern", "graywolf_{version}_{arch}.deb")

    release = graywolf_latest_release(pinned_version)
    tag     = release["tag_name"]
    version = tag.lstrip("v")
    _ok(f"Release: {tag}")

    want  = pattern.format(version=version, arch=deb_arch)
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


# ── Step 5: GrayWolf APRS History API service ──────────────────────────────────
def enable_api_service(repo_root):
    """Enable the APRS History API by delegating to its own enabler script."""
    _step(5, f"Enabling the GrayWolf APRS History API (port {API_PORT})")
    api_enabler = os.path.join(repo_root, "scripts", "enable-graywolf-api.py")
    if not os.path.exists(api_enabler):
        _warn(f"{api_enabler} not found — skipping API service.")
        return
    _run([sys.executable, api_enabler], check=False)


# ── Main entry point ───────────────────────────────────────────────────────────
def run(pinned_version=None, repo_root=None):
    """Full install sequence. Called by the thin CLI wrapper."""
    if repo_root is None:
        repo_root = _SCRIPTS_DIR

    print()
    print("  OASIS -- GrayWolf APRS Installer")
    _hr()
    _info("Project: https://github.com/chrissnell/graywolf")
    _info(f"Service port: {PORT}")

    offline_dir = _offline_dir(repo_root)
    deb_arch    = check_platform()
    local_deb   = graywolf_find_local(offline_dir, deb_arch)

    # Always resolve the target release from GitHub first so that an outdated
    # offline package does not silently prevent an upgrade.
    with tempfile.TemporaryDirectory() as tmp:
        url, filename = resolve_release(pinned_version, deb_arch)

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
    enable_api_service(repo_root)

    print()
    print("  OASIS -- GrayWolf install complete.")
    _hr()
    _info(f"Open http://localhost:{PORT} to configure GrayWolf.")
    _info(f"APRS History API + system stats serve on :{API_PORT} (graywolf-api.service).")
    print()
