#!/usr/bin/env python3
"""
setup-server.py
---------------
Create the Python virtual environment and install all OASIS server
dependencies. Run this once after cloning, and again any time
scripts/requirements.txt changes.

What this does:
  1. Verifies Python 3.9+
  2. Creates .venv in the repo root (if not already present)
  3. Installs Flask + gunicorn from server/wheels/ (fully offline)
  4. Installs psutil from PyPI (requires internet, ~2 MB)

Usage:
  python3 scripts/setup-server.py
  python3 scripts/setup-server.py --offline   # skip PyPI, wheels only
"""

import argparse
import os
import subprocess
import sys

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR   = os.path.join(REPO_ROOT, ".venv")
WHEELS_DIR = os.path.join(REPO_ROOT, "server", "wheels")
REQ_FILE   = os.path.join(REPO_ROOT, "scripts", "requirements.txt")

# Packages bundled in server/wheels/ (installed offline).
OFFLINE_PKGS = ["flask", "gunicorn"]
# Packages that require PyPI (not vendored).
ONLINE_PKGS  = ["psutil"]

# ── Helpers ────────────────────────────────────────────────────────────────────
def _hr():
    print("─" * 60)

def _step(n, label):
    print(f"\n[{n}] {label}")
    _hr()

def _ok(msg):   print(f"    ✓  {msg}")
def _info(msg): print(f"       {msg}")
def _warn(msg): print(f"    ⚠  {msg}")
def _fail(msg):
    print(f"\n  ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

def _run(cmd, **kwargs):
    """Run a subprocess, streaming output, raising on failure."""
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        _fail(f"Command failed: {' '.join(str(c) for c in cmd)}")
    return result


# ── Step 1: Check Python version ───────────────────────────────────────────────
def check_python():
    _step(1, "Checking Python version")
    v = sys.version_info
    _info(f"Found Python {v.major}.{v.minor}.{v.micro}")
    if v < (3, 9):
        _fail(f"Python 3.9+ required. Install it from https://python.org")
    _ok(f"Python {v.major}.{v.minor} — OK")


# ── Step 2: Create virtual environment ─────────────────────────────────────────
def create_venv():
    _step(2, "Virtual environment")
    if os.path.isdir(VENV_DIR):
        _ok(f".venv already exists — skipping creation")
        return
    _info("Creating .venv ...")
    _run([sys.executable, "-m", "venv", VENV_DIR])
    _ok(f".venv created at {VENV_DIR}")


def _venv_bin(name):
    """Return path to a binary inside .venv."""
    if sys.platform == "win32":
        return os.path.join(VENV_DIR, "Scripts", name + ".exe")
    return os.path.join(VENV_DIR, "bin", name)


# ── Step 3: Install from bundled wheels (offline) ──────────────────────────────
def install_offline():
    _step(3, "Installing Flask + gunicorn from bundled wheels (offline)")
    if not os.path.isdir(WHEELS_DIR):
        _fail(f"Wheels directory not found: {WHEELS_DIR}")

    pip = _venv_bin("pip")
    _run(
        [pip, "install", "--quiet",
         "--no-index", "--find-links", WHEELS_DIR] + OFFLINE_PKGS,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    for pkg in OFFLINE_PKGS:
        _ok(pkg)


# ── Step 4: Install from PyPI (online) ─────────────────────────────────────────
def install_online():
    _step(4, "Installing psutil from PyPI (requires internet)")
    _info("psutil is not vendored — downloading ~2 MB from PyPI.")

    pip = _venv_bin("pip")
    result = subprocess.run(
        [pip, "install", "--quiet"] + ONLINE_PKGS,
        capture_output=True, text=True
    )
    if result.returncode != 0:
        _warn("PyPI install failed. If this Pi has no internet, copy")
        _warn("a psutil wheel for your architecture into server/wheels/ and")
        _warn("re-run with --offline.")
        _info(result.stderr.strip())
        return False

    for pkg in ONLINE_PKGS:
        _ok(pkg)
    return True


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Set up the OASIS Python server environment.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Install from bundled wheels only; skip PyPI (psutil will be missing).",
    )
    args = parser.parse_args()

    print()
    print("  OASIS — Server Setup")
    _hr()

    check_python()
    create_venv()
    install_offline()

    if not args.offline:
        install_online()
    else:
        _warn("--offline: skipping psutil. The APRS stats API will not work")
        _warn("until psutil is installed. See server/wheels/ README for details.")

    python = _venv_bin("python")
    print()
    _hr()
    print("  Setup complete.")
    _info(f"Activate the venv:  source .venv/bin/activate")
    _info(f"Or run directly:    {os.path.relpath(python)} server/app.py")
    _hr()
    print()


if __name__ == "__main__":
    main()
