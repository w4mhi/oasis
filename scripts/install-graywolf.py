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
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import urllib.request

GITHUB_API  = "https://api.github.com/repos/chrissnell/graywolf/releases/latest"
SERVICE     = "graywolf"
PORT        = 8080

# ── Helpers ────────────────────────────────────────────────────────────────────
def _hr():   print("─" * 60)
def _step(n, label):
    print(f"\n[{n}] {label}")
    _hr()
def _ok(msg):   print(f"    ✓  {msg}")
def _info(msg): print(f"       {msg}")
def _warn(msg): print(f"    ⚠  {msg}")
def _fail(msg):
    print(f"\n  ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

def _run(cmd, check=True, **kwargs):
    return subprocess.run(cmd, check=check, **kwargs)

class _Progress:
    def __init__(self, total):
        self.total = total
        self.received = 0

    def update(self, chunk):
        self.received += chunk
        pct = (self.received / self.total * 100) if self.total else 0
        mb  = self.received / 1_048_576
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"\r    {bar} {pct:5.1f}%  {mb:.1f} MB", end="", flush=True)

    def done(self):
        mb = self.received / 1_048_576
        print(f"\r    {'█' * 50} 100.0%  {mb:.1f} MB")


# ── Step 1: Platform check ─────────────────────────────────────────────────────
def check_platform():
    _step(1, "Checking platform")

    if sys.platform != "linux":
        _fail("GrayWolf Linux packages require a Linux system.\n"
              "       For macOS: download the .tar.gz manually from\n"
              "       https://github.com/chrissnell/graywolf/releases")

    if not _run(["which", "apt"], check=False, capture_output=True).returncode == 0:
        _fail("apt not found. This script supports Debian/Ubuntu/Raspberry Pi OS.\n"
              "       For RPM-based systems, use the .rpm from\n"
              "       https://github.com/chrissnell/graywolf/releases")

    machine = platform.machine()
    _info(f"Architecture: {machine}")

    arch_map = {
        "aarch64": "arm64",   # Pi Zero 2W, Pi 3/4/5 (64-bit OS)
        "arm64":   "arm64",
        "armv7l":  "armhf",   # Pi 2/3/4 (32-bit OS)
        "armhf":   "armhf",
        "armv6l":  "armv7l",  # Pi 1 / Pi Zero — closest available deb
        "x86_64":  "amd64",
        "amd64":   "amd64",
    }
    deb_arch = arch_map.get(machine)
    if not deb_arch:
        _fail(f"No GrayWolf .deb available for architecture '{machine}'.\n"
              "       Check https://github.com/chrissnell/graywolf/releases")

    _ok(f"Architecture → .deb suffix: {deb_arch}")
    return deb_arch


# ── Step 2: Resolve release ────────────────────────────────────────────────────
def resolve_release(pinned_version, deb_arch):
    _step(2, "Resolving GrayWolf release")

    if pinned_version:
        tag = f"v{pinned_version.lstrip('v')}"
        url = f"https://api.github.com/repos/chrissnell/graywolf/releases/tags/{tag}"
        _info(f"Pinned version: {tag}")
    else:
        url = GITHUB_API
        _info("Fetching latest release from GitHub ...")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "oasis-emcomm"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            release = json.load(resp)
    except Exception as exc:
        _fail(f"Could not reach GitHub API: {exc}")

    tag     = release["tag_name"]          # e.g. "v0.13.16"
    version = tag.lstrip("v")              # e.g. "0.13.16"
    _ok(f"Release: {tag}")

    # Find the .deb asset for this architecture.
    want = f"graywolf_{version}_{deb_arch}.deb"
    asset = next(
        (a for a in release.get("assets", []) if a["name"] == want),
        None
    )
    if not asset:
        available = [a["name"] for a in release["assets"] if a["name"].endswith(".deb")]
        _fail(f"Expected asset '{want}' not found.\n"
              f"       Available .deb files: {available}")

    _ok(f"Asset: {asset['name']}  ({asset['size'] / 1_048_576:.1f} MB)")
    return asset["browser_download_url"], asset["name"]


# ── Step 3: Download ───────────────────────────────────────────────────────────
def download_deb(url, filename, dest_dir):
    _step(3, f"Downloading {filename}")
    _info(f"Source: {url}")

    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            prog  = _Progress(total)
            data  = bytearray()
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                data += chunk
                prog.update(len(chunk))
            prog.done()
    except Exception as exc:
        _fail(f"Download failed: {exc}")

    path = os.path.join(dest_dir, filename)
    with open(path, "wb") as fh:
        fh.write(data)
    _ok(f"Saved to {path}")
    return path


# ── Step 4: Install ────────────────────────────────────────────────────────────
def install_deb(deb_path):
    _step(4, "Installing GrayWolf")
    _info("Running: sudo apt install -y " + deb_path)
    _info("You may be prompted for your sudo password.")
    print()

    result = _run(["sudo", "apt", "install", "-y", deb_path], check=False)
    if result.returncode != 0:
        _fail("apt install failed. Check the output above for details.")

    _ok("GrayWolf installed")


# ── Step 5: Enable service ─────────────────────────────────────────────────────
def enable_service():
    _step(5, "Enabling and starting GrayWolf service")

    _run(["sudo", "systemctl", "enable", "--now", SERVICE])
    _ok(f"systemctl enable --now {SERVICE}")

    # Give it a moment, then check status.
    result = _run(
        ["sudo", "systemctl", "is-active", SERVICE],
        check=False, capture_output=True, text=True
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
    )
    parser.add_argument(
        "--version",
        metavar="X.Y.Z",
        help="Install a specific GrayWolf version instead of the latest.",
    )
    args = parser.parse_args()

    print()
    print("  OASIS — GrayWolf APRS Installer")
    _hr()
    _info("Project: https://github.com/chrissnell/graywolf")
    _info(f"Service port: {PORT}")

    deb_arch = check_platform()

    with tempfile.TemporaryDirectory() as tmp:
        url, filename = resolve_release(args.version, deb_arch)
        deb_path = download_deb(url, filename, tmp)
        install_deb(deb_path)

    enable_service()

    print()
    _hr()
    print("  GrayWolf installed and running.")
    _info(f"Open http://localhost:{PORT} to complete setup:")
    _info("  1. Set your station callsign")
    _info("  2. Configure audio device and radio channel")
    _info("  3. Enable iGate, digipeater, or beacon as needed")
    _info("")
    _info("Service management:")
    _info("  sudo systemctl status graywolf")
    _info("  sudo systemctl restart graywolf")
    _info("  journalctl -u graywolf -f")
    _hr()
    print()


if __name__ == "__main__":
    main()
