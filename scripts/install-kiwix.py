#!/usr/bin/env python3
"""
install-kiwix.py
----------------
Download and install kiwix-serve on Linux. Kiwix provides offline access
to Wikipedia, OpenStreetMap, Project Gutenberg, and other reference content
through a local web server on port 8081.

What this does:
  1. Detects your architecture
  2. Downloads kiwix-tools (contains kiwix-serve) from download.kiwix.org
  3. Installs kiwix-serve to /usr/local/bin/
  4. Creates and enables a systemd service on port 8081

After install, run scripts/download-wikipedia.py to get content.

Usage:
  python3 scripts/install-kiwix.py
  python3 scripts/install-kiwix.py --version 3.8.2   # pin a version
  python3 scripts/install-kiwix.py --zim-dir /mnt/ssd/zim

Requires: Linux, sudo, internet access (~5 MB download).
"""

import argparse
import io
import os
import platform
import subprocess
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run,
    kiwix_find_local, kiwix_latest_version, kiwix_download_tarball,
    binary_version, version_decision,
)

INSTALL_BIN     = "/usr/local/bin/kiwix-serve"
SERVICE_NAME    = "kiwix"
PORT            = 8081
DEFAULT_VERSION = "3.8.2"
DEFAULT_ZIM_DIR = os.path.expanduser("~/zim")
SERVICE_FILE    = f"/etc/systemd/system/{SERVICE_NAME}.service"
REPO_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFLINE_DIR     = os.path.join(REPO_ROOT, "offline-packages", "kiwix")

ARCH_MAP = {
    "aarch64": "aarch64",
    "arm64":   "aarch64",
    "armv7l":  "armhf",
    "armhf":   "armhf",
    "armv6l":  "armv6",
    "i686":    "i586",
    "i386":    "i586",
    "x86_64":  "x86_64",
    "amd64":   "x86_64",
}


# ── Step 1: Platform check ─────────────────────────────────────────────────────
def check_platform():
    _step(1, "Checking platform")
    if sys.platform != "linux":
        _fail("This script installs the Linux build of kiwix-serve.\n"
              "     For macOS: brew install kiwix (via Homebrew)")

    machine    = platform.machine()
    kiwix_arch = ARCH_MAP.get(machine)
    _info(f"Architecture: {machine}")
    if not kiwix_arch:
        _fail(f"No kiwix-tools build for architecture \"{machine}\".")
    _ok(f"Architecture -> kiwix suffix: linux-{kiwix_arch}")
    return kiwix_arch


# ── Step 2: Resolve local or download ─────────────────────────────────────────
def get_tarball(version, kiwix_arch):
    _step(2, "Locating kiwix-tools tarball")
    local = kiwix_find_local(OFFLINE_DIR, kiwix_arch)
    if local:
        _info(f"Using offline package: {os.path.basename(local)}")
        with open(local, "rb") as fh:
            return fh.read()
    _info("No offline package found -- downloading from kiwix.org ...")
    _warn("Run 'python3 scripts/create-offline-dist.py' to build a bundle with all packages.")
    tarball_path = kiwix_download_tarball(
        os.path.join(REPO_ROOT, "offline-packages", "kiwix"),
        version, kiwix_arch,
    )
    with open(tarball_path, "rb") as fh:
        return fh.read()


# ── Step 3: Extract and install kiwix-serve ───────────────────────────────────
def install_kiwix_serve(data, version):
    _step(3, "Installing kiwix-serve to /usr/local/bin/")

    inst = binary_version(["kiwix-serve", "--version"])
    if version_decision("kiwix-serve", version, inst) == "skip":
        return  # keep the newer/equal kiwix-serve already installed

    _info("Extracting kiwix-serve from archive ...")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            member = next(
                (m for m in tf.getmembers() if m.name.endswith("kiwix-serve")), None
            )
            if not member:
                _fail(f"kiwix-serve not found in archive.")
            member.name = "kiwix-serve"
            tf.extract(member, path="/tmp")
    except Exception as exc:
        _fail(f"Extraction failed: {exc}")

    _run(["sudo", "install", "-m", "755", "/tmp/kiwix-serve", INSTALL_BIN])
    _ok(f"kiwix-serve installed -> {INSTALL_BIN}")

    result = _run([INSTALL_BIN, "--version"], capture_output=True, text=True, check=False)
    ver = result.stdout.strip() or result.stderr.strip()
    if ver:
        _ok(f"Version: {ver.splitlines()[0]}")


# ── Step 4: Create systemd service ────────────────────────────────────────────
def create_service(zim_dir):
    _step(4, "Creating systemd service")
    os.makedirs(zim_dir, exist_ok=True)
    _info(f"ZIM directory: {zim_dir}")

    service_content = f"""[Unit]
Description=Kiwix offline reader (OASIS)
After=network.target

[Service]
Type=simple
ExecStart={INSTALL_BIN} --port {PORT} --library {zim_dir}/library.xml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    proc = subprocess.Popen(
        ["sudo", "tee", SERVICE_FILE],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    proc.communicate(service_content.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {SERVICE_FILE}")

    _ok(f"Service file: {SERVICE_FILE}")
    _run(["sudo", "systemctl", "daemon-reload"])
    _ok("systemctl daemon-reload")
    _run(["sudo", "systemctl", "enable", SERVICE_NAME])
    _ok(f"systemctl enable {SERVICE_NAME}")
    _warn("Service not started yet -- no ZIM content downloaded.")
    _info("Run scripts/download-wikipedia.py, then:")
    _info(f"  sudo systemctl start {SERVICE_NAME}")


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Install kiwix-serve for offline Wikipedia/reference content.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/install-kiwix.py                        # install latest\n"
            "  python3 scripts/install-kiwix.py --version 3.8.2       # pin a version\n"
            "  python3 scripts/install-kiwix.py --zim-dir /mnt/ssd/zim  # custom ZIM dir\n"
        ),
    )
    parser.add_argument("--version", default=DEFAULT_VERSION, metavar="X.Y.Z",
                        help=f"kiwix-tools version to install (default: {DEFAULT_VERSION}).")
    parser.add_argument("--zim-dir", default=DEFAULT_ZIM_DIR, metavar="PATH",
                        help="Directory where ZIM files will be stored (default: ~/zim).")
    args = parser.parse_args()

    print()
    print("  OASIS -- Kiwix Installer")
    _hr()

    kiwix_arch = check_platform()
    data       = get_tarball(args.version, kiwix_arch)
    install_kiwix_serve(data, args.version)
    create_service(os.path.expanduser(args.zim_dir))

    print()
    print("  OASIS -- Kiwix install complete.")
    _hr()
    _info(f"Service: {SERVICE_NAME}  (port {PORT})")
    _info("Get content:  python3 scripts/download-wikipedia.py")
    print()


if __name__ == "__main__":
    main()
