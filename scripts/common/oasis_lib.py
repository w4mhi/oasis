"""
oasis_lib.py
------------
Shared library for all OASIS setup and install scripts.

Provides:
  - Unified display helpers  (_ok, _fail, _warn, _info, _hr, _step, _section,
                               _dl, _cp, _run)
  - Progress bar             (Progress)
  - Network utilities        (has_internet, download_to, download_bytes)
  - FCC database functions   (fcc_download, fcc_build_index, fcc_build_zip_table,
                               fcc_indexes_ready)
  - GrayWolf functions       (graywolf_find_local, graywolf_latest_release,
                               graywolf_download_deb)
  - Kiwix functions          (kiwix_find_local, kiwix_latest_version,
                               kiwix_download_tarball)
  - RTL-SDR functions        (rtl_sdr_find_local, debian_packages_index,
                               rtl_sdr_download_debs)
  - webssh / ttyd functions  (ttyd_find_local, ttyd_download)

Usage (from any script in scripts/):
    from common.oasis_lib import _ok, _fail, _warn, _info, has_internet, ...
"""

import csv
import gzip
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile


# ── Display helpers ────────────────────────────────────────────────────────────

def _hr():
    print("─" * 60)


def _section(title):
    """Wide section header used by create-oasis-offline."""
    print()
    print(f"  {title}")
    print("─" * 64)


def _step(n, label):
    """Numbered step header used by install-* and setup-* scripts."""
    print(f"\n[{n}] {label}")
    _hr()


def _ok(msg):   print(f"  ✓  {msg}")
def _info(msg): print(f"     {msg}")
def _warn(msg): print(f"  ⚠  {msg}")
def _dl(msg):   print(f"  ↓  [download]  {msg}")
def _cp(msg):   print(f"  →  [copy]      {msg}")


def _fail(msg):
    print(f"\n  ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _run(cmd, check=True, **kwargs):
    """Run a subprocess command. Calls _fail() on non-zero exit when check=True."""
    result = subprocess.run(cmd, check=False, **kwargs)
    if check and result.returncode != 0:
        _fail(f"Command failed: {' '.join(str(c) for c in cmd)}")
    return result


# ── Version helpers (install/upgrade, never downgrade) ──────────────────────────

def dpkg_installed_version(pkg):
    """Return the installed version of a dpkg package, or None if not installed."""
    r = subprocess.run(["dpkg-query", "-W", "-f=${Version}", pkg],
                       capture_output=True, text=True)
    v = r.stdout.strip()
    return v if r.returncode == 0 and v else None


def deb_field(path, field):
    """Read a control field (e.g. 'Package', 'Version') from a .deb file, or None."""
    r = subprocess.run(["dpkg-deb", "-f", path, field], capture_output=True, text=True)
    v = r.stdout.strip()
    return v if r.returncode == 0 and v else None


def dpkg_cmp(a, op, b):
    """True if 'a op b' under dpkg version ordering. op: gt ge lt le eq.

    Used for plain semver strings too (kiwix/ttyd) — dpkg is present on every
    Debian/Raspberry Pi OS target these installers run on.
    """
    return subprocess.run(["dpkg", "--compare-versions", a, op, b],
                          capture_output=True).returncode == 0


def binary_version(cmd):
    """Run a '--version'-style command and return the first X.Y(.Z) token, or None.

    Returns None if the binary is absent (OSError) or prints no version.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return None
    m = re.search(r"\d+\.\d+(?:\.\d+)?", (r.stdout or "") + (r.stderr or ""))
    return m.group(0) if m else None


def version_decision(name, our_version, installed_version, cmp=dpkg_cmp):
    """
    Decide what to do for a package given the version we can install vs the one
    already present. Logs the reasoning. Returns 'install', 'upgrade' or 'skip'.

        not installed         -> 'install'
        ours strictly newer   -> 'upgrade'
        ours same or older     -> 'skip'   (never downgrade)
        our version unknown    -> 'skip'   (don't risk a downgrade)
    """
    if not installed_version:
        _info(f"{name}: not installed → installing {our_version or '(unknown)'}")
        return "install"
    if not our_version:
        _ok(f"{name}: {installed_version} installed; available version unknown — keeping")
        return "skip"
    if cmp(our_version, "gt", installed_version):
        _info(f"{name}: {installed_version} installed → upgrading to {our_version}")
        return "upgrade"
    _ok(f"{name}: {installed_version} installed ≥ available {our_version} — keeping (no downgrade)")
    return "skip"


# ── Progress bar ───────────────────────────────────────────────────────────────

class Progress:
    """Simple download progress bar (block characters, MB counter)."""

    def __init__(self, total):
        self.total    = total
        self.received = 0
        self.start    = time.time()

    def _draw(self):
        pct = (self.received / self.total * 100) if self.total else 0
        mb  = self.received / 1_048_576
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"\r    {bar} {pct:5.1f}%  {mb:.1f} MB", end="", flush=True)

    def update(self, chunk_size):
        self.received += chunk_size
        self._draw()

    def done(self):
        elapsed = time.time() - self.start
        mb = self.received / 1_048_576
        print(f"\r    {'█' * 50} 100.0%  {mb:.1f} MB  ({elapsed:.0f}s)")


# ── Network utilities ──────────────────────────────────────────────────────────

def has_internet(host="8.8.8.8", port=53, timeout=3):
    """Best-effort check for outbound internet connectivity (DNS port)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def download_to(url, dest_path):
    """
    Stream *url* directly to *dest_path* with a progress bar.
    Raises on network or I/O error (caller decides how to handle).
    """
    req = urllib.request.Request(url, headers={"User-Agent": "oasis-emcomm"})
    print("    Connecting...", end="", flush=True)
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        prog  = Progress(total)
        with open(dest_path, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                prog.update(len(chunk))
        prog.done()


def download_bytes(url):
    """
    Download *url* into memory with a progress bar.
    Returns (data: bytes, error: None) on success,
            (None, error_str) on failure.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "oasis-emcomm"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            prog  = Progress(total)
            buf   = io.BytesIO()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                buf.write(chunk)
                prog.update(len(chunk))
            prog.done()
            return buf.getvalue(), None
    except Exception as exc:
        return None, str(exc)


# ── FCC database ───────────────────────────────────────────────────────────────

FCC_URL      = "https://data.fcc.gov/download/pub/uls/complete/l_amat.zip"
GEONAMES_URL = "https://download.geonames.org/export/zip/US.zip"


def _fcc_extract(source, data_dir):
    """
    Extract EN.dat and HD.dat from *source* (file path or file-like) into *data_dir*.
    """
    try:
        with zipfile.ZipFile(source) as zf:
            names = zf.namelist()
            for target in ("EN.dat", "HD.dat"):
                match = next(
                    (n for n in names if n.upper().endswith(target.upper())), None
                )
                if not match:
                    _warn(f"{target} not found inside the FCC zip.")
                    continue
                with zf.open(match) as src, \
                     open(os.path.join(data_dir, target), "wb") as dst:
                    dst.write(src.read())
                _ok(f"{target} extracted")
    except zipfile.BadZipFile as exc:
        _fail(f"FCC zip is not a valid zip file: {exc}")


def fcc_download_zip(dest_path):
    """
    Download l_amat.zip and stream it directly to *dest_path*.
    Used by create-oasis-offline.py to bundle the raw zip for offline installs.
    """
    _info(f"Source: {FCC_URL}")
    _info("Downloading FCC amateur radio license data (~160 MB) ...")
    try:
        with urllib.request.urlopen(FCC_URL, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            prog  = Progress(total)
            with open(dest_path, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
                    prog.update(len(chunk))
            prog.done()
    except Exception as exc:
        if os.path.exists(dest_path):
            os.remove(dest_path)
        _fail(f"FCC download failed: {exc}")


def fcc_download(data_dir, force=False):
    """
    Download (or extract from local zip) EN.dat + HD.dat into *data_dir*.
    Local-first: if l_amat.zip exists in *data_dir*, extracts from it without
    downloading (offline install path).  Falls back to internet if zip absent.
    Skips entirely if EN.dat already exists (pass force=True to re-download).
    """
    en_dat   = os.path.join(data_dir, "EN.dat")
    zip_path = os.path.join(data_dir, "l_amat.zip")
    os.makedirs(data_dir, exist_ok=True)

    if not force and os.path.exists(en_dat):
        _cp("EN.dat already present — skipping download")
        return

    # ─ Offline path: extract from bundled zip ───────────────────────────────────
    if os.path.exists(zip_path):
        _cp(f"Using bundled l_amat.zip (offline install)")
        _info("Extracting EN.dat and HD.dat ...")
        _fcc_extract(zip_path, data_dir)
        return

    # ─ Online path: download then extract ────────────────────────────────────
    _info(f"Source: {FCC_URL}")
    _info("Downloading FCC amateur radio license data (~160 MB) ...")
    try:
        with urllib.request.urlopen(FCC_URL, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            prog  = Progress(total)
            buf   = io.BytesIO()
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                buf.write(chunk)
                prog.update(len(chunk))
            prog.done()
        data = buf.getvalue()
    except Exception as exc:
        if os.path.exists(en_dat):
            _warn(f"Download failed — using existing EN.dat: {exc}")
            return
        _fail(
            f"FCC download failed and no local EN.dat found.\n"
            f"     Error: {exc}\n"
            "     Connect to the internet and re-run, or supply EN.dat manually."
        )

    _info("Extracting EN.dat and HD.dat ...")
    _fcc_extract(io.BytesIO(data), data_dir)


FCC_INDEX_NAMES = ("EN.idx", "EN_name.idx", "EN_grid.idx")
FCC_INDEX_META  = "EN.idx.meta"


def _fcc_sha256(path):
    """Streaming SHA-256 of a file — constant memory, safe on a Pi Zero."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fcc_write_index_meta(data_dir):
    """Record the EN.dat size + SHA-256 the indexes in *data_dir* were built
    against (EN.idx.meta). Lets a target validate shipped prebuilt indexes
    against its own extracted EN.dat before trusting them."""
    en_path = os.path.join(data_dir, "EN.dat")
    if not os.path.exists(en_path):
        return
    meta = {
        "en_size":   os.path.getsize(en_path),
        "en_sha256": _fcc_sha256(en_path),
        "built":     time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "indexes":   [n for n in FCC_INDEX_NAMES
                      if os.path.exists(os.path.join(data_dir, n))],
    }
    with open(os.path.join(data_dir, FCC_INDEX_META), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")


def fcc_indexes_ready(data_dir):
    """True when valid prebuilt indexes are present for the local EN.dat.

    Requires all three index files plus EN.idx.meta whose recorded size and
    SHA-256 match EN.dat. Used to skip the memory-heavy rebuild when an offline
    bundle already shipped the indexes (the build that OOMs a 512 MB Pi Zero).
    Size is compared first (cheap); the SHA-256 is only computed on a size match.
    """
    en_path   = os.path.join(data_dir, "EN.dat")
    meta_path = os.path.join(data_dir, FCC_INDEX_META)
    if not (os.path.exists(en_path) and os.path.exists(meta_path)):
        return False
    if any(not os.path.exists(os.path.join(data_dir, n)) for n in FCC_INDEX_NAMES):
        return False
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return False
    if os.path.getsize(en_path) != meta.get("en_size"):
        return False
    return _fcc_sha256(en_path) == meta.get("en_sha256")


def fcc_build_index(data_dir, server_dir):
    """
    Build EN.idx from EN.dat + HD.dat in *data_dir*.
    Imports server/lookup.py from *server_dir* and passes explicit file paths,
    so any data_dir works (including oasis-offline/).
    """
    en_path    = os.path.join(data_dir, "EN.dat")
    index_path = os.path.join(data_dir, "EN.idx")
    hd_path    = os.path.join(data_dir, "HD.dat")

    if not os.path.exists(en_path):
        _warn(f"EN.dat not found at {en_path} — skipping index build.")
        return

    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    try:
        import lookup
    except ImportError as exc:
        _warn(f"Could not import server/lookup.py: {exc} — skipping index build.")
        return

    start = time.time()
    try:
        count, filtered = lookup.build_index(
            en_path=en_path, index_path=index_path, hd_path=hd_path
        )
    except FileNotFoundError as exc:
        _warn(f"Index build failed: {exc}")
        return

    elapsed = time.time() - start
    _ok(f"Indexed {count:,} call signs in {elapsed:.1f}s")
    _ok("Active-only filtering: ON (HD.dat used)" if filtered
        else "Active-only filtering: OFF (HD.dat absent — all records indexed)")

    # ── Secondary indexes (name + grid) ──────────────────────────────────────
    # These are built immediately after the primary index using the same EN.dat
    # scan. They require zipcodes.csv for the grid index; if it is absent the
    # grid index is silently skipped (the name index still works).
    name_idx  = os.path.join(data_dir, "EN_name.idx")
    grid_idx  = os.path.join(data_dir, "EN_grid.idx")
    zip_path  = os.path.join(data_dir, "zipcodes.csv")

    start = time.time()
    try:
        n = lookup.build_name_index(en_path=en_path, index_path=name_idx, hd_path=hd_path)
        _ok(f"Name index: {n:,} entries in {time.time()-start:.1f}s")
    except Exception as exc:
        _warn(f"Name index build failed: {exc}")

    if os.path.exists(zip_path):
        start = time.time()
        try:
            n = lookup.build_grid_index(en_path=en_path, index_path=grid_idx,
                                        hd_path=hd_path, zip_path=zip_path)
            _ok(f"Grid index: {n:,} entries in {time.time()-start:.1f}s")
        except Exception as exc:
            _warn(f"Grid index build failed: {exc}")
    else:
        _warn("zipcodes.csv not found — grid index skipped (run without --index-only to build it)")

    # Fingerprint the EN.dat these indexes were built against, so an offline
    # bundle can ship prebuilt indexes and the target can verify they match its
    # own EN.dat (identical bytes → the byte offsets stay valid) and skip the
    # RAM-heavy rebuild — the build that OOMs a 512 MB Pi Zero.
    _fcc_write_index_meta(data_dir)


def fcc_build_zip_table(data_dir):
    """
    Download the GeoNames US ZIP code table (~1 MB) and write zipcodes.csv
    into *data_dir*.
    Hard-fails if the download fails and no zipcodes.csv is present.
    """
    zip_csv = os.path.join(data_dir, "zipcodes.csv")
    os.makedirs(data_dir, exist_ok=True)

    _info(f"Source: {GEONAMES_URL}")
    data, err = download_bytes(GEONAMES_URL)
    if data is None:
        if os.path.exists(zip_csv):
            _warn(f"GeoNames download failed — keeping existing zipcodes.csv: {err}")
            return
        _fail(
            f"Could not download GeoNames data and no zipcodes.csv found.\n"
            f"     Error: {err}\n"
            "     Connect to the internet and re-run, or supply zipcodes.csv manually."
        )

    _info("Parsing US.txt ...")
    POSTAL_COL, LAT_COL, LON_COL = 1, 9, 10
    seen = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            us_name = next(
                (n for n in zf.namelist() if n.upper() == "US.TXT"), None
            )
            if not us_name:
                _fail(f"US.txt not found in GeoNames zip. Contents: {zf.namelist()}")
            with zf.open(us_name) as fh:
                for raw in fh:
                    line  = raw.decode("utf-8", errors="replace").rstrip("\n")
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

    with open(zip_csv, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["zip", "lat", "lon"])
        for zip5 in sorted(seen):
            writer.writerow([zip5, *seen[zip5]])

    _ok(f"Wrote {len(seen):,} ZIP codes → zipcodes.csv")


# ── GrayWolf ───────────────────────────────────────────────────────────────────

_GRAYWOLF_API = "https://api.github.com/repos/chrissnell/graywolf/releases"


def graywolf_find_local(offline_dir, deb_arch):
    """Return path to a local GrayWolf .deb for *deb_arch*, or None."""
    if not os.path.isdir(offline_dir):
        return None
    for fname in os.listdir(offline_dir):
        if fname.endswith(f"_{deb_arch}.deb"):
            return os.path.join(offline_dir, fname)
    return None


def graywolf_latest_release(pinned_version=None):
    """
    Fetch GrayWolf release metadata from GitHub.
    Returns the parsed release dict (includes 'assets', 'tag_name', etc.).
    Hard-fails on network error.
    """
    if pinned_version:
        tag = f"v{pinned_version.lstrip('v')}"
        url = f"{_GRAYWOLF_API}/tags/{tag}"
        _info(f"Pinned version: {tag}")
    else:
        url = f"{_GRAYWOLF_API}/latest"
        _info("Fetching latest GrayWolf release from GitHub ...")

    req = urllib.request.Request(url, headers={"User-Agent": "oasis-emcomm"})
    last_exc = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                wait = 5 * attempt
                _warn(f"GitHub API attempt {attempt} failed ({exc}) — retrying in {wait}s ...")
                time.sleep(wait)
    _fail(f"Could not reach GitHub API: {last_exc}")


def graywolf_download_deb(url, filename, dest_dir):
    """
    Download a GrayWolf .deb from *url* into *dest_dir*.
    Returns the destination path. Hard-fails on error.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    _info(f"Source: {url}")
    try:
        download_to(url, dest_path)
    except Exception as exc:
        _fail(f"GrayWolf download failed: {exc}")
    _ok(f"Saved {filename}")
    return dest_path


# ── Pat (Winlink) ───────────────────────────────────────────────────────────────
# Pat is the Winlink client (web UI + Telnet/RF transports). Same GitHub-release
# .deb shape as GrayWolf — asset names are pat_<version>_linux_<arch>.deb.
_PAT_API = "https://api.github.com/repos/la5nta/pat/releases"


def pat_find_local(offline_dir, deb_arch):
    """Return path to a local Pat .deb for *deb_arch*, or None.

    Expected name: pat_<version>_linux_<deb_arch>.deb. macOS AppleDouble
    sidecars (._*) are ignored.
    """
    if not os.path.isdir(offline_dir):
        return None
    for fname in sorted(os.listdir(offline_dir)):
        if fname.startswith("._"):
            continue
        if fname.startswith("pat_") and fname.endswith(f"_linux_{deb_arch}.deb"):
            return os.path.join(offline_dir, fname)
    return None


def pat_latest_release(pinned_version=None):
    """
    Fetch Pat release metadata from GitHub.
    Returns the parsed release dict (includes 'assets', 'tag_name', etc.).
    Hard-fails on network error.
    """
    if pinned_version:
        tag = f"v{pinned_version.lstrip('v')}"
        url = f"{_PAT_API}/tags/{tag}"
        _info(f"Pinned version: {tag}")
    else:
        url = f"{_PAT_API}/latest"
        _info("Fetching latest Pat release from GitHub ...")

    req = urllib.request.Request(url, headers={"User-Agent": "oasis-emcomm"})
    last_exc = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                wait = 5 * attempt
                _warn(f"GitHub API attempt {attempt} failed ({exc}) — retrying in {wait}s ...")
                time.sleep(wait)
    _fail(f"Could not reach GitHub API: {last_exc}")


def pat_download_deb(url, filename, dest_dir):
    """
    Download a Pat .deb from *url* into *dest_dir*.
    Returns the destination path. Hard-fails on error.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    _info(f"Source: {url}")
    try:
        download_to(url, dest_path)
    except Exception as exc:
        _fail(f"Pat download failed: {exc}")
    _ok(f"Saved {filename}")
    return dest_path


# ── Kiwix ──────────────────────────────────────────────────────────────────────

KIWIX_BASE = "https://download.kiwix.org/release/kiwix-tools"


def kiwix_find_local(offline_dir, kiwix_arch):
    """Return path to a local kiwix-tools .tar.gz for *kiwix_arch*, or None."""
    if not os.path.isdir(offline_dir):
        return None
    for fname in os.listdir(offline_dir):
        if (re.match(rf"kiwix-tools_linux-{re.escape(kiwix_arch)}-", fname)
                and fname.endswith(".tar.gz")):
            return os.path.join(offline_dir, fname)
    return None


def kiwix_latest_version():
    """Parse the Kiwix download page for the highest published version string."""
    try:
        with urllib.request.urlopen(f"{KIWIX_BASE}/", timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        versions = re.findall(
            r"kiwix-tools_linux-aarch64-(\d+\.\d+\.\d+)\.tar\.gz", html
        )
        return (
            max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))
            if versions else None
        )
    except Exception:
        return None


def kiwix_download_tarball(dest_dir, version, kiwix_arch):
    """
    Download a kiwix-tools .tar.gz into *dest_dir*.
    Returns the destination path. Hard-fails on error.
    """
    filename  = f"kiwix-tools_linux-{kiwix_arch}-{version}.tar.gz"
    dest_path = os.path.join(dest_dir, filename)
    url       = f"{KIWIX_BASE}/{filename}"
    os.makedirs(dest_dir, exist_ok=True)
    _info(f"Version: {version}")
    _info(f"Source:  {url}")
    try:
        download_to(url, dest_path)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            _fail(
                f"Not found: {url}\n"
                f"     Check available versions at {KIWIX_BASE}/"
            )
        _fail(f"Kiwix download failed (HTTP {exc.code}): {exc}")
    except Exception as exc:
        _fail(f"Kiwix download failed: {exc}")
    _ok(f"Saved {filename}")
    return dest_path


# ── RTL-SDR / Debian packages ──────────────────────────────────────────────────

DEBIAN_BASE      = "https://deb.debian.org/debian"
DEBIAN_SUITE     = "bookworm"
RTL_SDR_PACKAGES = ["rtl-sdr", "librtlsdr0", "libusb-1.0-0"]

# Tools the RTL-SDR → GrayWolf APRS feed (and bench-testing) need but Raspberry Pi
# OS does not ship: socat carries the demodulated audio to GrayWolf's sdr_udp
# socket; tcpdump verifies the feed; multimon-ng is a standalone AFSK1200 decoder
# for testing the SDR chain independently of GrayWolf (see docs/graywolf-rtl-sdr.md).
# libwrap0 / libpcap0.8 are socat/tcpdump's not-always-present shared-lib deps
# (libc6 / libssl3 are base-system, always installed). Bundled so install-rtl-sdr.py
# can install them fully offline.
#
# Note: multimon-ng pulls a larger X11/audio dependency tree than the feed tools;
# only the multimon-ng .deb itself is vendored here, so on a minimal/Lite image its
# deps are resolved by apt (internet). install-rtl-sdr.py installs it best-effort.
FEED_PACKAGES = ["socat", "tcpdump", "multimon-ng", "libwrap0", "libpcap0.8"]


def rtl_sdr_find_local(offline_dir, deb_arch):
    """
    Return a list of .deb paths in *offline_dir* that match *deb_arch*.
    Returns an empty list if the directory doesn't exist or is empty.
    """
    if not os.path.isdir(offline_dir):
        return []
    return [
        os.path.join(offline_dir, f)
        for f in os.listdir(offline_dir)
        if f.endswith(f"_{deb_arch}.deb") or (
            f.endswith(".deb") and any(
                f.startswith(pkg.replace("-", "_")) or
                f.startswith(pkg)
                for pkg in RTL_SDR_PACKAGES
            )
        )
    ]


def debian_packages_index(arch, packages, suite=DEBIAN_SUITE):
    """
    Download and parse the Debian Packages.gz index for *arch*.
    Returns {package_name: {Filename, Version, SHA256, Size}}.
    Only packages listed in *packages* are kept.
    """
    url = f"{DEBIAN_BASE}/dists/{suite}/main/binary-{arch}/Packages.gz"
    _info(f"Fetching Debian package index: {suite}/main/binary-{arch} ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "oasis-emcomm"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = gzip.decompress(resp.read())
    except Exception as exc:
        _warn(f"Could not download Debian package index: {exc}")
        return {}

    want    = set(packages)
    pkgs    = {}
    current = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line == "":
            name = current.get("Package")
            if name and name in want:
                pkgs[name] = {
                    k: current.get(k, "")
                    for k in ("Filename", "Version", "SHA256", "Size")
                }
            current = {}
        elif ": " in line and not line.startswith(" "):
            key, _, val = line.partition(": ")
            current[key] = val
    return pkgs


def rtl_sdr_download_debs(dest_dir, deb_arch, packages=None, suite=DEBIAN_SUITE):
    """
    Download RTL-SDR Debian packages for *deb_arch* into *dest_dir*.
    Returns a list of downloaded file paths.
    """
    packages = packages or RTL_SDR_PACKAGES
    os.makedirs(dest_dir, exist_ok=True)

    pkg_index = debian_packages_index(deb_arch, packages, suite)
    if not pkg_index:
        _warn(f"Could not get package index for {deb_arch} — skipping download.")
        return []

    downloaded = []
    for pkg_name in packages:
        info = pkg_index.get(pkg_name)
        if not info or not info["Filename"]:
            _warn(f"Package '{pkg_name}' not found in {suite}/{deb_arch} index.")
            continue
        filename  = os.path.basename(info["Filename"])
        dest_path = os.path.join(dest_dir, filename)
        url       = f"{DEBIAN_BASE}/{info['Filename']}"
        size_mb   = int(info["Size"] or 0) / 1_048_576
        _dl(f"  {filename}  ({size_mb:.2f} MB)  ← deb.debian.org/{suite}")
        try:
            download_to(url, dest_path)
            _ok(f"  {filename}")
            downloaded.append(dest_path)
        except Exception as exc:
            _warn(f"  Failed to download {filename}: {exc}")
    return downloaded


# ── webssh / ttyd ──────────────────────────────────────────────────────────────

TTYD_VERSION  = "1.7.7"
TTYD_BASE_URL = "https://github.com/tsl0922/ttyd/releases/download"


def ttyd_find_local(offline_dir, suffix):
    """Return path to a local ttyd.<suffix> binary, or None."""
    if not os.path.isdir(offline_dir):
        return None
    path = os.path.join(offline_dir, f"ttyd.{suffix}")
    return path if os.path.isfile(path) else None


def ttyd_download(dest_dir, suffix, version=TTYD_VERSION):
    """
    Download the ttyd static binary for *suffix* into *dest_dir* and make it
    executable.  Returns the destination path, or None on failure (logged).
    """
    import stat as _stat

    os.makedirs(dest_dir, exist_ok=True)
    filename  = f"ttyd.{suffix}"
    dest_path = os.path.join(dest_dir, filename)
    url       = f"{TTYD_BASE_URL}/{version}/{filename}"
    _info(f"Source: {url}")
    try:
        download_to(url, dest_path)
        os.chmod(
            dest_path,
            os.stat(dest_path).st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH,
        )
        _ok(f"Downloaded {filename}")
        return dest_path
    except Exception as exc:
        _warn(f"Failed to download {filename}: {exc}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return None


# ── pmtiles CLI (Protomaps go-pmtiles) ─────────────────────────────────────────
# Single self-contained Go binary that converts MBTiles -> PMTiles offline.
# Vendored into the bundle's maps/ directory, one binary per platform, so an
# operator can convert legacy archives in the field with no internet.
# Used by maps/convert-mbtiles.py. Release asset naming is irregular: Darwin
# archives use 'go-pmtiles-<ver>_...' (hyphen) while Linux/Windows use
# 'go-pmtiles_<ver>_...' (underscore) — the manifest carries the exact template.

PMTILES_VERSION = "1.30.3"
_PMTILES_API    = "https://api.github.com/repos/protomaps/go-pmtiles/releases"


def pmtiles_latest_version():
    """Latest go-pmtiles version string (no leading 'v'), or None on failure."""
    try:
        req = urllib.request.Request(
            f"{_PMTILES_API}/latest", headers={"User-Agent": "oasis-emcomm"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        return (data.get("tag_name") or "").lstrip("v") or None
    except Exception:
        return None


def _pmtiles_extract_member(archive_bytes, asset_name):
    """Extract the 'pmtiles' (or 'pmtiles.exe') executable from a downloaded
    go-pmtiles archive. Handles .tar.gz and .zip. Returns the raw bytes, or None."""
    import tarfile

    data = io.BytesIO(archive_bytes)
    if asset_name.endswith(".zip"):
        try:
            with zipfile.ZipFile(data) as zf:
                for name in zf.namelist():
                    base = os.path.basename(name)
                    if base in ("pmtiles", "pmtiles.exe"):
                        return zf.read(name)
        except zipfile.BadZipFile:
            return None
    else:  # .tar.gz / .tgz
        try:
            with tarfile.open(fileobj=data, mode="r:gz") as tf:
                for member in tf.getmembers():
                    base = os.path.basename(member.name)
                    if base in ("pmtiles", "pmtiles.exe") and member.isfile():
                        fh = tf.extractfile(member)
                        return fh.read() if fh else None
        except (tarfile.TarError, OSError):
            return None
    return None


def pmtiles_download_binary(dest_dir, url, out_name):
    """
    Download a go-pmtiles release archive from *url*, extract the pmtiles
    executable, and write it to *dest_dir*/*out_name* (chmod +x).
    Returns the destination path, or None on failure (logged).
    """
    import stat as _stat

    os.makedirs(dest_dir, exist_ok=True)
    _info(f"Source: {url}")
    data, err = download_bytes(url)
    if data is None:
        _warn(f"Failed to download {out_name}: {err}")
        return None

    binary = _pmtiles_extract_member(data, url)
    if binary is None:
        _warn(f"Could not find the pmtiles executable inside {os.path.basename(url)}")
        return None

    dest_path = os.path.join(dest_dir, out_name)
    try:
        with open(dest_path, "wb") as fh:
            fh.write(binary)
        os.chmod(
            dest_path,
            os.stat(dest_path).st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH,
        )
        _ok(f"Extracted {out_name}")
        return dest_path
    except Exception as exc:
        _warn(f"Failed to write {out_name}: {exc}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return None
