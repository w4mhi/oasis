#!/usr/bin/env python3
"""
create-oasis-offline.py
-----------------------
Download every offline asset and build the OASIS offline distribution in one shot.
Always outputs to oasis-offline/ in the repo root (existing bundle is wiped).

  Default (no flags)
    Runs incrementally: existing assets at the current version are reused;
    only missing or outdated files are downloaded. Copies the repo source tree,
    writes the launchers and Windows Python runtime into oasis-offline/.
    Copy oasis-offline/ to a USB drive — it runs on Windows and Linux with no
    Python pre-installed and no internet access.

  --rebuild
    Wipe oasis-offline/ and perform a full clean rebuild from scratch.

  --check
    Verify that all offline assets are present in an existing oasis-offline/
    and that the wheel set satisfies every supported platform/Python target.
    No changes are made. Use in CI or before shipping a bundle.

  --skip-windows
    Skip downloading the embedded Windows Python runtime.
    start.bat will not work, but the build is fully offline.

  --update
    Update offline packages only in an existing distribution directory.
    By default targets the current working directory. Use --dir to point at
    a specific path (e.g. a mounted USB drive). Only the download phases run —
    source files, launchers, and the Windows runtime are not touched.

  --dir DIR
    Only valid with --update. Directory to update. Defaults to the current
    working directory when --update is used without --dir.

Build phases (run automatically unless --check):
  Phase 0 — Copy local files    oasis-offline/  (repo source tree)
  Phase 0a— APRS symbol sprites  oasis-offline/server/map-assets/
  Phase 1 — Python wheels       oasis-offline/server/wheels/
  Phase 2 — GrayWolf .deb       oasis-offline/offline-packages/graywolf/
  Phase 3 — Kiwix binaries      oasis-offline/offline-packages/kiwix/
  Phase 4 — FCC database        oasis-offline/fcc-offline-database/data/
  Phase 5 — RTL-SDR .deb        oasis-offline/offline-packages/rtl-sdr/
  Phase 6 — webssh ttyd binary  oasis-offline/offline-packages/webssh/
  Phase 7 — Pat (Winlink) .deb  oasis-offline/offline-packages/pat/
  Phase 8 — Wikipedia (Best of Wikipedia Mini)  oasis-offline/zim/  (~316 MB, 50K articles)

Usage:
  python3 scripts/create-oasis-offline.py                    # incremental build
  python3 scripts/create-oasis-offline.py --rebuild          # wipe + full rebuild
  python3 scripts/create-oasis-offline.py --check            # verify bundle (CI)
  python3 scripts/create-oasis-offline.py --skip-windows     # build, no Win runtime
  python3 scripts/create-oasis-offline.py --update           # update packages in repo root
  python3 scripts/create-oasis-offline.py --update --dir /mnt/usb  # update on USB drive
"""

import argparse
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

# ── Shared library ─────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import (
    _hr, _ok, _warn, _info, _dl, _cp, _section, _fail,
    download_to, download_bytes,
    fcc_download, fcc_download_zip,
    graywolf_latest_release, graywolf_download_deb,
    pat_latest_release,
    kiwix_latest_version, kiwix_download_tarball,
    rtl_sdr_download_debs,
    ttyd_download,
    KIWIX_BASE, RTL_SDR_PACKAGES, FEED_PACKAGES, DEBIAN_SUITE, TTYD_VERSION,
)

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR   = os.path.join(REPO_ROOT, "oasis-offline")
REQ_FILE  = os.path.join(REPO_ROOT, "scripts", "requirements.txt")

# ── Windows embedded Python ────────────────────────────────────────────────────
PYTHON_VERSION   = "3.12.10"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)

# ── Bundle copy exclusions ─────────────────────────────────────────────────────
# Directories excluded from the source copy (matched by basename).
EXCLUDE_DIRS = {".git", ".venv", "__pycache__", "temp", ".DS_Store",
                "offline-packages", "oasis-offline", "fcc-offline-database"}
# Directories excluded by path relative to REPO_ROOT (forward slashes).
# server/wheels/ is populated by the wheels download phase, not copied from repo.
EXCLUDE_RELPATHS = {"server/wheels"}
# EN.dat / HD.dat are large FCC raw source files; fcc-offline-database/ is in
# EXCLUDE_DIRS so these are never reached by build_copy anyway.
EXCLUDE_FILES = {"EN.dat", "HD.dat"}

# ── Phase 1: Python wheel targets ─────────────────────────────────────────────
# (pip --platform tag, [python versions]) — keep in sync with README.md.
TARGETS = [
    # Raspberry Pi (64-bit Pi OS) — the primary target.
    ("manylinux2014_aarch64", ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]),
    # Linux desktop / server.
    ("manylinux2014_x86_64",  ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]),
    # Apple Silicon.
    ("macosx_11_0_arm64",     ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]),
    # Intel Mac — MarkupSafe has no x86_64 macOS wheels past Python 3.11.
    ("macosx_10_9_x86_64",    ["3.9", "3.10", "3.11"]),
    # Windows.
    ("win_amd64",             ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]),
]






def _abi(pyver):
    return "cp" + pyver.replace(".", "")

def _download_mem(url, label):
    """Download a URL into memory with progress. Returns BytesIO at position 0."""
    _dl(f"{label} ...")
    data, err = download_bytes(url)
    if data is None:
        _fail(f"Download failed: {err}")
    return io.BytesIO(data)


# ── Phase 1: Python wheels ─────────────────────────────────────────────────────
def _resolve_target(platform, pyver, dest_dir, offline=False, find_links=None):
    """
    Run pip download for one (platform, python) target into dest_dir.
    offline=True adds --no-index --find-links find_links (CI / verify mode).
    Returns True on success.
    """
    cmd = [
        sys.executable, "-m", "pip", "download",
        "-r",                REQ_FILE,
        "--only-binary=:all:",
        "--platform",        platform,
        "--python-version",  pyver,
        "--implementation",  "cp",
        "--abi",             _abi(pyver),
        "-d",                dest_dir,
        "--quiet",
    ]
    if offline:
        cmd += ["--no-index", "--find-links", find_links or dest_dir]

    result = subprocess.run(cmd, capture_output=True, text=True)
    label  = f"{platform:<24} py{pyver}"
    if result.returncode == 0:
        _ok(label)
        return True
    _fail(label)
    for line in result.stderr.splitlines():
        if line.startswith("ERROR"):
            print(f"        {line}")
    return False


def phase_wheels(wheels_dir, check=False):
    """
    Phase 1: Python wheels.
    check=True  → --no-index resolution for all targets against wheels_dir (CI mode).
    check=False → download from PyPI directly into wheels_dir.
    Returns failure count (0 = success).
    """
    _section("Phase 1 — Python wheels")
    _info(f"requirements : {os.path.relpath(REQ_FILE)}")
    _info(f"wheels dir   : {os.path.relpath(wheels_dir)}")
    os.makedirs(wheels_dir, exist_ok=True)

    if check:
        failures = 0
        with tempfile.TemporaryDirectory() as tmp:
            for plat, pyvers in TARGETS:
                for pyver in pyvers:
                    if not _resolve_target(plat, pyver, tmp, offline=True,
                                           find_links=wheels_dir):
                        failures += 1
        if failures:
            _warn(f"{failures} target(s) FAILED — run without --check to rebuild.")
        else:
            _ok("All wheel targets satisfied")
        return failures

    failures = 0
    for plat, pyvers in TARGETS:
        for pyver in pyvers:
            if not _resolve_target(plat, pyver, wheels_dir):
                failures += 1
    if failures:
        _warn(f"{failures} target(s) FAILED — check your network connection.")
    else:
        _ok(f"Wheels ready in {os.path.relpath(wheels_dir)}/")
    return failures


# ── Phase 2: GrayWolf binaries ─────────────────────────────────────────────────
_GRAYWOLF_TARGETS = [
    ("arm64", "Raspberry Pi / Linux ARM 64-bit"),
    ("amd64", "Linux x86-64"),
]


def phase_graywolf(graywolf_dir, update=False):
    """Phase 2: Download GrayWolf .deb for arm64 + amd64."""
    _section("Phase 2 — GrayWolf APRS binaries")
    _info("Source  : https://github.com/chrissnell/graywolf")
    _info("Targets : arm64 (Pi), amd64 (Linux x86-64)")
    _info("Note    : Linux-only upstream — no macOS or Windows packages")
    os.makedirs(graywolf_dir, exist_ok=True)

    try:
        release = graywolf_latest_release()
    except SystemExit:
        _warn("GrayWolf will not be available for offline install.")
        return

    latest_ver = release["tag_name"].lstrip("v")
    _info(f"GrayWolf {latest_ver} — checking")
    downloaded = []
    for deb_arch, desc in _GRAYWOLF_TARGETS:
        filename  = f"graywolf_{latest_ver}_{deb_arch}.deb"
        dest_path = os.path.join(graywolf_dir, filename)
        if os.path.exists(dest_path):
            _cp(f"{filename}  (up to date)")
            downloaded.append(filename)
            continue
        asset = next(
            (a for a in release.get("assets", []) if a["name"] == filename), None
        )
        if not asset:
            _warn(f"Asset not found: {filename} ({desc}) — skipping")
            continue
        _dl(f"{filename}  ({asset['size'] / 1_048_576:.1f} MB)  ← GitHub")
        try:
            download_to(asset["browser_download_url"], dest_path)
            _ok(filename)
            downloaded.append(filename)
        except Exception as exc:
            _warn(f"Failed: {filename}: {exc}")

    if downloaded:
        _ok(f"GrayWolf {latest_ver} ready in {os.path.relpath(graywolf_dir)}/")
    else:
        _warn("No GrayWolf assets downloaded.")


# ── Phase 3: Kiwix binaries ────────────────────────────────────────────────────
_KIWIX_TARGETS = [
    ("aarch64", "Linux ARM 64-bit"),
    ("x86_64",  "Linux x86-64"),
]


def phase_kiwix(kiwix_dir, update=False):
    """Phase 3: Download kiwix-tools for linux-aarch64 + linux-x86_64."""
    _section("Phase 3 — Kiwix binaries")
    _info(f"Source  : {KIWIX_BASE}/")
    _info("Targets : linux-aarch64, linux-x86_64")
    os.makedirs(kiwix_dir, exist_ok=True)

    latest_ver = kiwix_latest_version()
    if latest_ver is None:
        _warn("Could not determine latest Kiwix version — skipping.")
        return

    _info(f"Kiwix {latest_ver} — checking")
    downloaded = []
    for kiwix_arch, desc in _KIWIX_TARGETS:
        filename  = f"kiwix-tools_linux-{kiwix_arch}-{latest_ver}.tar.gz"
        dest_path = os.path.join(kiwix_dir, filename)
        if os.path.exists(dest_path):
            _cp(f"{filename}  (up to date)")
            downloaded.append(filename)
            continue
        try:
            path = kiwix_download_tarball(kiwix_dir, latest_ver, kiwix_arch)
            downloaded.append(os.path.basename(path))
        except SystemExit:
            _warn(f"Failed to download kiwix-tools for {kiwix_arch} — skipping.")

    if downloaded:
        _ok(f"Kiwix {latest_ver} ready in {os.path.relpath(kiwix_dir)}/")
    else:
        _warn("No Kiwix assets downloaded.")


# ── Phase 0a: APRS symbol sprites ───────────────────────────────────────────
_APRS_SPRITES = [
    (
        "aprs-symbols-24-0.png",
        "https://github.com/hessu/aprs-symbols/raw/master/png/aprs-symbols-24-0.png",
        "Primary APRS symbol table (table '/')",
    ),
    (
        "aprs-symbols-24-1.png",
        "https://github.com/hessu/aprs-symbols/raw/master/png/aprs-symbols-24-1.png",
        "Alternate APRS symbol table (table '\\')",
    ),
]


def phase_aprs_sprites(map_assets_dir):
    """Phase 0a: Download APRS symbol sprite sheets into server/map-assets/."""
    _section("Phase 0a — APRS symbol sprites")
    _info("Source  : https://github.com/hessu/aprs-symbols")
    _info(f"Dest    : {os.path.relpath(map_assets_dir)}/")
    os.makedirs(map_assets_dir, exist_ok=True)

    for filename, url, desc in _APRS_SPRITES:
        dest = os.path.join(map_assets_dir, filename)
        if os.path.exists(dest):
            _cp(f"{filename}  (already present)")
            continue
        _dl(f"{filename}  ← {url}")
        try:
            download_to(url, dest)
            _ok(f"{filename}  ({desc})")
        except Exception as exc:
            _warn(f"Failed to download {filename}: {exc}")


# ── Phase 5: RTL-SDR Debian packages ─────────────────────────────────────────
_RTL_SDR_TARGETS = [
    ("arm64", "Raspberry Pi (64-bit)"),
    ("armhf", "Raspberry Pi (32-bit)"),
    ("amd64", "Linux x86-64"),
]


def phase_rtl_sdr(rtl_sdr_dir, update=False):
    """Phase 5: Download RTL-SDR + feed-tool Debian packages for arm64, armhf, amd64."""
    _section("Phase 5 — RTL-SDR Debian packages")
    packages = RTL_SDR_PACKAGES + FEED_PACKAGES
    _info(f"Suite   : Debian {DEBIAN_SUITE}")
    _info(f"Packages: {', '.join(packages)}")
    _info("Targets : arm64 (Pi 64-bit), armhf (Pi 32-bit), amd64")
    # rtl-sdr is the RX driver; socat carries the GrayWolf feed; tcpdump verifies
    # it. Both tool sets land in the same offline dir for install-rtl-sdr.py.
    for deb_arch, desc in _RTL_SDR_TARGETS:
        _info(f"  ─── {desc}")
        if os.path.isdir(rtl_sdr_dir):
            have = os.listdir(rtl_sdr_dir)
            def _present(prefix):
                return any(f.startswith(prefix) and f.endswith(f"_{deb_arch}.deb")
                           for f in have)
            # Up to date only if both the driver and the feed tools are bundled.
            if _present("rtl-sdr_") and _present("socat_") and _present("tcpdump_"):
                _cp(f"rtl-sdr + feed tools {deb_arch}: present  (up to date)")
                continue
        rtl_sdr_download_debs(rtl_sdr_dir, deb_arch, packages=packages)
# ── Phase 6: webssh (ttyd) static binaries ────────────────────────────────────
_WEBSSH_TARGETS = [
    ("aarch64", "Raspberry Pi (64-bit)"),
    ("armhf",   "Raspberry Pi (32-bit)"),
    ("arm",     "Raspberry Pi 1 / Zero (armv6)"),
    ("x86_64",  "Linux x86-64"),
]


def phase_webssh(webssh_dir, update=False):
    """
    Phase 6: Download the ttyd prebuilt static binaries (one per arch).
    ttyd is not in Debian stable; static binaries are the only reliable offline
    source. install-webssh.py installs these to /usr/local/bin/ttyd.
    """
    _section("Phase 6 — webssh (ttyd) static binaries")
    _info(f"Version : ttyd {TTYD_VERSION}")
    _info("Targets : " + ", ".join(f"{s} ({d})" for s, d in _WEBSSH_TARGETS))
    os.makedirs(webssh_dir, exist_ok=True)

    got = 0
    for suffix, desc in _WEBSSH_TARGETS:
        dest = os.path.join(webssh_dir, f"ttyd.{suffix}")
        if os.path.exists(dest):
            _cp(f"ttyd.{suffix}  (up to date)")
            got += 1
            continue
        if ttyd_download(webssh_dir, suffix) is not None:
            got += 1

    if got:
        _ok(f"{got}/{len(_WEBSSH_TARGETS)} ttyd binaries ready in "
            f"{os.path.relpath(webssh_dir)}/")
    else:
        _warn("No ttyd binaries downloaded — webssh offline install will not work.")


# ── Phase 7: Pat (Winlink) Debian packages ───────────────────────────────────
_PAT_TARGETS = [
    ("arm64", "Raspberry Pi (64-bit)"),
    ("armhf", "Raspberry Pi (32-bit)"),
    ("amd64", "Linux x86-64"),
]


def phase_pat(pat_dir, update=False):
    """Phase 7: Download the Pat (Winlink) .deb for arm64, armhf, amd64.

    Same GitHub-release shape as GrayWolf; asset names are
    pat_<version>_linux_<arch>.deb. Used by install-winlink.py.
    """
    _section("Phase 7 — Pat (Winlink) binaries")
    _info("Source  : https://github.com/la5nta/pat")
    _info("Targets : arm64 (Pi 64-bit), armhf (Pi 32-bit), amd64")
    _info("Note    : Linux .deb only here — install-winlink.py consumes these")
    os.makedirs(pat_dir, exist_ok=True)

    try:
        release = pat_latest_release()
    except SystemExit:
        _warn("Pat will not be available for offline install.")
        return

    latest_ver = release["tag_name"].lstrip("v")
    _info(f"Pat {latest_ver} — checking")
    downloaded = []
    for deb_arch, desc in _PAT_TARGETS:
        filename  = f"pat_{latest_ver}_linux_{deb_arch}.deb"
        dest_path = os.path.join(pat_dir, filename)
        if os.path.exists(dest_path):
            _cp(f"{filename}  (up to date)")
            downloaded.append(filename)
            continue
        asset = next(
            (a for a in release.get("assets", []) if a["name"] == filename), None
        )
        if not asset:
            _warn(f"Asset not found: {filename} ({desc}) — skipping")
            continue
        _dl(f"{filename}  ({asset['size'] / 1_048_576:.1f} MB)  ← GitHub")
        try:
            download_to(asset["browser_download_url"], dest_path)
            _ok(filename)
            downloaded.append(filename)
        except Exception as exc:
            _warn(f"Failed: {filename}: {exc}")

    if downloaded:
        _ok(f"Pat {latest_ver} ready in {os.path.relpath(pat_dir)}/")
    else:
        _warn("No Pat assets downloaded.")


# ── Phase 8: Wikipedia Mini ZIM ─────────────────────────────────────────────
WIKI_EDITION   = "wikipedia_en_top"
WIKI_FLAVOUR   = "mini"
WIKI_ZIM_BASE  = "https://download.kiwix.org/zim/wikipedia"
WIKI_CATALOG   = ("https://library.kiwix.org/catalog/v2/entries"
                  "?lang=eng&category=wikipedia&count=100")


def _resolve_wiki_url():
    """Query the Kiwix OPDS catalog for the latest wikipedia_en_top_mini ZIM URL.
    Returns (url, filename) or raises RuntimeError."""
    import xml.etree.ElementTree as ET
    edition_mini = f"{WIKI_EDITION}_{WIKI_FLAVOUR}"  # e.g. wikipedia_en_top_mini
    try:
        req = urllib.request.Request(
            WIKI_CATALOG,
            headers={"Accept": "application/atom+xml,application/xml,text/xml"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            catalog_xml = resp.read()
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(catalog_xml)
        for entry in root.findall("atom:entry", ns):
            for link in entry.findall("atom:link", ns):
                href = link.get("href", "")
                # Catalog acquisition links end in .zim.meta4 on lb.download.kiwix.org.
                # Extract just the filename and build the URL from WIKI_ZIM_BASE
                # (download.kiwix.org) — the lb subdomain may redirect to a different file.
                if edition_mini in href and (href.endswith(".zim") or href.endswith(".zim.meta4")):
                    filename = href.removesuffix(".meta4").rsplit("/", 1)[-1]
                    zim_url  = f"{WIKI_ZIM_BASE}/{filename}"
                    return zim_url, filename
    except Exception:
        pass  # fall through to directory listing

    # Fallback: scrape the directory listing for the newest matching filename.
    import re
    try:
        with urllib.request.urlopen(f"{WIKI_ZIM_BASE}/", timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        matches = re.findall(rf'href="({re.escape(edition_mini)}[^"]+\.zim)"', html)
        if matches:
            fname = sorted(matches)[-1]
            return f"{WIKI_ZIM_BASE}/{fname}", fname
    except Exception:
        pass

    raise RuntimeError(f"Could not resolve download URL for {edition_mini}")


def phase_wikipedia(zim_dir):
    """Phase 8: Download Wikipedia Top (Best of Wikipedia Mini) ZIM (~316 MB) into zim_dir."""
    edition_mini = f"{WIKI_EDITION}_{WIKI_FLAVOUR}"  # wikipedia_en_top_mini
    _section("Phase 8 — Wikipedia (Best of Wikipedia)")
    _info(f"Edition : {edition_mini}  (~316 MB, 50K best articles, no pictures)")
    _info(f"Source  : {WIKI_ZIM_BASE}/")
    _info(f"Dest    : {os.path.relpath(zim_dir)}/")
    os.makedirs(zim_dir, exist_ok=True)

    # Skip if any matching ZIM already present (handles date-stamped filenames).
    existing = [f for f in os.listdir(zim_dir)
                if f.startswith(edition_mini) and f.endswith(".zim")]
    if existing:
        _cp(f"{existing[0]}  (already present — skipping)")
        return

    try:
        url, filename = _resolve_wiki_url()
    except RuntimeError as exc:
        _warn(f"{exc}")
        _warn("Wikipedia will not be available in this bundle.")
        return

    _dl(f"{filename}  (∼316 MB)  ← Kiwix")
    _info(f"URL     : {url}")
    dest = os.path.join(zim_dir, filename)
    try:
        download_to(url, dest)
        _ok(f"{filename} saved to {os.path.relpath(zim_dir)}/")
    except Exception as exc:
        # Remove partial file so a re-run retries cleanly.
        if os.path.exists(dest):
            os.remove(dest)
        _warn(f"Download failed: {exc}")
        _warn("Wikipedia will not be available in this bundle.")


# ── Phase 4: FCC callsign database ────────────────────────────────────────────
def phase_fcc(fcc_dir):
    """
    Phase 4: Download l_amat.zip into fcc_dir.
    Extraction and indexing happen later on the target via setup-fcc-database.py.
    """
    _section("Phase 4 — FCC callsign database")

    zip_path = os.path.join(fcc_dir, "l_amat.zip")
    if os.path.exists(zip_path):
        _cp("l_amat.zip already present — skipping")
        return

    os.makedirs(fcc_dir, exist_ok=True)
    try:
        fcc_download_zip(zip_path)
        _ok(f"l_amat.zip saved → {os.path.relpath(zip_path)}")
        _ok("Run setup-fcc-database.py on the target to extract and index")
    except SystemExit:
        _warn("FCC download failed — callsign lookup will not work offline.")
        _warn("Re-run this script when the FCC site is reachable.")

# ── Build: copy project files ──────────────────────────────────────────────────
def build_copy(dest, src=None):
    """
    Copy the repo source tree into dest.  Package directories managed by the
    download phases (offline-packages/, server/wheels/) are skipped — they are
    already populated in dest before this function is called.
    Does not wipe dest.
    src defaults to REPO_ROOT; pass a different path when calling from inside
    the bundle (where REPO_ROOT is the bundle itself).
    """
    src = src or REPO_ROOT
    _section(f"Copying source files  →  {dest}")

    exclude_relpaths = {p.replace("/", os.sep) for p in EXCLUDE_RELPATHS}
    copied = skipped = 0
    for root, dirs, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        dirs[:] = [
            d for d in dirs
            if d not in EXCLUDE_DIRS
            and not d.startswith(".")
            and os.path.normpath(os.path.join(rel_root, d)) not in exclude_relpaths
        ]
        for fname in files:
            if (
                fname in EXCLUDE_FILES
                or any(fname.endswith(e) for e in (".pyc", ".pyo"))
                or fname == ".DS_Store"
            ):
                skipped += 1
                continue
            dest_path = os.path.join(dest, rel_root, fname)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(os.path.join(root, fname), dest_path)
            copied += 1

    _cp(f"{copied} files copied from repo to bundle  ({skipped} excluded)")

    # Report anything that will hurt the offline experience.
    gw_dir = os.path.join(dest, "offline-packages", "graywolf")
    kw_dir = os.path.join(dest, "offline-packages", "kiwix")
    rs_dir = os.path.join(dest, "offline-packages", "rtl-sdr")
    ws_dir = os.path.join(dest, "offline-packages", "webssh")
    pt_dir = os.path.join(dest, "offline-packages", "pat")
    if not any(f.endswith(".deb") for f in (os.listdir(gw_dir) if os.path.isdir(gw_dir) else [])):
        _warn("GrayWolf .deb missing — Pi users will need internet to install GrayWolf.")
    if not any(f.endswith(".tar.gz") for f in (os.listdir(kw_dir) if os.path.isdir(kw_dir) else [])):
        _warn("Kiwix binary missing — Pi users will need internet to install Kiwix.")
    rs_debs = [f for f in (os.listdir(rs_dir) if os.path.isdir(rs_dir) else []) if f.endswith(".deb")]
    if not any(f.startswith("rtl-sdr_") for f in rs_debs):
        _warn("RTL-SDR .deb packages missing — Pi users will need internet to install RTL-SDR.")
    if not any(f.startswith("socat_") for f in rs_debs):
        _warn("socat/tcpdump .deb packages missing — Pi users will need internet to enable the RTL-SDR feed.")
    if not any(f.startswith("ttyd.") for f in (os.listdir(ws_dir) if os.path.isdir(ws_dir) else [])):
        _warn("webssh (ttyd) static binaries missing — Pi users will need internet to install webssh.")
    if not any(f.endswith(".deb") for f in (os.listdir(pt_dir) if os.path.isdir(pt_dir) else [])):
        _warn("Pat .deb missing — Winlink users will need internet to install Pat.")



# ── Build: Windows embedded Python runtime ────────────────────────────────────
def _install_into_embedded(win_dir, wheels_src):
    """
    Populate embedded Python's site-packages from the vendored wheels.
    Extracts pure-Python wheels plus win_amd64 wheels compatible with cp312.
    No pip, no network — works identically on any build host OS.
    """
    site_packages = os.path.join(win_dir, "Lib", "site-packages")
    os.makedirs(site_packages, exist_ok=True)

    installed      = []
    saw_markupsafe = saw_psutil = False
    for fname in sorted(os.listdir(wheels_src)):
        if not fname.endswith(".whl"):
            continue
        lower    = fname.lower()
        is_pure  = "none-any" in lower
        is_win   = "win_amd64" in lower and (
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

    _ok(f"Extracted {len(installed)} wheel(s) into embedded Python")
    if not saw_markupsafe:
        _warn("MarkupSafe win_amd64 wheel missing — Flask will fail on Windows.")
    if not saw_psutil:
        _warn("psutil win_amd64 wheel missing — system stats will not work on Windows.")


def build_windows_runtime(dest, skip):
    _section("Windows runtime — embedded Python")
    win_dir    = os.path.join(dest, "_runtime", "windows")
    wheels_src = os.path.join(dest, "server", "wheels")
    os.makedirs(win_dir, exist_ok=True)

    if skip:
        _warn("--skip-windows: Python runtime omitted. start.bat will not work.")
        return

    data = _download_mem(PYTHON_EMBED_URL, f"Python {PYTHON_VERSION} embeddable (amd64)  ← python.org latest")
    with zipfile.ZipFile(data) as zf:
        zf.extractall(win_dir)
    _ok(f"Extracted Python {PYTHON_VERSION} to _runtime/windows/")

    # Enable 'import site' so installed packages are visible.
    pth_file = next(
        (os.path.join(win_dir, n) for n in os.listdir(win_dir) if n.endswith("._pth")),
        None,
    )
    if pth_file:
        with open(pth_file) as f:
            lines = f.readlines()
        with open(pth_file, "w") as f:
            for line in lines:
                f.write(line.replace("#import site", "import site"))
        _ok(f"Patched {os.path.basename(pth_file)}")

    _install_into_embedded(win_dir, wheels_src)


# ── Build: launchers ───────────────────────────────────────────────────────────
def build_launchers(dest):
    _section("Writing launchers")

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

    # Linux / macOS — bootstraps a venv from the bundled wheels on first run.
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
    st = os.stat(sh)
    os.chmod(sh, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    _ok("start.sh  (executable)")


# ── Build: summary ─────────────────────────────────────────────────────────────
def build_summary(dest):
    total = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, files in os.walk(dest)
        for f in files
    )
    _section("Bundle complete")
    _ok(f"Location : {dest}")
    _ok(f"Size     : {total / 1_048_576:.0f} MB")
    print()
    _info("Next step: copy oasis-offline/ to your USB drive")
    _info("")
    _info("Windows : double-click start.bat")
    _info("Linux   : chmod +x start.sh && ./start.sh  (first run bootstraps venv)")
    _info("Browser : http://localhost:8083")
    print()
    _info("Maps, forms, FCC lookup, calculators and reference all work offline.")
    _warn("GrayWolf, Kiwix and the APRS API show DOWN — those are Pi-only services.")
    _warn("Install them on the Pi from offline-packages/ using the install scripts.")
    print()


# ── Commands ───────────────────────────────────────────────────────────────────
def cmd_check():
    """Verify all offline assets in an existing oasis-offline/. Hard-fails on missing wheels."""
    print("\n  OASIS — create-oasis-offline  [--check]")
    _hr()

    wheels_dir = os.path.join(OUT_DIR, "server", "wheels")
    if not os.path.isdir(wheels_dir):
        _warn(f"oasis-offline/ not found or empty at {OUT_DIR}")
        _warn("Run without --check to build the bundle first.")
        sys.exit(1)

    wheel_failures = phase_wheels(wheels_dir, check=True)

    _section("Phase 2 — GrayWolf binaries  [check]")
    gw_dir  = os.path.join(OUT_DIR, "offline-packages", "graywolf")
    gw_debs = [f for f in (os.listdir(gw_dir) if os.path.isdir(gw_dir) else []) if f.endswith(".deb")]
    if gw_debs:
        _ok(f"{len(gw_debs)} GrayWolf .deb file(s) present")
    else:
        _warn("Not found — run without --check to build the bundle.")

    _section("Phase 3 — Kiwix binaries  [check]")
    kw_dir   = os.path.join(OUT_DIR, "offline-packages", "kiwix")
    kw_files = [f for f in (os.listdir(kw_dir) if os.path.isdir(kw_dir) else []) if f.endswith(".tar.gz")]
    if kw_files:
        _ok(f"{len(kw_files)} Kiwix archive(s) present")
    else:
        _warn("Not found — run without --check to build the bundle.")

    _section("Phase 5 — RTL-SDR packages  [check]")
    rs_dir  = os.path.join(OUT_DIR, "offline-packages", "rtl-sdr")
    rs_debs = [f for f in (os.listdir(rs_dir) if os.path.isdir(rs_dir) else []) if f.endswith(".deb")]
    if rs_debs:
        _ok(f"{len(rs_debs)} RTL-SDR .deb file(s) present")
    else:
        _warn("Not found — run without --check to build the bundle.")

    _section("Phase 6 — webssh (ttyd) static binaries  [check]")
    ws_dir  = os.path.join(OUT_DIR, "offline-packages", "webssh")
    ws_bins = [
        f for f in (os.listdir(ws_dir) if os.path.isdir(ws_dir) else [])
        if f.startswith("ttyd.") and not f.startswith("._")
    ]
    if ws_bins:
        _ok(f"{len(ws_bins)} ttyd binary/binaries present")
    else:
        _warn("Not found — run without --check to build the bundle.")

    _section("Phase 7 — Pat (Winlink) packages  [check]")
    pt_dir  = os.path.join(OUT_DIR, "offline-packages", "pat")
    pt_debs = [f for f in (os.listdir(pt_dir) if os.path.isdir(pt_dir) else []) if f.endswith(".deb")]
    if pt_debs:
        _ok(f"{len(pt_debs)} Pat .deb file(s) present")
    else:
        _warn("Not found — run without --check to build the bundle.")

    _section("Phase 8 — Wikipedia (Best of Wikipedia)  [check]")
    zim_dir  = os.path.join(OUT_DIR, "zim")
    edition_mini = f"{WIKI_EDITION}_{WIKI_FLAVOUR}"
    zim_files = [f for f in (os.listdir(zim_dir) if os.path.isdir(zim_dir) else [])
                 if f.startswith(edition_mini) and f.endswith(".zim")]
    if zim_files:
        size_mb = os.path.getsize(os.path.join(zim_dir, zim_files[0])) / 1_048_576
        _ok(f"{zim_files[0]}  ({size_mb:.0f} MB)")
    else:
        _warn("Wikipedia Mini ZIM not found — run without --check to download.")

    _section("Phase 4 — FCC database  [check]")
    fcc_zip = os.path.join(OUT_DIR, "fcc-offline-database", "data", "l_amat.zip")
    if os.path.exists(fcc_zip):
        size_mb = os.path.getsize(fcc_zip) / 1_048_576
        _ok(f"l_amat.zip present  ({size_mb:.0f} MB)")
    else:
        _warn("l_amat.zip not found — run without --check to build the bundle.")

    _hr()
    if wheel_failures:
        print(f"\n  {wheel_failures} wheel target(s) FAILED.\n")
        sys.exit(1)
    print("\n  All wheel targets satisfied.\n")


def cmd_build(skip_windows, rebuild=False):
    """Download all offline assets directly into oasis-offline/, then build the bundle."""
    print("\n  OASIS — create-oasis-offline")
    _hr()
    _info(f"Output : {OUT_DIR}")
    _info(f"Mode   : {'clean rebuild (wiping existing)' if rebuild else 'incremental (reusing existing assets)'}")

    if rebuild and os.path.exists(OUT_DIR):
        _info("Removing existing oasis-offline/ ...")
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    # Phase 0: copy repo source files first (offline-packages/ and server/wheels/
    # are excluded — the download phases below will populate those dirs).
    build_copy(OUT_DIR)

    # Download phases — each writes directly into its oasis-offline/ subdirectory.
    # update=True means "skip files already at current version" (always on by default).
    phase_aprs_sprites(os.path.join(OUT_DIR, "server", "map-assets"))
    phase_wheels(os.path.join(OUT_DIR, "server", "wheels"))
    phase_graywolf(os.path.join(OUT_DIR, "offline-packages", "graywolf"), update=True)
    phase_kiwix(os.path.join(OUT_DIR, "offline-packages", "kiwix"), update=True)
    fcc_dir    = os.path.join(OUT_DIR, "fcc-offline-database", "data")
    phase_fcc(fcc_dir)
    phase_rtl_sdr(os.path.join(OUT_DIR, "offline-packages", "rtl-sdr"), update=True)
    phase_webssh(os.path.join(OUT_DIR, "offline-packages", "webssh"), update=True)
    phase_pat(os.path.join(OUT_DIR, "offline-packages", "pat"), update=True)
    phase_wikipedia(os.path.join(OUT_DIR, "zim"))

    build_windows_runtime(OUT_DIR, skip_windows)
    build_launchers(OUT_DIR)
    build_summary(OUT_DIR)


# ── Update command ────────────────────────────────────────────────────────────
def cmd_update(target_dir):
    """Update an existing distribution directory: refresh offline packages AND
    sync repo source files.  When target_dir is the repo root itself (the
    default), the source-sync step is skipped — the repo IS the source.
    """
    print("\n  OASIS — create-oasis-offline  [--update]")
    _hr()
    _info(f"Target : {target_dir}")

    if not os.path.isdir(target_dir):
        _fail(
            f"Directory not found: {target_dir}\n"
            "     Run without --update to build a new distribution first."
        )

    # Determine if the target is a separate bundle directory or the repo root.
    # os.path.samefile handles symlinks and trailing slashes correctly.
    try:
        same_as_repo = os.path.samefile(target_dir, REPO_ROOT)
    except OSError:
        same_as_repo = os.path.abspath(target_dir) == os.path.abspath(REPO_ROOT)

    # When running from inside the bundle (oasis-offline/scripts/), REPO_ROOT is
    # the bundle directory itself.  Check if the parent of REPO_ROOT is the main
    # repo (has scripts/create-oasis-offline.py) and use it as the copy source.
    parent_repo = os.path.dirname(REPO_ROOT)
    parent_has_script = os.path.isfile(
        os.path.join(parent_repo, "scripts", "create-oasis-offline.py")
    )

    if same_as_repo:
        if parent_has_script:
            _info("Mode   : inside bundle — updating offline packages + syncing from parent repo")
        else:
            _info("Mode   : bundle root — updating offline packages only (no parent repo found)")
    else:
        _info("Mode   : bundle directory — updating offline packages + syncing source files")

    # Phase 0: sync repo source files first (before download phases populate
    # offline-packages/ and server/wheels/, which are excluded from the copy).
    # - If targeting a separate bundle dir → copy from this repo.
    # - If targeting our own REPO_ROOT but parent is the main repo → copy from parent.
    # - If we're on a USB drive with no parent repo → skip (nothing to copy from).
    if not same_as_repo:
        build_copy(target_dir)
    elif parent_has_script:
        build_copy(target_dir, src=parent_repo)

    phase_aprs_sprites(os.path.join(target_dir, "server", "map-assets"))
    phase_wheels(os.path.join(target_dir, "server", "wheels"))
    phase_graywolf(os.path.join(target_dir, "offline-packages", "graywolf"), update=True)
    phase_kiwix(os.path.join(target_dir, "offline-packages", "kiwix"), update=True)
    phase_fcc(os.path.join(target_dir, "fcc-offline-database", "data"))
    phase_rtl_sdr(os.path.join(target_dir, "offline-packages", "rtl-sdr"), update=True)
    phase_webssh(os.path.join(target_dir, "offline-packages", "webssh"), update=True)
    phase_pat(os.path.join(target_dir, "offline-packages", "pat"), update=True)
    phase_wikipedia(os.path.join(target_dir, "zim"))

    _section("Update complete")
    _ok(f"Updated: {target_dir}")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description=(
            "Download all offline assets and build the OASIS offline distribution.\n\n"
            "By default runs incrementally: existing assets are reused, only missing\n"
            "or outdated files are downloaded.  Use --rebuild for a clean slate.\n"
            "--check verifies an existing oasis-offline/ without rebuilding (CI mode).\n"
            "--update refreshes offline packages in an existing distribution directory."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/create-oasis-offline.py                        # incremental build\n"
            "  python3 scripts/create-oasis-offline.py --rebuild              # wipe + full rebuild\n"
            "  python3 scripts/create-oasis-offline.py --check               # verify bundle (CI)\n"
            "  python3 scripts/create-oasis-offline.py --skip-windows        # skip Windows Python\n"
            "  python3 scripts/create-oasis-offline.py --update              # update packages + sync source files"
            " into oasis-offline/\n"
            "  python3 scripts/create-oasis-offline.py --update --dir /mnt/usb  # update USB bundle\n"
        ),
    )
    ap.add_argument(
        "--check", action="store_true",
        help="Verify offline assets in oasis-offline/ and report findings. No changes (CI mode).",
    )
    ap.add_argument(
        "--skip-windows", action="store_true",
        help="Skip the Windows embedded-Python download. start.bat will not work.",
    )
    ap.add_argument(
        "--rebuild", action="store_true",
        help=(
            "Wipe oasis-offline/ and do a full clean rebuild. "
            "By default the script runs incrementally, reusing assets that are already "
            "at the current version and only downloading what is missing or outdated."
        ),
    )
    ap.add_argument(
        "--update", action="store_true",
        help=(
            "Update an existing distribution: refresh offline packages and sync repo source files. "
            f"Targets oasis-offline/ inside the repo by default; use --dir to specify a different path."
        ),
    )
    ap.add_argument(
        "--dir", metavar="DIR",
        help="Directory to update. Only valid with --update. Defaults to oasis-offline/ inside the repo.",
    )
    args = ap.parse_args()

    if args.dir and not args.update:
        ap.error("--dir is only valid with --update")

    if args.update:
        if args.dir:
            target = os.path.abspath(args.dir)
        elif os.path.isdir(OUT_DIR):
            target = OUT_DIR          # running from main repo → update oasis-offline/
        else:
            target = REPO_ROOT        # running from inside the bundle → update bundle root
        cmd_update(target)
    elif args.check:
        cmd_check()
    else:
        cmd_build(args.skip_windows, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
