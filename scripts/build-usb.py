#!/usr/bin/env python3
"""
build-usb.py
------------
Package OASIS into a self-contained USB bundle that runs on Windows and Linux
with no Python pre-installed.

What this builds:
  usb-dist/
  ├── _runtime/
  │   └── windows/          ← embedded Python 3.12 + Flask + psutil
  ├── server/               ← Flask app
  ├── static/               ← ICS forms, band plan, calculators, ...
  ├── css/                  ← shared design system
  ├── maps/                 ← PMTiles files
  ├── fcc-offline-database/ ← callsign data (if present)
  ├── ... (all project files)
  ├── start.bat             ← Windows launcher
  └── start.sh              ← Linux launcher (bootstraps venv on first run)

Windows:      uses the bundled embedded Python — nothing to install, runs offline.
Linux/macOS:  first run creates a venv from the bundled wheels (--no-index, no
              internet) using system Python 3, which ships with every major
              Linux distro and with macOS.

The build itself only needs internet to download the Windows embedded-Python
runtime from python.org; all Python packages come from the vendored
server/wheels/ set (see scripts/vendor-wheels.py). Use --skip-windows for a
fully offline build that targets Linux/macOS only.

Maps, forms, FCC lookup, calculators, and the reference library all work from
the bundle. Only the Pi-only companion services — GrayWolf, Kiwix, and the APRS
history API — show as DOWN on the dashboard, since they run on the Pi.

Usage:
  python3 scripts/build-usb.py
  python3 scripts/build-usb.py --out /Volumes/USB/oasis
  python3 scripts/build-usb.py --skip-windows   # copy files only, no Python download
"""

import argparse
import io
import os
import shutil
import stat
import sys
import urllib.request
import zipfile

# ── Config ─────────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PYTHON_VERSION   = "3.12.10"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)

# Files/dirs to exclude from the USB bundle
EXCLUDE = {
    ".git", ".venv", "__pycache__", "temp", "usb-dist",
    ".DS_Store", "*.pyc", "*.pyo",
}
# Large FCC data files — user must run setup-fcc-database.py to generate these
EXCLUDE_FILES = {"EN.dat", "HD.dat", "EN.idx"}

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

def _download(url, label):
    _info(f"Downloading {label} ...")
    data = io.BytesIO()
    with urllib.request.urlopen(url) as r:
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            data.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                print(f"\r       {pct:3d}%  {downloaded // 1048576} MB / {total // 1048576} MB", end="", flush=True)
    print()
    data.seek(0)
    return data


# ── Step 1: Copy project files ─────────────────────────────────────────────────
def copy_project(dest):
    _step(1, f"Copying project files → {dest}")

    if os.path.exists(dest):
        _info("Removing existing bundle ...")
        shutil.rmtree(dest)
    os.makedirs(dest)

    copied = 0
    skipped = 0

    for root, dirs, files in os.walk(REPO_ROOT):
        # Prune excluded directories in-place
        dirs[:] = [
            d for d in dirs
            if d not in EXCLUDE and not d.startswith(".")
        ]

        rel_root = os.path.relpath(root, REPO_ROOT)

        for fname in files:
            if fname in EXCLUDE_FILES:
                skipped += 1
                continue
            if any(fname.endswith(ext) for ext in (".pyc", ".pyo")):
                skipped += 1
                continue
            if fname == ".DS_Store":
                skipped += 1
                continue

            src  = os.path.join(root, fname)
            dest_path = os.path.join(dest, rel_root, fname)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src, dest_path)
            copied += 1

    _ok(f"{copied} files copied  ({skipped} excluded)")

    # Warn about missing FCC data
    fcc_data = os.path.join(dest, "fcc-offline-database", "data")
    missing = [f for f in ("EN.dat", "HD.dat", "EN.idx") if not os.path.exists(os.path.join(fcc_data, f))]
    if missing:
        _warn(f"FCC data files not included ({', '.join(missing)}) — callsign lookup will return 'not found'")
        _warn("Run scripts/setup-fcc-database.py first, then re-run this script.")


# ── Step 2 helpers ────────────────────────────────────────────────────────────
def _install_packages_into_embedded(win_dir, wheels_src):
    """
    Populate the embedded Python's site-packages by extracting the vendored
    wheels directly — no pip, no get-pip, no PyPI. This works identically on
    any build host (you never execute the Windows python.exe) and keeps the
    build fully offline.

    The embedded Python is CPython 3.12 / win_amd64, so we extract pure-Python
    wheels (none-any) plus any win_amd64 wheel compatible with it: cp312, the
    stable ABI (abi3), or tagless. psutil ships as cp3x-abi3-win_amd64, which
    is now vendored in server/wheels/, so nothing is fetched from the network.
    """
    site_packages = os.path.join(win_dir, "Lib", "site-packages")
    os.makedirs(site_packages, exist_ok=True)

    if not os.path.isdir(wheels_src):
        _fail(f"Wheels directory not found: {wheels_src}")

    installed = []
    saw_markupsafe = saw_psutil = False
    for fname in sorted(os.listdir(wheels_src)):
        if not fname.endswith(".whl"):
            continue
        lower = fname.lower()
        is_pure = "none-any" in lower
        is_win  = "win_amd64" in lower and (
            "cp312" in lower or "abi3" in lower or "-none-" in lower
        )
        if not (is_pure or is_win):
            continue
        with zipfile.ZipFile(os.path.join(wheels_src, fname)) as zf:
            zf.extractall(site_packages)
        installed.append(fname)
        if lower.startswith("markupsafe") and "win_amd64" in lower:
            saw_markupsafe = True
        if lower.startswith("psutil") and "win_amd64" in lower:
            saw_psutil = True

    _ok(f"Extracted {len(installed)} vendored wheel(s) into embedded Python (offline)")
    if not saw_markupsafe:
        _warn("No MarkupSafe win_amd64 wheel found — Flask will fail to import on Windows.")
        _warn("Run scripts/vendor-wheels.py to refill server/wheels/.")
    if not saw_psutil:
        _warn("No psutil win_amd64 wheel found — APRS/system stats will not work on USB.")


# ── Step 2: Windows — embedded Python ─────────────────────────────────────────
def setup_windows(dest, skip):
    _step(2, "Windows runtime — embedded Python")

    win_dir = os.path.join(dest, "_runtime", "windows")
    os.makedirs(win_dir, exist_ok=True)

    if skip:
        _warn("--skip-windows: skipping Python download. start.bat will not work.")
        return

    # Download embedded Python zip
    data = _download(PYTHON_EMBED_URL, f"Python {PYTHON_VERSION} embeddable (amd64)")
    with zipfile.ZipFile(data) as zf:
        zf.extractall(win_dir)
    _ok(f"Extracted to _runtime/windows/")

    # Uncomment 'import site' in pythonXY._pth so pip works
    pth_file = None
    for name in os.listdir(win_dir):
        if name.endswith("._pth"):
            pth_file = os.path.join(win_dir, name)
            break
    if pth_file:
        with open(pth_file) as f:
            lines = f.readlines()
        with open(pth_file, "w") as f:
            for line in lines:
                f.write(line.replace("#import site", "import site"))
        _ok(f"Patched {os.path.basename(pth_file)} (enabled import site)")

    # Populate the embedded Python from the vendored wheels — offline, and the
    # same code path on every build host (Windows/macOS/Linux). No pip needed.
    _install_packages_into_embedded(win_dir, os.path.join(REPO_ROOT, "server", "wheels"))


# ── Step 3: Write launchers ────────────────────────────────────────────────────
def write_launchers(dest):
    _step(3, "Writing launchers")

    # Windows
    bat = os.path.join(dest, "start.bat")
    with open(bat, "w", newline="\r\n") as f:
        f.write(
            "@echo off\r\n"
            "title OASIS\r\n"
            "cd /d \"%~dp0\"\r\n"
            "echo Starting OASIS...\r\n"
            "_runtime\\windows\\python.exe server\\app.py\r\n"
            "pause\r\n"
        )
    _ok("start.bat")

    # Linux / macOS — first run bootstraps a venv from the bundled wheels only
    # (--no-index), so it works with no internet. Uses system python3, which
    # ships with every major Linux distro and macOS.
    sh = os.path.join(dest, "start.sh")
    with open(sh, "w", newline="\n") as f:
        f.write(
            "#!/bin/bash\n"
            "set -e\n"
            'DIR="$(cd "$(dirname "$0")" && pwd)"\n'
            'VENV="$DIR/_runtime/linux/.venv"\n'
            'WHEELS="$DIR/server/wheels"\n'
            "cd \"$DIR\"\n"
            "\n"
            "if ! command -v python3 &>/dev/null; then\n"
            '  echo "ERROR: python3 not found. Install it with your package manager."\n'
            "  exit 1\n"
            "fi\n"
            "\n"
            'if [ ! -d "$VENV" ]; then\n'
            '  echo "First run — setting up Python environment from bundled wheels (offline) ..."\n'
            '  python3 -m venv "$VENV"\n'
            '  "$VENV/bin/pip" install --quiet --no-index --find-links "$WHEELS" flask gunicorn psutil\n'
            '  echo "Done."\n'
            "fi\n"
            "\n"
            'echo "Starting OASIS — open http://localhost:8083 in your browser"\n'
            '"$VENV/bin/python" server/app.py\n'
        )
    # Make executable
    st = os.stat(sh)
    os.chmod(sh, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    _ok("start.sh  (executable)")


# ── Step 4: Summary ────────────────────────────────────────────────────────────
def summary(dest):
    total = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, files in os.walk(dest)
        for f in files
    )
    _step(4, "Bundle complete")
    _ok(f"Location : {dest}")
    _ok(f"Size     : {total / 1_048_576:.0f} MB")
    print()
    _info("Windows : double-click start.bat")
    _info("Linux   : chmod +x start.sh && ./start.sh  (first run bootstraps venv)")
    _info("Browser : http://localhost:8083")
    print()
    _info("Maps, forms, FCC lookup, calculators and reference all work offline.")
    _warn("GrayWolf, Kiwix and the APRS API show DOWN — those are Pi-only services.")
    print()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Build a self-contained OASIS USB bundle for Windows and Linux."
    )
    parser.add_argument(
        "--out", default=os.path.join(REPO_ROOT, "usb-dist"),
        help="Output directory (default: usb-dist/ in repo root)"
    )
    parser.add_argument(
        "--skip-windows", action="store_true",
        help="Skip embedded Python download (copy files only)"
    )
    args = parser.parse_args()

    print(f"\nOASIS — build-usb")
    _hr()
    _info(f"Source : {REPO_ROOT}")
    _info(f"Output : {args.out}")

    copy_project(args.out)
    setup_windows(args.out, args.skip_windows)
    write_launchers(args.out)
    summary(args.out)


if __name__ == "__main__":
    main()
