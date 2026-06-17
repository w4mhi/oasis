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
FCC_INDEX  = os.path.join(REPO_ROOT, "fcc-offline-database", "data", "EN.idx")

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


def _pkg_ok(pkg):
    """Return True if pkg is importable inside the venv."""
    r = subprocess.run(
        [_venv_bin("python"), "-c", f"import {pkg}"],
        capture_output=True,
    )
    return r.returncode == 0


# ── Pre-flight check ───────────────────────────────────────────────────────────
def check_status():
    """
    Print status of all OASIS components.
    Returns (server_ok, fcc_ok) booleans.
    """
    print()
    print("  OASIS — Pre-flight Check")
    _hr()

    server_ok = True

    # venv
    if not os.path.isdir(VENV_DIR):
        print("  ✗  .venv          missing       → python3 scripts/setup-server.py")
        server_ok = False
    else:
        for pkg in OFFLINE_PKGS:
            if _pkg_ok(pkg):
                _ok(f"{pkg:<14} installed")
            else:
                print(f"  ✗  {pkg:<14} not installed  → python3 scripts/setup-server.py")
                server_ok = False
        # psutil is optional — warn but don't block
        if _pkg_ok("psutil"):
            _ok(f"{'psutil':<14} installed")
        else:
            _warn(f"{'psutil':<14} not installed  (APRS stats unavailable)")

    # FCC index
    if os.path.exists(FCC_INDEX):
        kb = os.path.getsize(FCC_INDEX) // 1024
        _ok(f"{'FCC index':<14} {kb:,} KB")
        fcc_ok = True
    else:
        print("  ✗  FCC index      missing       → python3 scripts/setup-fcc-database.py")
        fcc_ok = False

    print()
    return server_ok, fcc_ok


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
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether all components are ready; prompt to fix missing ones.",
    )
    args = parser.parse_args()

    if args.check:
        server_ok, fcc_ok = check_status()

        if server_ok and fcc_ok:
            _ok("All components ready — run start.sh to launch OASIS.")
            print()
            return

        # Prompt for each missing component
        if not server_ok:
            try:
                ans = input("  Server environment not ready. Run setup now? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans == "y":
                _hr()
                check_python()
                create_venv()
                install_offline()
                if not args.offline:
                    install_online()
                _ok("Server environment ready.")
                print()
            else:
                _warn("Skipped. Run:  python3 scripts/setup-server.py")
                print()

        if not fcc_ok:
            try:
                ans = input("  FCC callsign index not built. Run setup now? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans == "y":
                fcc_script = os.path.join(REPO_ROOT, "scripts", "setup-fcc-database.py")
                subprocess.run([sys.executable, fcc_script], check=False)
            else:
                _warn("Skipped. Callsign lookups will return 'not found'.")
                _warn("Run:  python3 scripts/setup-fcc-database.py")
                print()
        return

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
