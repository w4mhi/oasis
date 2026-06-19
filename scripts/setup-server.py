#!/usr/bin/env python3
"""
setup-server.py
---------------
Create the Python virtual environment and install all OASIS server
dependencies. Run this once after cloning, and again any time
scripts/requirements.txt changes.

Install source is chosen automatically — wheels-first:
  • server/wheels/ populated → install from wheels (PyPI as fallback per-package).
  • server/wheels/ empty, internet present → install from PyPI.
  • server/wheels/ empty, no internet → hard fail with instructions.

Dependency installs are best-effort: if a package is missing or fails to
install, the error is logged to the console and setup continues with the rest.

What this does:
  1. Verifies Python 3.9+
  2. Creates .venv in the repo root (if not already present)
  3. Installs the dependencies from scripts/requirements.txt
  4. Installs system emoji + mono fonts (Raspberry Pi / Linux, online only)

Usage:
  python3 scripts/setup-server.py
  python3 scripts/setup-server.py --check     # report component status
"""

import argparse
import os
import re
import subprocess
import sys

# ── Shared library ───────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, has_internet

# ── Paths ──────────────────────────────────────────────────────────
REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR   = os.path.join(REPO_ROOT, ".venv")
WHEELS_DIR = os.path.join(REPO_ROOT, "server", "wheels")
REQ_FILE   = os.path.join(REPO_ROOT, "scripts", "requirements.txt")
FCC_INDEX  = os.path.join(REPO_ROOT, "fcc-offline-database", "data", "EN.idx")

# Modules the server imports — used only by the --check status display.
CORE_MODULES     = ["flask", "gunicorn"]   # required for the server to run
OPTIONAL_MODULES = ["psutil"]              # optional (APRS / system stats)


def _run(cmd, **kwargs):
    """Run a subprocess, streaming output, raising on failure (fatal)."""
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        _fail(f"Command failed: {' '.join(str(c) for c in cmd)}")
    return result

def _log_err(text):
    """Print the tail of a captured stderr so failures are diagnosable."""
    text = (text or "").strip()
    if not text:
        return
    for line in text.splitlines()[-4:]:
        _info(line[:200])


def _pkg_ok(pkg):
    """Return True if pkg is importable inside the venv."""
    r = subprocess.run(
        [_venv_bin("python"), "-c", f"import {pkg}"],
        capture_output=True,
    )
    return r.returncode == 0


def decide_source():
    """
    Decide where packages come from — wheels-first.
    Returns (online, banner) where online=False means use local wheels,
    online=True means use PyPI, and online=None means neither source is
    available (caller should fail).
    """
    wheels_populated = (
        os.path.isdir(WHEELS_DIR)
        and any(f.endswith(".whl") for f in os.listdir(WHEELS_DIR))
    )
    if wheels_populated:
        return False, "Use local wheels — server/wheels/ is populated"
    if has_internet():
        return True, "Use Internet — server/wheels/ is empty"
    return None, "No source — server/wheels/ empty and no internet"


def print_source_banner(online, banner):
    """Print the chosen install source prominently at the top of the log."""
    marker = "📦" if online is False else "🌐"
    print(f"  {marker}  {banner}")
    _hr()
    if online is False:
        _info(f"Installing from bundled wheels in {os.path.relpath(WHEELS_DIR)}/")
        _info("PyPI is used as fallback if a wheel is missing.")
    else:
        _info("Downloading from PyPI; server/wheels/ is empty.")


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
        for pkg in CORE_MODULES:
            if _pkg_ok(pkg):
                _ok(f"{pkg:<14} installed")
            else:
                print(f"  ✗  {pkg:<14} not installed  → python3 scripts/setup-server.py")
                server_ok = False
        # optional modules — warn but don't block
        for pkg in OPTIONAL_MODULES:
            if _pkg_ok(pkg):
                _ok(f"{pkg:<14} installed")
            else:
                _warn(f"{pkg:<14} not installed  (APRS stats unavailable)")

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


# ── Step 3: Install dependencies ───────────────────────────────────────────────
def parse_requirements():
    """Read scripts/requirements.txt into a list of pip requirement specs."""
    specs = []
    try:
        with open(REQ_FILE) as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if line:
                    specs.append(line)
    except OSError as exc:
        _warn(f"Could not read {REQ_FILE}: {exc}")
    return specs


def _req_name(spec):
    """Extract the bare package name from a requirement spec."""
    return re.split(r"[<>=!~\[ ]", spec, maxsplit=1)[0]


class _PipResult:
    """Stand-in for CompletedProcess when pip can't even be launched."""
    def __init__(self, returncode, stderr):
        self.returncode = returncode
        self.stderr = stderr


def _pip(cmd):
    """Run a pip command, never raising — a launch failure becomes a result."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        return _PipResult(1, f"could not launch pip: {exc}")


def _pip_offline(pip, spec):
    return _pip([pip, "install", "--quiet", "--no-index", "--find-links", WHEELS_DIR, spec])


def _pip_online(pip, spec):
    return _pip([pip, "install", "--quiet", spec])


def install_one(pip, spec, online):
    """
    Install a single requirement. Never raises — returns True on success,
    False on failure (after logging the error).
    """
    name = _req_name(spec)

    if online:
        r = _pip_online(pip, spec)
        if r.returncode == 0:
            _ok(f"{name} (from PyPI)")
            return True
        _warn(f"{name}: PyPI install failed — trying bundled wheel")
        r2 = _pip_offline(pip, spec)
        if r2.returncode == 0:
            _ok(f"{name} (from bundled wheel — offline fallback)")
            return True
        _warn(f"{name}: could not install from PyPI or bundled wheels — skipping")
        _log_err(r2.stderr or r.stderr)
        return False

    # Offline: wheels only, no PyPI.
    r = _pip_offline(pip, spec)
    if r.returncode == 0:
        _ok(f"{name} (from bundled wheel — offline)")
        return True
    _warn(f"{name}: no bundled wheel / install failed — skipping")
    _log_err(r.stderr)
    return False


def install_dependencies(online):
    _step(3, "Installing Python dependencies")

    pip   = _venv_bin("pip")
    specs = parse_requirements()
    if not specs:
        _warn("No requirements found — skipping dependency install.")
        return

    wheels_populated = (
        os.path.isdir(WHEELS_DIR)
        and any(f.endswith(".whl") for f in os.listdir(WHEELS_DIR))
    )
    if not wheels_populated and not online:
        _fail(
            "server/wheels/ is empty and no internet is reachable.\n"
            "       Options:\n"
            "         (a) Copy oasis-dist/server/wheels/ here, or\n"
            "         (b) Connect to the internet and re-run."
        )

    installed, failed, skipped = [], [], []
    for spec in specs:
        name = _req_name(spec)
        # gunicorn is POSIX-only; the dev server is used on Windows.
        if name.lower() == "gunicorn" and sys.platform == "win32":
            _warn(f"{name}: POSIX-only — skipped on Windows")
            skipped.append(name)
            continue
        (installed if install_one(pip, spec, online) else failed).append(name)

    print()
    if installed:
        _ok(f"Installed: {', '.join(installed)}")
    if skipped:
        _info(f"Skipped:   {', '.join(skipped)}")
    if failed:
        _warn(f"Failed (logged, continuing): {', '.join(failed)}")
        _warn("The server may have reduced functionality until these are installed.")


# ── Step 4: Install system emoji + mono fonts (Linux / Raspberry Pi only) ──────
def install_system_fonts(online):
    _step(4, "System fonts (Raspberry Pi / Linux)")

    if not _cmd_exists("apt-get"):
        _warn("apt-get not found — install fonts manually:")
        _warn("  sudo apt-get install -y fonts-noto-color-emoji fonts-noto-mono")
        return

    packages = ["fonts-noto-color-emoji", "fonts-noto-mono"]
    missing = [p for p in packages if not _apt_installed(p)]

    if not missing:
        for p in packages:
            _ok(f"{p} already installed")
        return

    if not online:
        _warn("Offline mode — skipping apt font download.")
        _warn(f"  When online: sudo apt-get install -y {' '.join(missing)}")
        return

    _info(f"Installing: {', '.join(missing)}")
    result = subprocess.run(
        ["sudo", "apt-get", "install", "-y"] + missing,
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        for p in missing:
            _ok(p)
    else:
        _warn("apt-get failed — try manually:")
        _warn(f"  sudo apt-get install -y {' '.join(missing)}")
        _log_err(result.stderr)


def _cmd_exists(name):
    import shutil
    return shutil.which(name) is not None


def _apt_installed(pkg):
    r = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", pkg],
        capture_output=True, text=True,
    )
    return "install ok installed" in r.stdout


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Set up the OASIS Python server environment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/setup-server.py         # local wheels if present, else PyPI\n"
            "  python3 scripts/setup-server.py --check # report what is installed / missing\n"
        ),
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
                online, banner = decide_source()
                print_source_banner(online, banner)
                check_python()
                create_venv()
                install_dependencies(online)
                _ok("Server environment setup finished (see log above).")
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

    online, banner = decide_source()

    if online is None:
        _fail(
            "server/wheels/ is empty and no internet is reachable.\n"
            "       Options:\n"
            "         (a) Copy oasis-dist/server/wheels/ to server/wheels/, or\n"
            "         (b) Connect to the internet and re-run."
        )

    print()
    print("  OASIS — Server Setup")
    _hr()
    print_source_banner(online, banner)

    check_python()
    create_venv()
    install_dependencies(online)
    if sys.platform == "linux":
        install_system_fonts(online)

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
