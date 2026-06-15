#!/usr/bin/env python3
"""
setup-fcc-database.py
---------------------
One-shot setup for the OASIS offline FCC callsign database.

Run this ONCE on any machine that has internet access, then copy the
oasis-emcomm directory to the Pi. The Pi never needs to go online.

What this does (in order):
  1. Downloads EN.dat + HD.dat from the FCC (~160 MB total)
  2. Builds the binary-search index (EN.idx) from those files
  3. Optionally downloads and builds the full ZIP→grid table from GeoNames

Usage:
  python3 scripts/setup-fcc-database.py              # steps 1 + 2
  python3 scripts/setup-fcc-database.py --full-zip   # steps 1 + 2 + 3
  python3 scripts/setup-fcc-database.py --index-only # step 2 only (re-index)
  python3 scripts/setup-fcc-database.py --help

Re-run any time the FCC publishes new data (they refresh every Sunday):
  python3 scripts/setup-fcc-database.py
"""

import argparse
import csv
import io
import os
import sys
import time
import urllib.request
import zipfile

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(REPO_ROOT, "server")
DATA_DIR   = os.path.join(REPO_ROOT, "fcc-offline-database", "data")

FCC_URL      = "https://data.fcc.gov/download/pub/uls/complete/l_amat.zip"
GEONAMES_URL = "https://download.geonames.org/export/zip/US.zip"

EN_DAT   = os.path.join(DATA_DIR, "EN.dat")
HD_DAT   = os.path.join(DATA_DIR, "HD.dat")
ZIP_CSV  = os.path.join(DATA_DIR, "zipcodes.csv")

# ── Helpers ────────────────────────────────────────────────────────────────────
def _hr():
    print("─" * 60)

def _step(n, label):
    print(f"\n[{n}] {label}")
    _hr()

def _ok(msg):
    print(f"    ✓  {msg}")

def _info(msg):
    print(f"       {msg}")

def _fail(msg):
    print(f"\n  ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


class _Progress:
    """Simple download progress bar."""
    def __init__(self, label, total):
        self.label = label
        self.total = total
        self.received = 0
        self.start = time.time()

    def update(self, chunk):
        self.received += chunk
        pct = (self.received / self.total * 100) if self.total else 0
        mb  = self.received / 1_048_576
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"\r    {bar} {pct:5.1f}%  {mb:.1f} MB", end="", flush=True)

    def done(self):
        elapsed = time.time() - self.start
        mb = self.received / 1_048_576
        print(f"\r    {'█' * 50} 100.0%  {mb:.1f} MB  ({elapsed:.0f}s)")


def _download(url, dest_name):
    """
    Download *url* and return the raw bytes, showing a progress bar.
    *dest_name* is used only for display.
    """
    _info(f"Source: {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            prog  = _Progress(dest_name, total)
            buf   = io.BytesIO()
            while True:
                chunk = resp.read(1 << 16)  # 64 KB
                if not chunk:
                    break
                buf.write(chunk)
                prog.update(len(chunk))
            prog.done()
            return buf.getvalue()
    except Exception as exc:
        _fail(f"Download failed: {exc}")


# ── Step 1: Download FCC data ──────────────────────────────────────────────────
def download_fcc():
    _step(1, "Downloading FCC amateur license data")
    _info("This is the complete ULS file — EN.dat + HD.dat (~160 MB zipped).")

    os.makedirs(DATA_DIR, exist_ok=True)
    data = _download(FCC_URL, "l_amat.zip")

    _info("Extracting EN.dat and HD.dat ...")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            for target in ("EN.dat", "HD.dat"):
                match = next((n for n in names if n.upper().endswith(target.upper())), None)
                if not match:
                    _fail(f"{target} not found inside the FCC zip. Names: {names[:10]}")
                with zf.open(match) as src, open(os.path.join(DATA_DIR, target), "wb") as dst:
                    dst.write(src.read())
                _ok(f"{target} extracted to fcc-offline-database/data/")
    except zipfile.BadZipFile as exc:
        _fail(f"The downloaded file is not a valid zip: {exc}")


# ── Step 2: Build the call-sign index ─────────────────────────────────────────
def build_index():
    _step(2, "Building call-sign index")
    _info("Reads EN.dat + HD.dat once; writes EN.idx (binary-search index).")
    _info("On a Pi Zero this may take a couple of minutes.")

    # Import lookup from server/ at runtime so this script has no install deps.
    sys.path.insert(0, SERVER_DIR)
    try:
        import lookup
    except ImportError as exc:
        _fail(f"Could not import server/lookup.py: {exc}")

    if not os.path.exists(EN_DAT):
        _fail(f"EN.dat not found at {EN_DAT}\n"
              "       Run without --index-only to download it first.")

    start = time.time()
    try:
        count, filtered = lookup.build_index()
    except FileNotFoundError as exc:
        _fail(str(exc))

    elapsed = time.time() - start
    _ok(f"Indexed {count:,} call signs in {elapsed:.1f}s")
    if filtered:
        _ok("Active-only filtering: ON (HD.dat used)")
    else:
        _info("Active-only filtering: OFF (HD.dat absent — all records indexed)")


# ── Step 3: Full ZIP→grid table from GeoNames (optional) ──────────────────────
def build_zip_table():
    _step(3, "Building full ZIP→grid table from GeoNames (optional)")
    _info("Downloads ~1 MB from GeoNames (CC BY 4.0) and produces a")
    _info("40,000-row zipcodes.csv. The repo already ships a complete table;")
    _info("use this only to refresh it from a newer GeoNames release.")

    data = _download(GEONAMES_URL, "US.zip")

    _info("Parsing US.txt ...")
    POSTAL_COL, LAT_COL, LON_COL = 1, 9, 10
    seen = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            us_name = next((n for n in zf.namelist() if n.upper() == "US.TXT"), None)
            if not us_name:
                _fail(f"US.txt not found in GeoNames zip. Contents: {zf.namelist()}")
            with zf.open(us_name) as fh:
                for raw in fh:
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    parts = line.split("\t")
                    if len(parts) <= LON_COL:
                        continue
                    zip5 = parts[POSTAL_COL].strip()[:5]
                    lat  = parts[LAT_COL].strip()
                    lon  = parts[LON_COL].strip()
                    if not (zip5 and lat and lon) or zip5 in seen:
                        continue
                    try:
                        float(lat); float(lon)
                    except ValueError:
                        continue
                    seen[zip5] = (lat, lon)
    except zipfile.BadZipFile as exc:
        _fail(f"GeoNames download is not a valid zip: {exc}")

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ZIP_CSV, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["zip", "lat", "lon"])
        for zip5 in sorted(seen):
            writer.writerow([zip5, *seen[zip5]])

    _ok(f"Wrote {len(seen):,} ZIP codes → fcc-offline-database/data/zipcodes.csv")


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Set up the OASIS offline FCC callsign database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/setup-fcc-database.py            "
            "# download + index\n"
            "  python3 scripts/setup-fcc-database.py --full-zip "
            "# + refresh ZIP table\n"
            "  python3 scripts/setup-fcc-database.py --index-only"
            "# re-index existing data\n"
        ),
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="Skip download; rebuild EN.idx from existing EN.dat + HD.dat.",
    )
    parser.add_argument(
        "--full-zip",
        action="store_true",
        help="Also download GeoNames data and rebuild the ZIP→grid table.",
    )
    args = parser.parse_args()

    print()
    print("  OASIS — FCC Offline Database Setup")
    _hr()

    if not args.index_only:
        download_fcc()

    build_index()

    if args.full_zip:
        build_zip_table()

    print()
    _hr()
    print("  Setup complete. FCC database is ready for offline use.")
    _hr()
    print()


if __name__ == "__main__":
    main()
