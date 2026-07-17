#!/usr/bin/env python3
"""
services/kiwix/download-wikipedia.py
-------------------------------------
Download an offline Wikipedia ZIM file for use with kiwix-serve.
ZIM files are self-contained offline snapshots published by the Kiwix project.

Choose the edition that fits your storage:
  top-mini      ~316 MB  — 50K best articles, no pictures
  simple-mini   ~447 MB  — Simple English, ~394K articles, no pictures
  simple-nopic  ~937 MB  — Simple English with full details, no pictures
  top-nopic     ~2.1 GB  — 50K best articles with full details, no pictures
  simple-maxi   ~3.2 GB  — Simple English with pictures
  all-mini      ~11.7 GB — All ~19M articles, no pictures, no details
  all-nopic     ~48 GB   — Full English Wikipedia, no images (needs large SSD)
  all-maxi      ~115 GB  — Full English Wikipedia with all images (large SSD)

After download, the kiwix service is updated and restarted automatically.

Usage:
  python3 services/kiwix/download-wikipedia.py             # interactive picker
  python3 services/kiwix/download-wikipedia.py --edition top-mini
  python3 services/kiwix/download-wikipedia.py --edition simple-maxi --zim-dir /mnt/ssd/zim
  python3 services/kiwix/download-wikipedia.py --list       # show all editions
  python3 services/kiwix/download-wikipedia.py --url https://download.kiwix.org/zim/wikipedia/...zim

ZIM files are sourced from https://download.kiwix.org/zim/wikipedia/
Sizes are approximate and reflect the 2026 Kiwix catalog.
"""

import argparse
import os
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET

# Import the ONE operator-home resolver the kiwix installer uses, so this
# downloader and the installed kiwix-start script can never disagree about
# where ZIM files live — even when either is run as bare root. (See
# target_user_home()'s docstring in services/kiwix/common/kiwix.py.)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
from services.kiwix.common.kiwix import target_user_home as _target_user_home  # noqa: E402



KIWIX_ZIM_BASE  = "https://download.kiwix.org/zim/wikipedia"
KIWIX_CATALOG   = "https://library.kiwix.org/catalog/v2/entries?lang=eng&category=wikipedia&count=100"
DEFAULT_ZIM_DIR = os.path.join(_target_user_home()[1], "oasis-offline", "zim")
SERVICE_NAME    = "kiwix"
LIBRARY_XML     = "library.xml"

# Curated editions with stable names, typical sizes, and descriptions.
# Sizes reflect the 2026-06 Kiwix catalog (approximate — updates monthly).
# Kiwix appends a date (YYYY-MM) to filenames; we resolve the latest via catalog.
# "name" + "_" + "flavour" forms the ZIM filename prefix, e.g. wikipedia_en_top_mini
EDITIONS = {
    "top-mini": {
        "name":    "wikipedia_en_top",
        "flavour": "mini",
        "label":   "Best of Wikipedia — mini (no pics)",
        "size":    "~316 MB",
        "desc":    "50K best articles, no pictures, no detailed sections."
                   " Best choice for a small SD card (32 GB+).",
    },
    "simple-mini": {
        "name":    "wikipedia_en_simple_all",
        "flavour": "mini",
        "label":   "Simple English Wikipedia — mini (no pics)",
        "size":    "~447 MB",
        "desc":    "~394K articles written in plain English, no pictures."
                   " Great for quick lookups and non-native readers.",
    },
    "simple-nopic": {
        "name":    "wikipedia_en_simple_all",
        "flavour": "nopic",
        "label":   "Simple English Wikipedia — full text, no pics",
        "size":    "~937 MB",
        "desc":    "~394K plain-English articles with full details, no pictures.",
    },
    "top-nopic": {
        "name":    "wikipedia_en_top",
        "flavour": "nopic",
        "label":   "Best of Wikipedia — full text, no pics",
        "size":    "~2.1 GB",
        "desc":    "50K best articles with full detail sections, no pictures."
                   " Good balance for a 4+ GB card.",
    },
    "simple-maxi": {
        "name":    "wikipedia_en_simple_all",
        "flavour": "maxi",
        "label":   "Simple English Wikipedia — with pictures",
        "size":    "~3.2 GB",
        "desc":    "~394K plain-English articles with all images."
                   " Good for tablets / Pi 4 with an 8+ GB card.",
    },
    "all-mini": {
        "name":    "wikipedia_en_all",
        "flavour": "mini",
        "label":   "Wikipedia (all articles) — mini (no pics)",
        "size":    "~11.7 GB",
        "desc":    "All ~19M English articles, no pictures, no detail sections."
                   " Needs 16+ GB free storage.",
    },
    "all-nopic": {
        "name":    "wikipedia_en_all",
        "flavour": "nopic",
        "label":   "Wikipedia (all articles) — full text, no pics",
        "size":    "~48 GB",
        "desc":    "Complete English Wikipedia with full details, no images."
                   " Requires a 64+ GB SSD.",
    },
    "all-maxi": {
        "name":    "wikipedia_en_all",
        "flavour": "maxi",
        "label":   "Wikipedia (all articles) — with pictures",
        "size":    "~115 GB",
        "desc":    "Complete English Wikipedia with all images."
                   " Large SSD required (128+ GB).",
    },
}

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

class _Progress:
    def __init__(self, total):
        self.total = total
        self.received = 0

    def update(self, chunk):
        self.received += chunk
        pct = (self.received / self.total * 100) if self.total else 0
        gb  = self.received / 1_073_741_824
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"\r    {bar} {pct:5.1f}%  {gb:.2f} GB", end="", flush=True)

    def done(self):
        gb = self.received / 1_073_741_824
        print(f"\r    {'█' * 50} 100.0%  {gb:.2f} GB")


# ── Step 1: Resolve latest ZIM filename from Kiwix catalog ────────────────────
def resolve_zim_url(edition_name, edition_flavour):
    _step(1, "Resolving latest ZIM from Kiwix catalog")
    _info(f"Querying: {KIWIX_CATALOG}")
    # The combined prefix used in filenames, e.g. wikipedia_en_top_mini
    combined = f"{edition_name}_{edition_flavour}"

    try:
        req = urllib.request.Request(
            KIWIX_CATALOG,
            headers={"Accept": "application/atom+xml,application/xml,text/xml"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            catalog_xml = resp.read()
    except Exception as exc:
        _fail(f"Could not reach Kiwix catalog: {exc}\n"
              "       Check your internet connection and try again.")

    # Parse OPDS/Atom catalog.
    # Acquisition links end in .zim.meta4 — strip the .meta4 to get the real URL.
    try:
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "dc":   "http://purl.org/dc/terms/"}
        root = ET.fromstring(catalog_xml)
        for entry in root.findall("atom:entry", ns):
            for link in entry.findall("atom:link", ns):
                href = link.get("href", "")
                # Catalog hrefs end in .zim.meta4 on lb.download.kiwix.org.
                # Extract just the filename and rebuild on KIWIX_ZIM_BASE
                # (download.kiwix.org) — the lb subdomain may redirect to a different file.
                if combined in href and (href.endswith(".zim") or href.endswith(".zim.meta4")):
                    filename = href.removesuffix(".meta4").rsplit("/", 1)[-1]
                    zim_url  = f"{KIWIX_ZIM_BASE}/{filename}"
                    _ok(f"Latest: {filename}")
                    return zim_url, filename
    except ET.ParseError:
        pass  # fall through to direct URL construction

    _warn("Catalog lookup inconclusive — falling back to direct URL pattern.")
    return None, None


def build_direct_url(edition_name, edition_flavour):
    """Construct a best-guess direct URL if catalog lookup fails."""
    combined = f"{edition_name}_{edition_flavour}"
    listing_url = f"{KIWIX_ZIM_BASE}/"
    _info(f"Checking directory listing: {listing_url}")
    try:
        with urllib.request.urlopen(listing_url, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        import re
        matches = re.findall(rf'href="({re.escape(combined)}[^"]+\.zim)"', html)
        if matches:
            latest = sorted(matches)[-1]
            url = f"{KIWIX_ZIM_BASE}/{latest}"
            _ok(f"Found: {latest}")
            return url, latest
    except Exception:
        pass

    _fail(f"Could not determine the download URL for '{combined}'.\n"
          "       Browse available files at:\n"
          f"       {KIWIX_ZIM_BASE}/\n"
          "       Then use --url to provide it directly.")


# ── Step 2: Download ZIM ───────────────────────────────────────────────────────
def download_zim(url, filename, zim_dir):
    _step(2, f"Downloading {filename}")
    _info(f"Source:      {url}")
    _info(f"Destination: {zim_dir}/")

    dest = os.path.join(zim_dir, filename)
    if os.path.exists(dest):
        _warn(f"File already exists: {dest}")
        # No controlling tty (e.g. the privileged installer worker): keep the
        # existing ZIM rather than crash on input()'s EOFError — a present ZIM is
        # the whole point of the offline bundle, so treat it as done, not failed.
        if not sys.stdin.isatty():
            _info("Non-interactive — keeping the existing file (skipping download).")
            return dest
        try:
            answer = input("       Overwrite? [y/N] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer != "y":
            _info("Download skipped — using existing file.")
            return dest

    os.makedirs(zim_dir, exist_ok=True)

    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            if total:
                _info(f"Size: {total / 1_073_741_824:.2f} GB")
            prog = _Progress(total)
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)  # 1 MB chunks for large files
                    if not chunk:
                        break
                    fh.write(chunk)
                    prog.update(len(chunk))
            prog.done()
    except KeyboardInterrupt:
        # Clean up partial file on Ctrl-C.
        if os.path.exists(dest):
            os.remove(dest)
        print("\n\n  Download cancelled.")
        sys.exit(1)
    except Exception as exc:
        _fail(f"Download failed: {exc}")

    _ok(f"Saved: {dest}")
    return dest


# ── Step 3: Update kiwix library and restart service ──────────────────────────
def update_library(zim_dir, zim_path):
    _step(3, "Updating Kiwix library")

    # Check if kiwix-manage is available (comes with kiwix-tools).
    manage = subprocess.run(["which", "kiwix-manage"], capture_output=True)
    lib_path = os.path.join(zim_dir, LIBRARY_XML)

    if manage.returncode == 0:
        _info("Using kiwix-manage to update library.xml ...")
        if not os.path.exists(lib_path):
            subprocess.run(["kiwix-manage", lib_path, "add", zim_path], check=True)
        else:
            # Check if this ZIM is already registered.
            result = subprocess.run(
                ["kiwix-manage", lib_path, "show"],
                capture_output=True, text=True
            )
            if zim_path not in result.stdout:
                subprocess.run(["kiwix-manage", lib_path, "add", zim_path], check=True)
        _ok(f"library.xml updated: {lib_path}")
    else:
        # kiwix-manage not available — write a minimal library.xml manually.
        _warn("kiwix-manage not found — writing minimal library.xml")
        _info("(This is normal if kiwix was installed without kiwix-tools)")
        book_id = os.path.splitext(os.path.basename(zim_path))[0].replace("_", "-")
        lib_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<library version="20110515">\n'
            f'  <book id="{book_id}" path="{zim_path}"/>\n'
            '</library>\n'
        )
        with open(lib_path, "w", encoding="utf-8") as fh:
            fh.write(lib_xml)
        _ok(f"library.xml written: {lib_path}")

    # Restart service if running.
    result = subprocess.run(
        ["systemctl", "is-enabled", SERVICE_NAME],
        capture_output=True, text=True
    )
    if result.stdout.strip() == "enabled":
        _info(f"Restarting {SERVICE_NAME} service ...")
        subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME], check=False)
        _ok(f"sudo systemctl restart {SERVICE_NAME}")
    else:
        _info(f"Service '{SERVICE_NAME}' is not enabled — skipping restart.")
        _info(f"Start it with:  sudo systemctl start {SERVICE_NAME}")


# ── Edition picker ─────────────────────────────────────────────────────────────
def pick_edition():
    print()
    print("  Available Wikipedia editions:")
    print()
    keys = list(EDITIONS.keys())
    for i, key in enumerate(keys, 1):
        ed = EDITIONS[key]
        print(f"  [{i}] {ed['label']}  {ed['size']}")
        print(f"       {ed['desc']}")
        print()

    while True:
        choice = input(f"  Select edition [1-{len(keys)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            return keys[int(choice) - 1]
        if choice in EDITIONS:
            return choice
        print(f"  Enter a number from 1 to {len(keys)}.")


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Download an offline Wikipedia ZIM file for Kiwix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Editions (sizes reflect 2026 Kiwix catalog):\n"
            "  top-mini      ~316 MB   50K best articles, no pictures\n"
            "  simple-mini   ~447 MB   Simple English, ~394K articles, no pictures\n"
            "  simple-nopic  ~937 MB   Simple English with full details, no pictures\n"
            "  top-nopic     ~2.1 GB   50K best articles with full details, no pictures\n"
            "  simple-maxi   ~3.2 GB   Simple English with pictures\n"
            "  all-mini      ~11.7 GB  All ~19M articles, no pictures, no details\n"
            "  all-nopic     ~48 GB    Full Wikipedia, no images (SSD recommended)\n"
            "  all-maxi      ~115 GB   Full Wikipedia with all images (large SSD)\n"
        )
    )
    parser.add_argument(
        "--edition", choices=list(EDITIONS.keys()),
        help="Which Wikipedia edition to download.",
    )
    parser.add_argument(
        "--url",
        help="Direct URL to a ZIM file (overrides --edition).",
    )
    parser.add_argument(
        "--zim-dir",
        default=DEFAULT_ZIM_DIR,
        metavar="PATH",
        help="Directory to store ZIM files (default: ~/oasis-offline/zim).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available editions and exit.",
    )
    args = parser.parse_args()

    if args.list:
        for key, ed in EDITIONS.items():
            print(f"{key:8}  {ed['size']:8}  {ed['label']} — {ed['desc']}")
        sys.exit(0)

    print()
    print("  OASIS — Wikipedia Downloader")
    _hr()
    _info(f"ZIM files will be saved to: {os.path.expanduser(args.zim_dir)}")

    zim_dir = os.path.expanduser(args.zim_dir)

    if args.url:
        url      = args.url
        filename = url.rsplit("/", 1)[-1]
        _info(f"Using direct URL: {url}")
    else:
        edition = args.edition or pick_edition()
        ed = EDITIONS[edition]
        _info(f"Edition: {ed['label']}  ({ed['size']})")

        url, filename = resolve_zim_url(ed["name"], ed["flavour"])
        if not url:
            url, filename = build_direct_url(ed["name"], ed["flavour"])

    zim_path = download_zim(url, filename, zim_dir)
    update_library(zim_dir, zim_path)

    print()
    _hr()
    print("  Wikipedia download complete.")
    _info(f"ZIM file:  {zim_path}")
    _info(f"Start Kiwix:  sudo systemctl start {SERVICE_NAME}")
    _info("Open browser: http://localhost:8081")
    _hr()
    print()


if __name__ == "__main__":
    main()
