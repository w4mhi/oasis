#!/usr/bin/env python3
"""
convert-mbtiles.py
------------------
Convert a legacy .mbtiles archive to .pmtiles format for use with OASIS.

Wraps the 'pmtiles' CLI (Protomaps go-pmtiles).  The binary must be present
on PATH or in this directory (maps/) before running — no internet needed at
conversion time.

The offline bundle (oasis-offline/) ships a per-platform pmtiles binary in
maps/ automatically (pmtiles-linux-arm64, pmtiles-darwin-arm64, …), so on a
USB-deployed bundle this script works out of the box with no extra download.

The resulting .pmtiles file can be:
  • Loaded at runtime via Offline Maps → Load maps
  • Placed in maps/ to be picked up automatically by the map page

Usage:
  python3 maps/convert-mbtiles.py region.mbtiles
  python3 maps/convert-mbtiles.py region.mbtiles out/region.pmtiles
  python3 maps/convert-mbtiles.py --check          # verify pmtiles is reachable

Getting the pmtiles binary manually (only needed when NOT using the bundle):
  https://github.com/protomaps/go-pmtiles/releases — download the archive for
  your platform, extract 'pmtiles', and drop it in this maps/ directory (or on
  your PATH).  Do this on a machine with internet before going to the field.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))   # maps/


# ── Find pmtiles binary ────────────────────────────────────────────────────────

def _platform_binary_name():
    """Return the bundled per-platform pmtiles binary name for this host, or None.

    The offline bundle ships one binary per platform in maps/ (named by
    create-oasis-offline.py's pmtiles phase): pmtiles-linux-arm64,
    pmtiles-linux-x86_64, pmtiles-darwin-arm64, pmtiles-darwin-x86_64,
    pmtiles-windows-x86_64.exe.
    """
    sys_  = platform.system().lower()
    mach  = platform.machine().lower()

    if sys_ == "linux":
        if mach in ("aarch64", "arm64"):
            return "pmtiles-linux-arm64"
        if mach in ("x86_64", "amd64"):
            return "pmtiles-linux-x86_64"
    elif sys_ == "darwin":
        if mach == "arm64":
            return "pmtiles-darwin-arm64"
        if mach in ("x86_64", "amd64"):
            return "pmtiles-darwin-x86_64"
    elif sys_ == "windows":
        if mach in ("x86_64", "amd64"):
            return "pmtiles-windows-x86_64.exe"
    return None


def _find_pmtiles():
    """Return the path to the pmtiles binary, or None if not found.

    Search order:
      1. maps/pmtiles — a binary the operator dropped in manually
      2. maps/<platform-specific> — the per-platform binary the offline bundle ships
      3. System PATH — package-manager or manual install
      4. Standard bin directories — for trimmed systemd PATH environments
    """
    # 1. Manually placed binary in maps/
    local = os.path.join(_HERE, "pmtiles")
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local

    # 2. Per-platform binary vendored into maps/ by the offline bundle builder
    plat_name = _platform_binary_name()
    if plat_name:
        cand = os.path.join(_HERE, plat_name)
        if os.path.isfile(cand):
            # Ensure it's executable (USB/FAT copies may have lost the bit).
            if not os.access(cand, os.X_OK):
                try:
                    os.chmod(cand, 0o755)
                except OSError:
                    pass
            if os.access(cand, os.X_OK):
                return cand

    # 3. PATH
    path = shutil.which("pmtiles")
    if path:
        return path

    # 4. Standard dirs
    for d in ("/usr/local/bin", "/usr/bin", "/opt/homebrew/bin"):
        cand = os.path.join(d, "pmtiles")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand

    return None


def _install_hint():
    """Return a platform-aware download hint for the pmtiles binary."""
    sys_  = platform.system().lower()
    mach  = platform.machine().lower()

    if sys_ == "linux":
        if mach in ("aarch64", "arm64"):
            variant, ext, extract = "Linux_arm64",   "tar.gz", "tar -xzf"
        elif mach in ("armv7l", "armhf"):
            variant, ext, extract = "Linux_armv7",   "tar.gz", "tar -xzf"
        else:
            variant, ext, extract = "Linux_x86_64",  "tar.gz", "tar -xzf"
    elif sys_ == "darwin":
        if mach == "arm64":
            variant, ext, extract = "Darwin_arm64",  "zip",    "unzip"
        else:
            variant, ext, extract = "Darwin_x86_64", "zip",    "unzip"
    else:
        variant, ext, extract = "Windows_x86_64", "zip", "unzip"

    archive = f"go-pmtiles-*_{variant}.{ext}" if sys_ == "darwin" else f"go-pmtiles_*_{variant}.{ext}"

    return (
        f"  Download the pmtiles binary for your platform ({variant}):\n"
        "  https://github.com/protomaps/go-pmtiles/releases\n\n"
        f"  Extract the 'pmtiles' binary and place it in maps/ or on your PATH:\n"
        f"    {extract} {archive} pmtiles\n"
        f"    mv pmtiles {_HERE}/\n"
        f"    chmod +x {_HERE}/pmtiles\n\n"
        "  Then re-run this script.  No internet is needed after that."
    )


# ── Conversion ─────────────────────────────────────────────────────────────────

def cmd_check(pmtiles_bin):
    """Report whether the pmtiles binary is available and print its version."""
    if not pmtiles_bin:
        print("  ✗  pmtiles not found.")
        print()
        print(_install_hint())
        return 1

    try:
        r = subprocess.run(
            [pmtiles_bin, "version"],
            capture_output=True, text=True, timeout=5,
        )
        ver = (r.stdout or r.stderr or "").strip().splitlines()
        ver_line = ver[0] if ver else "(version unknown)"
    except Exception as exc:
        ver_line = f"(could not query version: {exc})"

    print(f"  ✓  pmtiles found: {pmtiles_bin}")
    print(f"     {ver_line}")
    return 0


def cmd_convert(pmtiles_bin, src, dst):
    """Run pmtiles convert src → dst."""
    if not pmtiles_bin:
        print(f"  ✗  pmtiles binary not found.")
        print()
        print(_install_hint())
        return 1

    # Validate input
    if not os.path.isfile(src):
        print(f"  ✗  Input file not found: {src}", file=sys.stderr)
        return 1
    if not src.lower().endswith(".mbtiles"):
        print(f"  ✗  Input must be a .mbtiles file: {src}", file=sys.stderr)
        return 1

    # Derive output path if not given
    if dst is None:
        base = os.path.splitext(os.path.basename(src))[0]
        dst  = os.path.join(os.path.dirname(os.path.abspath(src)), base + ".pmtiles")

    if os.path.exists(dst):
        print(f"  ✗  Output already exists: {dst}")
        print("     Remove or rename it first, then re-run.")
        return 1

    # Ensure output directory exists
    out_dir = os.path.dirname(os.path.abspath(dst))
    os.makedirs(out_dir, exist_ok=True)

    src_mb = os.path.getsize(src) / 1_048_576
    print(f"\n  OASIS — MBTiles → PMTiles converter")
    print("─" * 56)
    print(f"     Input  : {src}  ({src_mb:.1f} MB)")
    print(f"     Output : {dst}")
    print(f"     Tool   : {pmtiles_bin}")
    print()
    print("  Converting …  (this may take a minute for large archives)")
    print()

    try:
        result = subprocess.run(
            [pmtiles_bin, "convert", src, dst],
            text=True,
        )
    except KeyboardInterrupt:
        print("\n  Interrupted. Removing partial output …")
        if os.path.exists(dst):
            os.remove(dst)
        return 1
    except Exception as exc:
        print(f"  ✗  Failed to run pmtiles: {exc}", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(f"  ✗  pmtiles exited with code {result.returncode}.", file=sys.stderr)
        # Remove partial output so a re-run is clean
        if os.path.exists(dst):
            os.remove(dst)
        return 1

    dst_mb = os.path.getsize(dst) / 1_048_576
    print(f"  ✓  Done.  {dst}  ({dst_mb:.1f} MB)")
    print()
    print("  Next steps:")
    maps_dir = _HERE
    if os.path.abspath(os.path.dirname(dst)) != os.path.abspath(maps_dir):
        print(f"    • Move {os.path.basename(dst)} into maps/ to load it automatically")
    print("    • Or open Offline Maps → Load maps to load it from its current location")
    print()
    return 0


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=(
            "Convert a legacy .mbtiles archive to .pmtiles for use with OASIS.\n"
            "Requires the pmtiles CLI binary — see --check for install instructions."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 maps/convert-mbtiles.py                      # show help\n"
            "  python3 maps/convert-mbtiles.py --check              # verify pmtiles is available\n"
            "  python3 maps/convert-mbtiles.py region.mbtiles       # convert (auto-names output)\n"
            "  python3 maps/convert-mbtiles.py region.mbtiles maps/region.pmtiles\n"
        ),
    )
    ap.add_argument(
        "input", nargs="?", metavar="INPUT.mbtiles",
        help=".mbtiles file to convert.",
    )
    ap.add_argument(
        "output", nargs="?", metavar="OUTPUT.pmtiles",
        help=".pmtiles output path (default: same directory and basename as input).",
    )
    ap.add_argument(
        "--check", action="store_true",
        help="Verify the pmtiles binary is available and print its version.",
    )
    args = ap.parse_args()

    pmtiles_bin = _find_pmtiles()

    if args.check:
        sys.exit(cmd_check(pmtiles_bin))

    if not args.input:
        ap.print_help()
        sys.exit(0)

    sys.exit(cmd_convert(pmtiles_bin, args.input, args.output))


if __name__ == "__main__":
    main()
