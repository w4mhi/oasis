#!/usr/bin/env python3
"""
common/server.py
----------------
Virtual-environment setup logic for the OASIS Flask server.

Public API
----------
  decide_source(wheels_dir)        -> (online: bool|None, banner: str)
  print_source_banner(online, banner)
  check_status(venv_dir, fcc_index) -> (server_ok, fcc_ok)
  check_python()
  create_venv(venv_dir)
  install_dependencies(online, venv_dir, wheels_dir)
  install_sqlite3(online)
  install_system_fonts(online)
  run(check_mode, repo_root)       # entry point for the thin CLI wrapper
"""

import os
import re
import subprocess
import sys

# ── Shared helpers ───────────────────────────────────────────────────────────
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS_DIR)

from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, has_internet, sudo_apt_cmd
from common import manifest as M

# Modules the server imports — used only by the --check status display.
CORE_MODULES     = ["flask", "gunicorn"]   # required for the server to run
OPTIONAL_MODULES = ["psutil"]              # optional (APRS / system stats)


def _pkg_ok(venv_dir, pkg):
    """Return True if pkg is importable inside the venv."""
    r = subprocess.run(
        [_venv_bin(venv_dir, "python"), "-c", f"import {pkg}"],
        capture_output=True,
    )
    return r.returncode == 0


def decide_source(wheels_dir):
    """
    Decide where packages come from — wheels-first.
    Returns (online, banner) where online=False means use local wheels,
    online=True means use PyPI, and online=None means neither source is
    available (caller should fail).
    """
    wheels_populated = (
        os.path.isdir(wheels_dir)
        and any(f.endswith(".whl") for f in os.listdir(wheels_dir))
    )
    if wheels_populated:
        return False, "Use local wheels — server/wheels/ is populated"
    if has_internet():
        return True, "Use Internet — server/wheels/ is empty"
    return None, "No source — server/wheels/ empty and no internet"


def print_source_banner(online, banner, wheels_dir):
    """Print the chosen install source prominently at the top of the log."""
    marker = "[offline]" if online is False else "[online]"
    print(f"  {marker}  {banner}")
    _hr()
    if online is False:
        _info(f"Installing from bundled wheels in {os.path.relpath(wheels_dir)}/")
        _info("PyPI is used as fallback if a wheel is missing.")
    else:
        _info("Downloading from PyPI; server/wheels/ is empty.")


# ── Pre-flight check ───────────────────────────────────────────────────────────
def check_status(venv_dir, fcc_index):
    """
    Print status of all OASIS components.
    Returns (server_ok, fcc_ok) booleans.
    """
    print()
    print("  OASIS — Pre-flight Check")
    _hr()

    server_ok = True

    # venv
    if not os.path.isdir(venv_dir):
        print("  ✗  .venv          missing       -> python3 scripts/setup-server.py")
        server_ok = False
    else:
        for pkg in CORE_MODULES:
            if _pkg_ok(venv_dir, pkg):
                _ok(f"{pkg:<14} installed")
            else:
                print(f"  ✗  {pkg:<14} not installed  -> python3 scripts/setup-server.py")
                server_ok = False
        # optional modules — warn but don't block
        for pkg in OPTIONAL_MODULES:
            if _pkg_ok(venv_dir, pkg):
                _ok(f"{pkg:<14} installed")
            else:
                _warn(f"{pkg:<14} not installed  (APRS stats unavailable)")

    # FCC index
    if os.path.exists(fcc_index):
        kb = os.path.getsize(fcc_index) // 1024
        _ok(f"{'FCC index':<14} {kb:,} KB")
        fcc_ok = True
    else:
        print("  ✗  FCC index      missing       -> python3 services/fcc_database/install.py")
        fcc_ok = False

    print()
    return server_ok, fcc_ok


# ── Step 1: Check Python version ───────────────────────────────────────────────
def check_python():
    _step(1, "Checking Python version")
    v = sys.version_info
    _info(f"Found Python {v.major}.{v.minor}.{v.micro}")
    if v < (3, 9):
        _fail("Python 3.9+ required. Install it from https://python.org")
    _ok(f"Python {v.major}.{v.minor} — OK")


# ── Step 2: Create virtual environment ─────────────────────────────────────────
def create_venv(venv_dir):
    _step(2, "Virtual environment")
    if os.path.isdir(venv_dir):
        _ok(".venv already exists — skipping creation")
        return
    _info("Creating .venv ...")
    subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
    _ok(f".venv created at {venv_dir}")


def _venv_bin(venv_dir, name):
    """Return path to a binary inside .venv."""
    if sys.platform == "win32":
        return os.path.join(venv_dir, "Scripts", name + ".exe")
    return os.path.join(venv_dir, "bin", name)


# ── Step 3: Install dependencies ───────────────────────────────────────────────
def _packages_from_manifest(feature="server"):
    """Read a pypi feature's package list from the manifest.

    Returns a list of pip requirement spec strings, e.g. ["Flask==3.1.3", ...].
    `feature` names the manifest pypi group (default "server"; the satellites
    installer passes "satellites"). Falls back to an empty list if the manifest
    is unavailable.
    """
    try:
        pkgs = M.pypi_packages(feature)
        specs = []
        for p in pkgs:
            name = p.get("name", "")
            ver  = p.get("version", "")
            if not name:
                continue
            if ver:
                # Append version spec only when it looks like a bare version
                # (no operator) — otherwise use the spec verbatim (e.g. >=23,<27).
                if re.match(r"^\d", ver):
                    specs.append(f"{name}=={ver}")
                else:
                    specs.append(f"{name}{ver}")
            else:
                specs.append(name)
        return specs
    except Exception as exc:
        _warn(f"Could not read {feature} packages from manifest: {exc}")
        return []


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


def _pip_offline(pip, spec, wheels_dir):
    return _pip([pip, "install", "--quiet", "--no-index", "--find-links", wheels_dir, spec])


def _pip_online(pip, spec):
    return _pip([pip, "install", "--quiet", spec])


def _log_err(text):
    """Print the tail of a captured stderr so failures are diagnosable."""
    text = (text or "").strip()
    if not text:
        return
    for line in text.splitlines()[-4:]:
        _info(line[:200])


def install_one(pip, spec, online, wheels_dir):
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
        r2 = _pip_offline(pip, spec, wheels_dir)
        if r2.returncode == 0:
            _ok(f"{name} (from bundled wheel — offline fallback)")
            return True
        _warn(f"{name}: could not install from PyPI or bundled wheels — skipping")
        _log_err(r2.stderr or r.stderr)
        return False

    # Offline: wheels only, no PyPI.
    r = _pip_offline(pip, spec, wheels_dir)
    if r.returncode == 0:
        _ok(f"{name} (from bundled wheel — offline)")
        return True
    _warn(f"{name}: no bundled wheel / install failed — skipping")
    _log_err(r.stderr)
    return False


def install_dependencies(online, venv_dir, wheels_dir):
    _step(3, "Installing Python dependencies")

    pip   = _venv_bin(venv_dir, "pip")
    specs = _packages_from_manifest()
    if not specs:
        _warn("No requirements found in manifest — skipping dependency install.")
        return

    wheels_populated = (
        os.path.isdir(wheels_dir)
        and any(f.endswith(".whl") for f in os.listdir(wheels_dir))
    )
    if not wheels_populated and not online:
        _fail(
            "server/wheels/ is empty and no internet is reachable.\n"
            "       Options:\n"
            "         (a) Copy oasis-offline/server/wheels/ here, or\n"
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
        (installed if install_one(pip, spec, online, wheels_dir) else failed).append(name)

    print()
    if installed:
        _ok(f"Installed: {', '.join(installed)}")
    if skipped:
        _info(f"Skipped:   {', '.join(skipped)}")
    if failed:
        _warn(f"Failed (logged, continuing): {', '.join(failed)}")
        _warn("The server may have reduced functionality until these are installed.")


# ── Step 4: Ensure python3-sqlite3 is available ────────────────────────────────
def install_sqlite3(online, venv_dir):
    _step(4, "python3-sqlite3 (Raspberry Pi / Linux)")

    if not _cmd_exists("apt-get"):
        _warn("apt-get not found — install sqlite3 support manually:")
        _warn("  sudo apt-get install -y python3-sqlite3")
        return

    # Fast check: can the venv python actually import sqlite3?
    try:
        venv_py = _venv_bin(venv_dir, "python")
        r = subprocess.run([venv_py, "-c", "import sqlite3"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            _ok("python3-sqlite3 available")
            return
    except Exception:
        pass

    if not _apt_installed("python3-sqlite3"):
        if not online:
            _warn("Offline mode — cannot install python3-sqlite3 via apt.")
            _warn("  When online: sudo apt-get install -y python3-sqlite3")
            return
        _info("Installing python3-sqlite3 ...")
        result = subprocess.run(
            sudo_apt_cmd("apt-get", "install", "-y", "python3-sqlite3"),
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            _ok("python3-sqlite3 installed")
        else:
            _warn("apt-get failed — try manually:")
            _warn("  sudo apt-get install -y python3-sqlite3")
            for line in (result.stderr or "").strip().splitlines()[-4:]:
                _info(line[:200])
    else:
        _ok("python3-sqlite3 already installed")


# ── Step 5: Install system emoji + mono fonts ──────────────────────────────────
def install_system_fonts(online):
    _step(5, "System fonts (Raspberry Pi / Linux)")

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
        sudo_apt_cmd("apt-get", "install", "-y", *missing),
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        for p in missing:
            _ok(p)
    else:
        _warn("apt-get failed — try manually:")
        _warn(f"  sudo apt-get install -y {' '.join(missing)}")
        for line in (result.stderr or "").strip().splitlines()[-4:]:
            _info(line[:200])


def _cmd_exists(name):
    import shutil
    return shutil.which(name) is not None


def _apt_installed(pkg):
    r = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", pkg],
        capture_output=True, text=True,
    )
    return "install ok installed" in r.stdout


# ── Main entry point ───────────────────────────────────────────────────────────
def run(check_mode=False, repo_root=None):
    """Full setup sequence. Called by the thin CLI wrapper."""
    if repo_root is None:
        repo_root = _SCRIPTS_DIR

    venv_dir   = os.path.join(repo_root, ".venv")
    wheels_dir = os.path.join(repo_root, "server", "wheels")
    fcc_index  = os.path.join(repo_root, "services", "fcc_database", "data", "EN.idx")

    if check_mode:
        server_ok, fcc_ok = check_status(venv_dir, fcc_index)

        if server_ok and fcc_ok:
            _ok("All components ready — run ./scripts/start-server.sh to launch OASIS.")
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
                online, banner = decide_source(wheels_dir)
                print_source_banner(online, banner, wheels_dir)
                check_python()
                create_venv(venv_dir)
                install_dependencies(online, venv_dir, wheels_dir)
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
                fcc_script = os.path.join(repo_root, "services", "fcc_database", "install.py")
                subprocess.run([sys.executable, fcc_script], check=False)
            else:
                _warn("Skipped. Callsign lookups will return 'not found'.")
                _warn("Run:  python3 services/fcc_database/install.py")
                print()
        return

    online, banner = decide_source(wheels_dir)

    if online is None:
        _fail(
            "server/wheels/ is empty and no internet is reachable.\n"
            "       Options:\n"
            "         (a) Copy oasis-offline/server/wheels/ to server/wheels/, or\n"
            "         (b) Connect to the internet and re-run."
        )

    print()
    print("  OASIS — Server Setup")
    _hr()
    print_source_banner(online, banner, wheels_dir)

    check_python()
    create_venv(venv_dir)
    install_dependencies(online, venv_dir, wheels_dir)
    if sys.platform == "linux":
        install_sqlite3(online, venv_dir)
        install_system_fonts(online)

    python = _venv_bin(venv_dir, "python")
    print()
    _hr()
    print("  Setup complete.")
    _info("Activate the venv:  source .venv/bin/activate")
    _info(f"Or run directly:    {os.path.relpath(python)} server/app.py")
    _hr()
    print()
