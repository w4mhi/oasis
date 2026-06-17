#!/usr/bin/env python3
"""
vendor-wheels.py
----------------
Re-download every Python wheel OASIS bundles in server/wheels/ so the server
can be installed with NO internet on any supported platform.

This is the single source of truth for the offline wheel set. Run it on a
machine WITH internet whenever scripts/requirements.txt changes; commit the
resulting server/wheels/. The Pi (and any other target) then installs purely
from these files via `pip install --no-index --find-links server/wheels`.

It downloads, for every supported {platform, python} target, exactly what
`requirements.txt` resolves to — pure-Python wheels (Flask, gunicorn, ...)
once, and the compiled deps (MarkupSafe, psutil) per platform/interpreter.

Usage:
  python3 scripts/vendor-wheels.py            # refresh the whole set
  python3 scripts/vendor-wheels.py --check    # verify, don't download (CI)

The --check mode resolves each target against the EXISTING wheels with
--no-index and fails if anything is missing — wire it into CI so a wheel gap
(like the Raspberry Pi aarch64 one) breaks the build instead of shipping.
"""

import argparse
import os
import subprocess
import sys

REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WHEELS_DIR = os.path.join(REPO_ROOT, "server", "wheels")
REQ_FILE   = os.path.join(REPO_ROOT, "scripts", "requirements.txt")

# Supported targets: (pip --platform tag, [python versions]).
# Keep this in sync with the support matrix in README.md / ASSESSMENT.md.
TARGETS = [
    # Raspberry Pi (64-bit Pi OS) — the primary target.
    ("manylinux2014_aarch64", ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]),
    # Linux desktop / server.
    ("manylinux2014_x86_64",  ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]),
    # Apple Silicon.
    ("macosx_11_0_arm64",     ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]),
    # Intel Mac — upstream MarkupSafe ships no x86_64 macOS wheels past 3.11,
    # so Intel Macs are supported only up to Python 3.11. (Apple Silicon is
    # covered through 3.14 above.)
    ("macosx_10_9_x86_64",    ["3.9", "3.10", "3.11"]),
    # Windows.
    ("win_amd64",             ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]),
]


def _hr():           print("─" * 64)
def _ok(msg):        print(f"  ✓  {msg}")
def _fail(msg):      print(f"  ✗  {msg}")


def _abi(pyver):
    return "cp" + pyver.replace(".", "")


def run_target(platform, pyver, check):
    """Resolve requirements for one target. Returns True on success."""
    cmd = [
        sys.executable, "-m", "pip", "download", "-r", REQ_FILE,
        "--only-binary=:all:",
        "--platform", platform,
        "--python-version", pyver,
        "--implementation", "cp",
        "--abi", _abi(pyver),
        "-d", WHEELS_DIR,
    ]
    if check:
        # Verify against what is already vendored — never touch the network.
        cmd += ["--no-index", "--find-links", WHEELS_DIR]

    result = subprocess.run(cmd, capture_output=True, text=True)
    label = f"{platform:<24} py{pyver}"
    if result.returncode == 0:
        _ok(label)
        return True
    _fail(label)
    # Surface the meaningful pip error line(s).
    for line in result.stderr.splitlines():
        if line.startswith("ERROR"):
            print(f"        {line}")
    return False


def main():
    ap = argparse.ArgumentParser(description="Vendor / verify the OASIS offline wheel set.")
    ap.add_argument("--check", action="store_true",
                    help="Verify the existing wheels satisfy every target; download nothing.")
    args = ap.parse_args()

    os.makedirs(WHEELS_DIR, exist_ok=True)
    print()
    print(f"  OASIS — {'verifying' if args.check else 'vendoring'} offline wheels")
    _hr()
    print(f"  requirements : {os.path.relpath(REQ_FILE)}")
    print(f"  wheels dir   : {os.path.relpath(WHEELS_DIR)}")
    _hr()

    failures = 0
    for platform, pyvers in TARGETS:
        for pyver in pyvers:
            if not run_target(platform, pyver, args.check):
                failures += 1

    _hr()
    if failures:
        print(f"  {failures} target(s) FAILED.")
        if args.check:
            print("  Run without --check (online) to refill the wheel set.")
        sys.exit(1)
    print("  All targets satisfied — the offline bundle is complete.")
    print()


if __name__ == "__main__":
    main()
