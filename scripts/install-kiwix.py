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
import urllib.request

DOWNLOAD_BASE = "https://download.kiwix.org/release/kiwix-tools"
DEFAULT_VERSION = "3.8.2"
INSTALL_BIN   = "/usr/local/bin/kiwix-serve"
SERVICE_NAME  = "kiwix"
PORT          = 8081
DEFAULT_ZIM_DIR = os.path.expanduser("~/zim")
SERVICE_FILE  = f"/etc/systemd/system/{SERVICE_NAME}.service"

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
        _fail("This script installs the Linux build of kiwix-serve.\n"
              "       For macOS: brew install kiwix (via Homebrew)")

    machine = platform.machine()
    _info(f"Architecture: {machine}")

    # kiwix-tools naming: linux-ARCH where ARCH is one of:
    # aarch64, armhf, armv6, i586, x86_64
    arch_map = {
        "aarch64": "aarch64",  # Pi Zero 2W, Pi 3/4/5 (64-bit OS)
        "arm64":   "aarch64",
        "armv7l":  "armhf",    # Pi 2/3/4 (32-bit OS)
        "armhf":   "armhf",
        "armv6l":  "armv6",    # Pi 1 / Pi Zero W
        "i686":    "i586",
        "i386":    "i586",
        "x86_64":  "x86_64",
        "amd64":   "x86_64",
    }
    kiwix_arch = arch_map.get(machine)
    if not kiwix_arch:
        _fail(f"No kiwix-tools build for architecture '{machine}'.\n"
              "       Check https://download.kiwix.org/release/kiwix-tools/")

    _ok(f"Architecture → kiwix suffix: linux-{kiwix_arch}")
    return kiwix_arch


# ── Step 2: Download kiwix-tools ──────────────────────────────────────────────
def download_kiwix(version, kiwix_arch):
    _step(2, "Downloading kiwix-tools")

    filename = f"kiwix-tools_linux-{kiwix_arch}-{version}.tar.gz"
    url = f"{DOWNLOAD_BASE}/{filename}"
    _info(f"Version: {version}")
    _info(f"Source:  {url}")

    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            prog  = _Progress(total)
            buf   = io.BytesIO()
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                buf.write(chunk)
                prog.update(len(chunk))
            prog.done()
            return buf.getvalue()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            _fail(f"Not found: {url}\n"
                  "       Check available versions at:\n"
                  f"       {DOWNLOAD_BASE}/")
        _fail(f"Download failed (HTTP {exc.code}): {exc}")
    except Exception as exc:
        _fail(f"Download failed: {exc}")


# ── Step 3: Extract and install kiwix-serve ───────────────────────────────────
def install_kiwix_serve(data):
    _step(3, "Installing kiwix-serve to /usr/local/bin/")

    _info("Extracting kiwix-serve from archive ...")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            serve_member = next(
                (m for m in tf.getmembers() if m.name.endswith("kiwix-serve")),
                None
            )
            if not serve_member:
                members = [m.name for m in tf.getmembers()]
                _fail(f"kiwix-serve not found in archive. Contents: {members[:10]}")

            serve_member.name = "kiwix-serve"  # strip path prefix
            tf.extract(serve_member, path="/tmp")
    except Exception as exc:
        _fail(f"Extraction failed: {exc}")

    _run(["sudo", "install", "-m", "755", "/tmp/kiwix-serve", INSTALL_BIN])
    _ok(f"kiwix-serve installed → {INSTALL_BIN}")

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
    # Write service file via sudo tee.
    proc = subprocess.Popen(
        ["sudo", "tee", SERVICE_FILE],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL
    )
    proc.communicate(service_content.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {SERVICE_FILE}")

    _ok(f"Service file: {SERVICE_FILE}")

    _run(["sudo", "systemctl", "daemon-reload"])
    _ok("systemctl daemon-reload")

    # Enable but don't start yet — no ZIM file exists until download-wikipedia.py runs.
    _run(["sudo", "systemctl", "enable", SERVICE_NAME])
    _ok(f"systemctl enable {SERVICE_NAME}")
    _warn("Service not started yet — no ZIM content downloaded.")
    _info("Run scripts/download-wikipedia.py, then:")
    _info(f"  sudo systemctl start {SERVICE_NAME}")


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Install kiwix-serve for offline Wikipedia/reference content.",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        metavar="X.Y.Z",
        help=f"kiwix-tools version to install (default: {DEFAULT_VERSION}).",
    )
    parser.add_argument(
        "--zim-dir",
        default=DEFAULT_ZIM_DIR,
        metavar="PATH",
        help=f"Directory where ZIM files will be stored (default: {DEFAULT_ZIM_DIR}).",
    )
    args = parser.parse_args()

    print()
    print("  OASIS — Kiwix Installer")
    _hr()
    _info("Provides offline Wikipedia, manuals, and reference content.")
    _info(f"Service port: {PORT}")

    kiwix_arch = check_platform()
    data = download_kiwix(args.version, kiwix_arch)
    install_kiwix_serve(data)
    create_service(os.path.expanduser(args.zim_dir))

    print()
    _hr()
    print("  Kiwix installed.")
    _info("Next step: download content")
    _info("  python3 scripts/download-wikipedia.py")
    _info("")
    _info("Then start the service:")
    _info(f"  sudo systemctl start {SERVICE_NAME}")
    _info(f"  Open http://localhost:{PORT}")
    _hr()
    print()


if __name__ == "__main__":
    main()
