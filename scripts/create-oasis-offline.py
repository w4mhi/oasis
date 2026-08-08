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
    Refresh the manifest's per-feature `version` from upstream latest. For each
    versioned feature (GrayWolf, Kiwix, Pat/Winlink, ttyd) it fetches the latest
    available version and rewrites `version` in scripts/offline-manifest.json:
    no version or a stale one -> UPDATED; already latest -> CURRENT; can't reach
    upstream -> left for --update. No downloads — the files are fetched later on
    --update / build, which read these pinned versions. Operates on the manifest
    in the script's own root (repo or a deployed bundle), updating it in place.

  --for-windows
    Also download the embedded Windows Python runtime (opt-in).
    By default the full build targets the Pi only; the launcher otherwise falls
    back to system Python on the target. (Implied by --profile windows.)

  --profile {full,windows}
    full (default)  — the complete Pi bundle in oasis-offline/.
    windows         — a tools-only bundle in oasis-offline-windows/: Flask +
                      standalone tools + FCC lookup + embedded Windows Python,
                      with no Pi hardware, displays, APRS/Winlink, ZIM or maps.
    What each profile copies is controlled by scripts/bundle-ignore (base) and
    scripts/bundle-ignore.windows (overlay) — gitignore syntax, edit those.

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
  Phase 4 — FCC database        oasis-offline/services/fcc_database/data/
  Phase 5 — RTL-SDR .deb        oasis-offline/features/rtl-sdr/packages/rtl-sdr/<suite>/  (feature-local)
  Phase 6 — webssh ttyd binary  oasis-offline/offline-packages/webssh/
  Phase 7 — Pat (Winlink) .deb  oasis-offline/offline-packages/pat/
    Phase 7a — ADS-B dump1090-fa .deb  oasis-offline/services/adsb/packages/dump1090-fa/<suite>/
  Phase 8 — Wikipedia (Best of Wikipedia Mini)  oasis-offline/zim/  (~316 MB, 50K articles)
  Phase 9 — pmtiles CLI binaries oasis-offline/maps/  (per-platform MBTiles→PMTiles converter)

Usage:
  python3 scripts/create-oasis-offline.py                    # incremental build (Pi only)
  python3 scripts/create-oasis-offline.py --rebuild          # wipe + full rebuild
  python3 scripts/create-oasis-offline.py --check            # verify bundle (CI)
  python3 scripts/create-oasis-offline.py --for-windows      # include Windows Python runtime
  python3 scripts/create-oasis-offline.py --update           # update packages in repo root
  python3 scripts/create-oasis-offline.py --update --dir /mnt/usb  # update on USB drive
  python3 scripts/create-oasis-offline.py --verify           # verify bundle checksums
  python3 scripts/create-oasis-offline.py --verify --dir /mnt/usb  # verify USB copy
"""

import argparse
import datetime
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

# ── Shared library ─────────────────────────────────────────────────────────────
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_SCRIPTS_DIR))
from common.oasis_lib import (
    _hr, _ok, _warn, _info, _dl, _cp, _section, _fail,
    download_to, download_bytes,
    fcc_download_zip, fcc_download, fcc_build_zip_table, fcc_build_index,
    fcc_indexes_ready, FCC_INDEX_NAMES, FCC_INDEX_META,
    graywolf_latest_release,
    pat_latest_release,
    kiwix_latest_version, kiwix_download_tarball,
    rtl_sdr_download_debs,
    ttyd_download,
    pmtiles_latest_version, pmtiles_download_binary, PMTILES_VERSION,
    KIWIX_BASE, TTYD_VERSION,
    debian_packages_index,
)
from common import manifest as M

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
OUT_DIR   = os.path.join(REPO_ROOT, "oasis-offline")            # full (Pi) bundle
# The "windows" (tools-only) profile builds into its own directory so it never
# clobbers — or gets cross-contaminated by — a full Pi bundle.
OUT_DIR_WINDOWS = os.path.join(REPO_ROOT, "oasis-offline-windows")
# --check works on the SCRIPT'S OWN root (parent of scripts/), so it behaves
# identically run from the repo or from inside a deployed oasis-offline/ bundle (or
# a card): it reads <root>/offline-packages/ and updates <root>/scripts/offline-manifest.json.
# (The build still outputs into OUT_DIR = <repo>/oasis-offline/.)
BUNDLE_PKG_ROOT = os.path.join(REPO_ROOT, "offline-packages")
REQ_FILE  = os.path.join(REPO_ROOT, "scripts", "requirements.txt")

# ── Manifest ───────────────────────────────────────────────────────────────────
# Load once; all phases read from this.
_MANIFEST_PATH = os.path.join(_SCRIPTS_DIR, "offline-manifest.json")

# ── Windows embedded Python ────────────────────────────────────────────────────
PYTHON_VERSION   = "3.12.10"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)

# ── Bundle copy filter (gitignore-style, external) ─────────────────────────────
# What never gets copied into a bundle is declared in scripts/bundle-ignore (and,
# per profile, scripts/bundle-ignore.<profile>) — a single reviewable file in
# gitignore syntax, not scattered constants. build_copy loads and honours it.
BUNDLE_IGNORE       = os.path.join(_SCRIPTS_DIR, "bundle-ignore")
BUNDLE_IGNORE_EXTRA = {"windows": os.path.join(_SCRIPTS_DIR, "bundle-ignore.windows")}

# ── Paths owned by the download/build phases, not by build_copy ─────────────────
# build_copy mirrors the repo *source* tree and reaps orphans (files that no longer
# exist in the repo). These subtrees are populated by the download phases and the
# runtime/launcher/manifest builders instead, so they must survive an incremental
# build_copy untouched — matched against a dest-relative path or any of its parents.
PRESERVE_IN_DEST = {
    "offline-packages",      # graywolf / kiwix / webssh / pat (shared central tree)
    "features/rtl-sdr/packages",  # feature-local: rtl-sdr + direwolf .debs (bundle_base)
    "services/satellites/packages",  # feature-local: satellites-voice .debs (phase_satellites_voice)
    "overlays",                  # vendored .dtbo blobs (see overlays/SOURCE.md)
    "displays/cm4stack/packages", # legacy m5stack-cm4.dtbo drop point
    "server/wheels",         # phase_wheels
    "services/map/map-assets",  # phase_aprs_sprites
    "services/fcc_database/data",  # phase_fcc
    "zim",                   # phase_wikipedia
    "maps",                  # phase_pmtiles (binaries live here alongside source — preserve whole)
    "_runtime",              # build_windows_runtime (embedded Python)
    "bundle-manifest.json",  # write_bundle_manifest
}


# ── pmtiles platform selection ────────────────────────────────────────────────
# The bundle targets Linux (Pi), so only the Linux pmtiles binaries ship by
# default. macOS/Windows binaries are ~55 MB each and marked default=false in the
# manifest — included only with --all-platforms. Derived from the manifest so it
# stays the single source of truth. Non-default binaries are also skipped by
# build_copy, so committed repo copies don't sneak back into a lean bundle.
def _pmtiles_platforms(all_platforms=False):
    try:
        plats = M.get_feature("pmtiles").get("platforms", [])
    except KeyError:
        return []
    return plats if all_platforms else [p for p in plats if p.get("default", True)]


def _pmtiles_nondefault_outs():
    try:
        plats = M.get_feature("pmtiles").get("platforms", [])
    except KeyError:
        return set()
    return {p["out"] for p in plats if not p.get("default", True)}

# ── Phase 1: Python wheel targets ─────────────────────────────────────────────
# (pip --platform tag, [python versions]) — keep in sync with README.md.
TARGETS = [
    # Raspberry Pi (64-bit Pi OS) — the primary target. Python 3.14 splits to the
    # newer manylinux_2_28 baseline: numpy (Skyfield's compiled dep) ships no
    # manylinux2014 aarch64/x86_64 wheel for 3.14, only manylinux_2_28 — and any
    # glibc new enough to run CPython 3.14 already satisfies 2_28. 3.9–3.13 stay
    # on manylinux2014 (numpy 2.0.2 / 2.2.6 still publish 2014 wheels there).
    ("manylinux2014_aarch64", ["3.9", "3.10", "3.11", "3.12", "3.13"]),
    ("manylinux_2_28_aarch64", ["3.14"]),
    # Linux desktop / server.
    ("manylinux2014_x86_64",  ["3.9", "3.10", "3.11", "3.12", "3.13"]),
    ("manylinux_2_28_x86_64", ["3.14"]),
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


# ── Manifest resolved-version lockfile ─────────────────────────────────────────
def _write_resolved(feature, suite, arch, versions):
    """Write resolved package versions back into the manifest's `resolved` map.

    Shape: feature.resolved[suite][arch] = {pkg: version, ...}
    suite may be None for non-apt features (uses '_' as key).
    """
    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    suite_key = suite if suite is not None else "_"
    feat = manifest["features"].get(feature)
    if feat is None:
        return
    resolved = feat.setdefault("resolved", {})
    by_arch = resolved.setdefault(suite_key, {})
    by_arch[arch] = versions

    with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ── Version check (--check): refresh each feature's `version` in the manifest ────
def _gh_version(fetch):
    """Latest version string from a GitHub-release fetcher (graywolf/pat), or None."""
    rel = fetch()
    return (rel.get("tag_name") or rel.get("name") or "").lstrip("v") or None


# feature -> callable returning the latest upstream version string (may raise).
_VERSIONED = {
    "graywolf": lambda: _gh_version(graywolf_latest_release),
    "kiwix":    kiwix_latest_version,
    "winlink":  lambda: _gh_version(pat_latest_release),
    "webssh":   lambda: TTYD_VERSION,
    "pmtiles":  pmtiles_latest_version,
}


def check_versions():
    """Fetch each versioned feature's latest upstream version and rewrite the
    manifest's `version` field — no downloads. Returns rows of
    (feature, old, new, status) where status is:
        'updated'     — manifest had no version, or a stale one; rewritten to latest
        'current'     — manifest version already matches upstream
        'uncheckable' — could not reach upstream (left as-is; fix on --update)
    The manifest is written only if a version actually changed.
    """
    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        man = json.load(f)

    rows, changed = [], False
    for feat, fetch in _VERSIONED.items():
        node = man.get("features", {}).get(feat)
        if node is None:
            continue
        old = node.get("version") or None
        try:
            latest = fetch()
        except Exception:
            latest = None

        if latest is None:
            rows.append((feat, old, None, "uncheckable"))
        elif old == latest:
            rows.append((feat, old, latest, "current"))
        else:
            node["version"] = latest
            changed = True
            rows.append((feat, old, latest, "updated"))

    if changed:
        with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(man, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return rows


# ── Phase 1: Python wheels ─────────────────────────────────────────────────────
_PYVER_MARKER_RE = re.compile(r'python_version\s*(>=|>|==|<=|<)\s*["\']([\d.]+)["\']')


def _req_ok_for_pyver(line, pyver):
    """True if a requirements line's python_version marker (if any) admits *pyver*.

    pip's --python-version does NOT evaluate python_version markers against the
    target — it uses the running interpreter — so a version-marked pin (e.g.
    `foo; python_version >= "3.10"`) is still resolved for the py3.9 targets and
    fails ("No matching distribution"). The builder therefore pre-filters
    requirements per target instead."""
    if ";" not in line:
        return True
    comps = _PYVER_MARKER_RE.findall(line.split(";", 1)[1])
    if not comps:
        return True
    a = tuple(int(x) for x in pyver.split("."))
    # AND every python_version comparison in the marker (e.g. a `>= "3.10" and
    # < "3.14"` band); any unsatisfied constraint excludes the line.
    for op, ver in comps:
        b = tuple(int(x) for x in ver.split("."))
        if not {">=": a >= b, ">": a > b, "==": a == b, "<=": a <= b, "<": a < b}[op]:
            return False
    return True


def _requirements_for_pyver(pyver, tmp_dir):
    """Path to REQ_FILE with python_version-marker-excluded lines removed (or
    REQ_FILE unchanged when nothing is filtered)."""
    kept, filtered = [], False
    with open(REQ_FILE, encoding="utf-8") as fh:
        for raw in fh:
            s = raw.strip()
            if s and not s.startswith("#") and not _req_ok_for_pyver(s, pyver):
                filtered = True
                continue
            kept.append(raw)
    if not filtered:
        return REQ_FILE
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, f"requirements-py{pyver.replace('.', '')}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(kept)
    return path


def _resolve_target(platform, pyver, dest_dir, offline=False, find_links=None):
    """
    Run pip download for one (platform, python) target into dest_dir.
    offline=True adds --no-index --find-links find_links (CI / verify mode).
    Returns True on success.
    """
    req_file = _requirements_for_pyver(pyver, dest_dir)
    cmd = [
        sys.executable, "-m", "pip", "download",
        "-r",                req_file,
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


def phase_wheels(wheels_dir, check=False, targets=None):
    """
    Phase 1: Python wheels.
    check=True  → --no-index resolution for all targets against wheels_dir (CI mode).
    check=False → download from PyPI directly into wheels_dir.
    targets defaults to the full TARGETS matrix; pass a subset (e.g. win_amd64
    only) for a single-platform bundle.
    Returns failure count (0 = success).
    """
    targets = targets if targets is not None else TARGETS
    _section("Phase 1 — Python wheels")
    _info(f"requirements : {os.path.relpath(REQ_FILE)}")
    _info(f"wheels dir   : {os.path.relpath(wheels_dir)}")
    _info(f"targets      : {', '.join(plat for plat, _ in targets)}")
    os.makedirs(wheels_dir, exist_ok=True)

    if check:
        failures = 0
        with tempfile.TemporaryDirectory() as tmp:
            for plat, pyvers in targets:
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
    for plat, pyvers in targets:
        for pyver in pyvers:
            if not _resolve_target(plat, pyver, wheels_dir):
                failures += 1
    if failures:
        _warn(f"{failures} target(s) FAILED — check your network connection.")
    else:
        _ok(f"Wheels ready in {os.path.relpath(wheels_dir)}/")
    return failures


# ── Phase 2: GrayWolf binaries ─────────────────────────────────────────────────
def phase_graywolf(bundle_root, update=False):
    """Phase 2: Download GrayWolf .deb for each arch in the manifest.

    Reads arches from the manifest feature. Output path: M.bundle_dir(bundle_root,
    'graywolf') — no suite since this is a github-release feature.
    """
    feature    = "graywolf"
    graywolf_dir = M.bundle_dir(bundle_root, feature)
    feat       = M.get_feature(feature)
    deb_arches = M.feature_arches(feature)

    _section("Phase 2 — GrayWolf APRS binaries")
    _info("Source  : https://github.com/chrissnell/graywolf")
    _info(f"Targets : {', '.join(deb_arches)}")
    _info("Note    : Linux-only upstream — no macOS or Windows packages")
    os.makedirs(graywolf_dir, exist_ok=True)

    pinned_ver = feat.get("version")
    try:
        release = graywolf_latest_release(pinned_version=pinned_ver)
    except SystemExit:
        _warn("GrayWolf will not be available for offline install.")
        return

    latest_ver = release["tag_name"].lstrip("v")
    _info(f"GrayWolf {latest_ver} — checking")
    downloaded = []
    for deb_arch in deb_arches:
        filename  = f"graywolf_{latest_ver}_{deb_arch}.deb"
        dest_path = os.path.join(graywolf_dir, filename)
        if os.path.exists(dest_path):
            _cp(f"{filename}  (up to date)")
            downloaded.append(filename)
            _write_resolved(feature, None, deb_arch, {"graywolf": latest_ver})
            continue
        asset = next(
            (a for a in release.get("assets", []) if a["name"] == filename), None
        )
        if not asset:
            _warn(f"Asset not found: {filename} ({deb_arch}) — skipping")
            continue
        _dl(f"{filename}  ({asset['size'] / 1_048_576:.1f} MB)  ← GitHub")
        try:
            download_to(asset["browser_download_url"], dest_path)
            _ok(filename)
            downloaded.append(filename)
            _write_resolved(feature, None, deb_arch, {"graywolf": latest_ver})
        except Exception as exc:
            _warn(f"Failed: {filename}: {exc}")

    if downloaded:
        _ok(f"GrayWolf {latest_ver} ready in {os.path.relpath(graywolf_dir)}/")
    else:
        _warn("No GrayWolf assets downloaded.")


# ── Phase 3: Kiwix binaries ────────────────────────────────────────────────────
def phase_kiwix(bundle_root, update=False):
    """Phase 3: Download kiwix-tools for each arch in the manifest.

    Reads arches and base_url from the manifest feature. Output path:
    M.bundle_dir(bundle_root, 'kiwix') — no suite.
    """
    feature  = "kiwix"
    kiwix_dir = M.bundle_dir(bundle_root, feature)
    feat     = M.get_feature(feature)
    base_url = feat.get("base_url", KIWIX_BASE)

    _section("Phase 3 — Kiwix binaries")
    _info(f"Source  : {base_url}/")
    kiwix_arches = M.feature_arches(feature)
    _info(f"Targets : {', '.join(kiwix_arches)}")
    os.makedirs(kiwix_dir, exist_ok=True)

    latest_ver = kiwix_latest_version()
    if latest_ver is None:
        _warn("Could not determine latest Kiwix version — skipping.")
        return

    _info(f"Kiwix {latest_ver} — checking")
    downloaded = []
    for kiwix_arch in kiwix_arches:
        filename  = f"kiwix-tools_linux-{kiwix_arch}-{latest_ver}.tar.gz"
        dest_path = os.path.join(kiwix_dir, filename)
        if os.path.exists(dest_path):
            _cp(f"{filename}  (up to date)")
            downloaded.append(filename)
            _write_resolved(feature, None, kiwix_arch, {"kiwix-tools": latest_ver})
            continue
        try:
            path = kiwix_download_tarball(kiwix_dir, latest_ver, kiwix_arch)
            downloaded.append(os.path.basename(path))
            _write_resolved(feature, None, kiwix_arch, {"kiwix-tools": latest_ver})
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
    (
        "aprs-symbols-64-0.png",
        "https://github.com/hessu/aprs-symbols/raw/master/png/aprs-symbols-64-0.png",
        "Primary APRS symbol table, 64px (table '/')",
    ),
    (
        "aprs-symbols-64-1.png",
        "https://github.com/hessu/aprs-symbols/raw/master/png/aprs-symbols-64-1.png",
        "Alternate APRS symbol table, 64px (table '\\')",
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


# ── Phase 5: RTL-SDR Debian packages (suite-aware) ────────────────────────────
#
# The manifest has three apt features that all share bundle_group "rtl-sdr":
#   rtl-sdr        — driver + librtlsdr (librtlsdr0 on bookworm, librtlsdr2 on trixie)
#   rtl-sdr-feed   — socat, tcpdump, and shared-lib deps
#   rtl-sdr-diag   — multimon-ng (best-effort, diagnostic only)
#
# Each is vendored per suite per arch into:
#   features/rtl-sdr/packages/rtl-sdr/<suite>/  (bundle_base + bundle_group "rtl-sdr")
#
# The "up to date" check is now per-suite, keyed on suite-correct package names.

_RTL_SDR_APT_FEATURES = ["rtl-sdr", "rtl-sdr-feed", "rtl-sdr-diag"]


def _rtl_sdr_suite_dir(bundle_root, suite):
    """Return the per-suite output directory for all rtl-sdr apt features."""
    # All three features share bundle_group "rtl-sdr", so any of them gives the
    # same bundle_dir. Pass suite so M.bundle_dir appends it.
    return M.bundle_dir(bundle_root, "rtl-sdr", suite=suite)


def _rtl_sdr_all_packages(suite):
    """Collect all apt package names for the given suite across all rtl-sdr features."""
    pkgs = []
    for feature in _RTL_SDR_APT_FEATURES:
        for p in M.apt_packages(feature, suite=suite):
            if p not in pkgs:
                pkgs.append(p)
    return pkgs


def _rtl_sdr_suite_present(suite_dir, suite, deb_arch):
    """True when the suite+arch slot looks complete (driver + feed tools present)."""
    if not os.path.isdir(suite_dir):
        return False
    have = os.listdir(suite_dir)
    def _present(prefix):
        return any(f.startswith(prefix) and f.endswith(f"_{deb_arch}.deb")
                   for f in have)

    # librtlsdr package name differs by suite.
    rtlsdr_lib = "librtlsdr0" if suite == "bookworm" else "librtlsdr2"
    return (
        _present("rtl-sdr_")
        and _present(f"{rtlsdr_lib}_")
        and _present("socat_")
        and _present("tcpdump_")
    )


def phase_rtl_sdr(bundle_root, update=False):
    """Phase 5: Download RTL-SDR apt packages per suite per arch.

    Iterates every suite and arch declared for apt features in the manifest. For
    each (suite, arch) slot that is not already complete, calls
    rtl_sdr_download_debs with the suite-correct package list and writes resolved
    versions back into the manifest.
    """
    _section("Phase 5 — RTL-SDR Debian packages  (suite-aware)")

    for suite in M.feature_suites("rtl-sdr"):
        pkgs = _rtl_sdr_all_packages(suite)
        suite_dir = _rtl_sdr_suite_dir(bundle_root, suite)
        _info(f"Suite   : Debian {suite}")
        _info(f"Packages: {', '.join(pkgs)}")
        _info(f"Dest    : {os.path.relpath(suite_dir)}/")

        for deb_arch in M.feature_arches("rtl-sdr"):
            _info(f"  ─── {suite}/{deb_arch}")
            if _rtl_sdr_suite_present(suite_dir, suite, deb_arch):
                _cp(f"rtl-sdr {suite}/{deb_arch}: present  (up to date)")
                continue

            rtl_sdr_download_debs(
                suite_dir, deb_arch, packages=pkgs, suite=suite
            )

            # Write resolved versions back into the manifest for each feature.
            # Fetch the index once more (it may already be cached in memory by
            # rtl_sdr_download_debs, but we need the version strings).
            pkg_index = debian_packages_index(deb_arch, pkgs, suite=suite)
            for feature in _RTL_SDR_APT_FEATURES:
                feature_pkgs = M.apt_packages(feature, suite=suite)
                versions = {
                    p: pkg_index[p]["Version"]
                    for p in feature_pkgs
                    if p in pkg_index and pkg_index[p].get("Version")
                }
                if versions:
                    _write_resolved(feature, suite, deb_arch, versions)

        _ok(f"RTL-SDR {suite} done  →  {os.path.relpath(suite_dir)}/")


# ── Phase 5b: Direwolf (Winlink RF modem) Debian packages (suite-aware) ────────
#
# The Winlink RF transport. Vendored per suite per arch into
#   features/rtl-sdr/packages/direwolf/<suite>/   (bundle_base + bundle_group "direwolf")
# Only the packages the manifest names are fetched — no dependency-closure
# resolution — so Direwolf's runtime deps (libhamlib*, etc.) must be curated in
# the manifest's by_suite list on the connected build machine (see the feature
# '_note'). Most deps are already on a stock Pi OS image.
def _direwolf_present(suite_dir, deb_arch):
    """True when the direwolf .deb for this arch is already bundled."""
    if not os.path.isdir(suite_dir):
        return False
    return any(f.startswith("direwolf_") and f.endswith(f"_{deb_arch}.deb")
               for f in os.listdir(suite_dir))


def phase_direwolf(bundle_root, update=False):
    """Download Direwolf apt package(s) per suite per arch, writing resolved."""
    feature = "direwolf"
    _section("Phase 5b — Direwolf (Winlink RF modem) Debian packages  (suite-aware)")

    for suite in M.feature_suites(feature):
        pkgs = M.apt_packages(feature, suite=suite)
        suite_dir = M.bundle_dir(bundle_root, "direwolf", suite=suite)
        _info(f"Suite   : Debian {suite}")
        _info(f"Packages: {', '.join(pkgs)}")
        _info(f"Dest    : {os.path.relpath(suite_dir)}/")

        for deb_arch in M.feature_arches(feature):
            _info(f"  ─── {suite}/{deb_arch}")
            if _direwolf_present(suite_dir, deb_arch):
                _cp(f"direwolf {suite}/{deb_arch}: present  (up to date)")
                continue

            rtl_sdr_download_debs(suite_dir, deb_arch, packages=pkgs, suite=suite)

            pkg_index = debian_packages_index(deb_arch, pkgs, suite=suite)
            versions = {
                p: pkg_index[p]["Version"]
                for p in pkgs
                if p in pkg_index and pkg_index[p].get("Version")
            }
            if versions:
                _write_resolved(feature, suite, deb_arch, versions)

        _ok(f"Direwolf {suite} done  →  {os.path.relpath(suite_dir)}/")


# ── Phase 5c: Satellites pass-alert voice (TTS) Debian packages (suite-aware) ──
#
# The speech-dispatcher + espeak-ng stack the Satellites page uses for the spoken
# pass alert. Vendored per suite per arch into
#   services/satellites/packages/satellites-voice/<suite>/   (bundle_base + bundle_group)
# Like Direwolf, only the packages the manifest names are fetched — no
# dependency-closure resolution — so runtime deps (libspeechd2, libsonic0, …) must
# be curated in the manifest's by_suite list on the connected build machine (see
# the feature '_note'). The voice is optional, so a missing bundle is non-fatal.
def _satellites_voice_present(suite_dir, deb_arch):
    """True when the core speech-dispatcher .deb for this arch is already bundled."""
    if not os.path.isdir(suite_dir):
        return False
    return any(f.startswith("speech-dispatcher_") and f.endswith(f"_{deb_arch}.deb")
               for f in os.listdir(suite_dir))


def phase_satellites_voice(bundle_root, update=False):
    """Download the pass-alert voice apt packages per suite per arch, writing resolved."""
    feature = "satellites-voice"
    _section("Phase 5c — Satellites pass-alert voice (TTS) Debian packages  (suite-aware)")

    for suite in M.feature_suites(feature):
        pkgs = M.apt_packages(feature, suite=suite)
        suite_dir = M.bundle_dir(bundle_root, feature, suite=suite)
        _info(f"Suite   : Debian {suite}")
        _info(f"Packages: {', '.join(pkgs)}")
        _info(f"Dest    : {os.path.relpath(suite_dir)}/")

        for deb_arch in M.feature_arches(feature):
            _info(f"  ─── {suite}/{deb_arch}")
            if _satellites_voice_present(suite_dir, deb_arch):
                _cp(f"satellites-voice {suite}/{deb_arch}: present  (up to date)")
                continue

            rtl_sdr_download_debs(suite_dir, deb_arch, packages=pkgs, suite=suite)

            pkg_index = debian_packages_index(deb_arch, pkgs, suite=suite)
            versions = {
                p: pkg_index[p]["Version"]
                for p in pkgs
                if p in pkg_index and pkg_index[p].get("Version")
            }
            if versions:
                _write_resolved(feature, suite, deb_arch, versions)

        _ok(f"Satellites voice {suite} done  →  {os.path.relpath(suite_dir)}/")


# ── Phase 7a: ADS-B (dump1090-fa) Debian package (suite-aware) ───────────────
FA_BASE_URL = "https://www.flightaware.com/adsb/piaware/files/packages"


def _fa_packages_records(suite, deb_arch):
    """Return parsed package records from FlightAware apt index for suite/arch."""
    candidates = [
        f"{FA_BASE_URL}/dists/{suite}/piaware/binary-{deb_arch}/Packages.gz",
        f"{FA_BASE_URL}/dists/{suite}/piaware/binary-{deb_arch}/Packages",
    ]
    text = None
    for url in candidates:
        data, _err = download_bytes(url)
        if data is None:
            continue
        try:
            if url.endswith(".gz"):
                text = gzip.decompress(data).decode("utf-8", errors="replace")
            else:
                text = data.decode("utf-8", errors="replace")
            break
        except Exception:
            continue
    if not text:
        return []

    records = []
    for stanza in text.split("\n\n"):
        rec = {}
        for line in stanza.splitlines():
            if not line or line.startswith(" "):
                continue
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            rec[key.strip()] = val.strip()
        if rec.get("Package"):
            records.append(rec)
    return records


def _fa_best_dump1090_record(suite, deb_arch):
    recs = [r for r in _fa_packages_records(suite, deb_arch) if r.get("Package") == "dump1090-fa"]
    if not recs:
        return None
    best = recs[0]
    for rec in recs[1:]:
        if M.vcmp(rec.get("Version", "0"), best.get("Version", "0")) > 0:
            best = rec
    return best


def phase_satellites_roster(bundle_root):
    """Satellite roster: run build-roster.py into the bundle's configuration/.

    The satellite map reads configuration/satellites.json — the curated roster with
    per-sat labels + downlinks. It's a BUILD-TIME artifact: build-roster fetches
    SatNOGS metadata + CelesTrak TLEs (needs internet HERE, on the build host) and
    writes the roster + TLE cache the Pi then serves fully offline. Without this the
    Pi ships an empty roster and the map degrades to bare, label-less TLE-only sats.

    build-roster no-ops on ANY fetch failure (leaves existing data intact), so an
    offline build is a warning — never a wipe. Writes straight into the bundle via
    --config/--cache, so the maintainer's own repo configuration/ is left untouched.
    """
    _section("Phase — Satellite roster (SatNOGS + CelesTrak)")
    cfg_dir     = os.path.join(bundle_root, "configuration")
    roster_json = os.path.join(cfg_dir, "satellites.json")
    tle_cache   = os.path.join(cfg_dir, "tle-cache")
    _info("Source  : SatNOGS DB + CelesTrak TLE groups")
    _info(f"Dest    : {os.path.relpath(roster_json)}  (+ tle-cache/)")
    os.makedirs(tle_cache, exist_ok=True)

    cmd = [sys.executable,
           os.path.join(REPO_ROOT, "services", "satellites", "build-roster.py"),
           "--config", roster_json, "--cache", tle_cache]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        try:
            with open(roster_json) as fh:
                n = len(json.load(fh).get("satellites", []))
        except Exception:
            n = "?"
        _ok(f"roster built — {n} satellites")
    else:
        tail = (result.stderr or result.stdout).strip().splitlines()
        _warn("build-roster could not fetch (offline build?) — "
              + (tail[-1] if tail else "existing roster left as-is"))
        if not os.path.exists(roster_json):
            _warn("no roster shipped — the Pi falls back to bare TLE-only sats "
                  "until its first online refresh")


def phase_adsb(bundle_root, update=False):
    """Phase 7a: Download dump1090-fa .deb per suite/arch into ADS-B bundle path."""
    feature = "dump1090-fa"
    _section("Phase 7a — ADS-B dump1090-fa Debian packages  (suite-aware)")
    _info(f"Source  : {FA_BASE_URL}")

    try:
        suites = M.feature_suites(feature)
        arches = M.feature_arches(feature)
    except KeyError:
        _warn("Manifest feature 'dump1090-fa' missing — skipping ADS-B vendoring.")
        return

    for suite in suites:
        suite_dir = M.bundle_dir(bundle_root, feature, suite=suite)
        _info(f"Suite   : Debian {suite}")
        _info(f"Dest    : {os.path.relpath(suite_dir)}/")
        os.makedirs(suite_dir, exist_ok=True)

        for deb_arch in arches:
            _info(f"  ─── {suite}/{deb_arch}")
            present = [
                f for f in os.listdir(suite_dir)
                if f.startswith("dump1090-fa_") and f.endswith(f"_{deb_arch}.deb")
            ]
            if present:
                _cp(f"dump1090-fa {suite}/{deb_arch}: present  (up to date)")
                continue

            rec = _fa_best_dump1090_record(suite, deb_arch)
            if not rec:
                _warn(f"No dump1090-fa package found for {suite}/{deb_arch} in FlightAware index.")
                continue
            rel = rec.get("Filename") or ""
            ver = rec.get("Version") or ""
            if not rel:
                _warn(f"Index entry missing Filename for {suite}/{deb_arch} — skipping")
                continue
            url = rel if rel.startswith("http") else f"{FA_BASE_URL}/{rel.lstrip('/')}"
            out = os.path.join(suite_dir, os.path.basename(rel))
            _dl(f"{os.path.basename(out)}  ({suite}/{deb_arch})  ← FlightAware")
            try:
                download_to(url, out)
                _ok(os.path.basename(out))
                _write_resolved(feature, suite, deb_arch, {"dump1090-fa": ver})
            except Exception as exc:
                if os.path.exists(out):
                    try:
                        os.remove(out)
                    except OSError:
                        pass
                _warn(f"Failed to download dump1090-fa for {suite}/{deb_arch}: {exc}")

        _ok(f"ADS-B {suite} done  →  {os.path.relpath(suite_dir)}/")


# ── Phase 6: webssh (ttyd) static binaries ────────────────────────────────────
def phase_webssh(bundle_root, update=False):
    """Phase 6: Download the ttyd prebuilt static binaries for each arch in the manifest.

    Reads arches and pinned version from the manifest feature. Output:
    M.bundle_dir(bundle_root, 'webssh') — no suite.
    """
    feature    = "webssh"
    webssh_dir = M.bundle_dir(bundle_root, feature)
    feat       = M.get_feature(feature)
    ttyd_ver   = feat.get("version", TTYD_VERSION)
    ttyd_arches = M.feature_arches(feature)

    _section("Phase 6 — webssh (ttyd) static binaries")
    _info(f"Version : ttyd {ttyd_ver}")
    _info(f"Targets : {', '.join(ttyd_arches)}")
    os.makedirs(webssh_dir, exist_ok=True)

    got = 0
    for suffix in ttyd_arches:
        dest = os.path.join(webssh_dir, f"ttyd.{suffix}")
        if os.path.exists(dest):
            _cp(f"ttyd.{suffix}  (up to date)")
            got += 1
            _write_resolved(feature, None, suffix, {"ttyd": ttyd_ver})
            continue
        if ttyd_download(webssh_dir, suffix, version=ttyd_ver) is not None:
            got += 1
            _write_resolved(feature, None, suffix, {"ttyd": ttyd_ver})

    if got:
        _ok(f"{got}/{len(ttyd_arches)} ttyd binaries ready in "
            f"{os.path.relpath(webssh_dir)}/")
    else:
        _warn("No ttyd binaries downloaded — webssh offline install will not work.")


def _file_sha256(path):
    """SHA-256 of a (possibly large) file, streamed in 1 MiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Phase 9: pmtiles CLI (MBTiles → PMTiles converter) ────────────────────────
def phase_pmtiles(maps_dir, update=False, all_platforms=False):
    """Phase 9: Download the Protomaps go-pmtiles CLI for each platform in the
    manifest into the bundle's maps/ directory.

    Each release archive is fetched, the pmtiles executable is extracted, and it
    is written as maps/<out> (e.g. pmtiles-linux-arm64). maps/convert-mbtiles.py
    auto-detects the right one for the host so an operator can convert legacy
    MBTiles to PMTiles in the field with no internet. Only the default (Linux)
    platforms are fetched unless all_platforms is set (--all-platforms).
    """
    feature = "pmtiles"
    try:
        feat = M.get_feature(feature)
    except KeyError:
        _warn("pmtiles feature not in manifest — skipping.")
        return

    version   = feat.get("version", PMTILES_VERSION)
    repo      = feat.get("repo", "protomaps/go-pmtiles")
    platforms = _pmtiles_platforms(all_platforms)
    base_url  = f"https://github.com/{repo}/releases/download/v{version}"

    _section("Phase 9 — pmtiles CLI (MBTiles → PMTiles converter)")
    _info(f"Source  : https://github.com/{repo}")
    _info(f"Version : pmtiles {version}")
    _info(f"Targets : {', '.join(p['out'] for p in platforms)}"
          + ("" if all_platforms else "  (Linux only; use --all-platforms for macOS/Windows)"))
    _info(f"Dest    : {os.path.relpath(maps_dir)}/")
    os.makedirs(maps_dir, exist_ok=True)

    got = 0
    for plat in platforms:
        out_name = plat["out"]
        dest     = os.path.join(maps_dir, out_name)
        if os.path.exists(dest):
            _cp(f"{out_name}  (up to date)")
            got += 1
            _write_resolved(feature, None, out_name, {"pmtiles": version})
            continue
        asset = plat["asset"].format(version=version)
        url   = f"{base_url}/{asset}"
        if pmtiles_download_binary(maps_dir, url, out_name) is not None:
            got += 1
            _write_resolved(feature, None, out_name, {"pmtiles": version})

    if got:
        _ok(f"{got}/{len(platforms)} pmtiles binaries ready in "
            f"{os.path.relpath(maps_dir)}/")
    else:
        _warn("No pmtiles binaries downloaded — offline MBTiles→PMTiles conversion "
              "will be unavailable.")


# ── Phase 7: Pat (Winlink) Debian packages ───────────────────────────────────
# Pat is still a github-release shape. Read arches from the manifest if a
# 'pat' feature is present; fall back to the hardcoded list.
_PAT_TARGETS_DEFAULT = [
    ("arm64", "Raspberry Pi (64-bit)"),
    ("armhf", "Raspberry Pi (32-bit)"),
    ("amd64", "Linux x86-64"),
]


def phase_pat(bundle_root, update=False):
    """Phase 7: Download the Pat (Winlink) .deb for each arch.

    Output: M.bundle_dir(bundle_root, 'pat') if 'pat' is in the manifest,
    otherwise the legacy path offline-packages/pat/. No suite.
    """
    _section("Phase 7 — Pat (Winlink) binaries")

    # Use manifest-declared arches if the feature exists; degrade gracefully.
    # Feature key is 'winlink' (bundle_group 'pat' → offline-packages/pat/).
    try:
        pat_arches = [(a, "") for a in M.feature_arches("winlink")]
        pat_dir    = M.bundle_dir(bundle_root, "winlink")
        have_feat  = True
    except KeyError:
        pat_arches = _PAT_TARGETS_DEFAULT
        pat_dir    = os.path.join(bundle_root, "offline-packages", "pat")
        have_feat  = False

    _info("Source  : https://github.com/la5nta/pat")
    _info(f"Targets : {', '.join(a for a, _ in pat_arches)}")
    _info("Note    : Linux .deb only here — services/winlink/install.py consumes these")
    os.makedirs(pat_dir, exist_ok=True)

    try:
        release = pat_latest_release()
    except SystemExit:
        _warn("Pat will not be available for offline install.")
        return

    latest_ver = release["tag_name"].lstrip("v")
    _info(f"Pat {latest_ver} — checking")
    downloaded = []
    for deb_arch, desc in pat_arches:
        filename  = f"pat_{latest_ver}_linux_{deb_arch}.deb"
        dest_path = os.path.join(pat_dir, filename)
        if os.path.exists(dest_path):
            _cp(f"{filename}  (up to date)")
            downloaded.append(filename)
            if have_feat:
                _write_resolved("winlink", None, deb_arch, {"pat": latest_ver})
            continue
        asset = next(
            (a for a in release.get("assets", []) if a["name"] == filename), None
        )
        if not asset:
            _warn(f"Asset not found: {filename} ({deb_arch}) — skipping")
            continue
        _dl(f"{filename}  ({asset['size'] / 1_048_576:.1f} MB)  ← GitHub")
        try:
            download_to(asset["browser_download_url"], dest_path)
            _ok(filename)
            downloaded.append(filename)
            if have_feat:
                _write_resolved("winlink", None, deb_arch, {"pat": latest_ver})
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


# M5Stack CM4Stack panel overlay — a prebuilt, kernel-portable device-tree blob.
# Lands in the bundle's overlays/ alongside the DRAWS blobs, so every overlay
# OASIS ships has ONE home and one installer (common/overlays.py). Installed on
# the target by displays/cm4stack/install-cm4stack.py. aw88xx.dtbo is
# intentionally NOT fetched (no working arm64 driver — see
# displays/cm4stack/cm4stack-oasis-panel.md §7).
M5STACK_OVERLAY_URL = (
    "https://raw.githubusercontent.com/m5stack/m5stack-linux-dtoverlays/"
    "main/overlays/cm4stack/bin/m5stack-cm4.dtbo"
)


def phase_cm4stack(bundle_root, update=False):
    """Ensure m5stack-cm4.dtbo is in the bundle's overlays/ for offline install.

    Single arch-independent file (a device-tree overlay), so no suite/arch split.

    It lands in overlays/ rather than displays/cm4stack/packages/ because every
    overlay OASIS ships now has one home and one installer (common/overlays.py).
    The repo already tracks a copy, so on a normal build this phase finds it and
    does nothing — the download is the path for a tree where the blob was
    deliberately removed, and the way to refresh it is to delete it and rebuild.
    bundle_root is <out>/offline-packages, so go up one to the bundle root.
    """
    dest_dir = os.path.join(os.path.dirname(bundle_root), "overlays")
    dest     = os.path.join(dest_dir, "m5stack-cm4.dtbo")

    _section("Phase — CM4Stack panel overlay")
    _info("Source  : github.com/m5stack/m5stack-linux-dtoverlays (overlays/cm4stack/bin)")

    if os.path.exists(dest):
        _cp("m5stack-cm4.dtbo  (already present)")
        return
    os.makedirs(dest_dir, exist_ok=True)
    _dl("m5stack-cm4.dtbo  ← GitHub")
    try:
        download_to(M5STACK_OVERLAY_URL, dest)
        _ok("m5stack-cm4.dtbo")
    except Exception as exc:
        _warn(f"Could not download m5stack-cm4.dtbo: {exc}")
        _warn("CM4Stack panel setup will need it fetched manually on the target.")


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
    Phase 4: Download l_amat.zip, extract EN.dat/HD.dat, AND prebuild the search
    indexes on this (capable) build host, so the Pi never has to run the
    RAM-heavy sort — nor the extraction it can't afford either.

    The bundle ships the raw EN.dat + HD.dat + EN.idx / EN_name.idx / EN_grid.idx
    + zipcodes.csv + EN.idx.meta, and DROPS l_amat.zip. The zip only ever existed
    to carry EN.dat to a machine capable of unpacking it; since we've already done
    the extraction and indexing here, the target uses the shipped files as-is.
    Copying a ~175 MB zip to the Pi just to delete it on first setup is waste.
    The zip is dropped IF AND ONLY IF the prebuilt indexes verify against the
    shipped EN.dat; otherwise it is kept as a recovery copy.
    """
    _section("Phase 4 — FCC callsign database")

    os.makedirs(fcc_dir, exist_ok=True)
    zip_path   = os.path.join(fcc_dir, "l_amat.zip")
    en_dat     = os.path.join(fcc_dir, "EN.dat")
    hd_dat     = os.path.join(fcc_dir, "HD.dat")
    server_dir = os.path.join(REPO_ROOT, "server")

    # 1 · Raw ULS zip (skip if already downloaded). This is a transient build
    #     artifact — it is dropped in step 3 once the .dat files + indexes exist.
    #     Because step 3 drops the zip, a re-run won't find it here; but the
    #     EN.dat/HD.dat it exists only to produce are kept, so skip the ~160 MB
    #     re-download whenever those extracted files are already present.
    if os.path.exists(zip_path):
        _cp("l_amat.zip already present — skipping download")
    elif os.path.exists(en_dat) and os.path.exists(hd_dat):
        _cp("EN.dat/HD.dat already extracted — skipping l_amat.zip download")
    else:
        try:
            fcc_download_zip(zip_path)
            _ok(f"l_amat.zip saved → {os.path.relpath(zip_path)}")
        except SystemExit:
            _warn("FCC download failed — callsign lookup will not work offline.")
            _warn("Re-run this script when the FCC site is reachable.")
            return

    # 2 · Extract EN.dat/HD.dat from the zip (cheap, streaming) so they can be
    #     shipped directly, then prebuild the indexes here so the Pi doesn't have
    #     to. fcc_download is idempotent — it skips extraction if EN.dat exists.
    try:
        fcc_download(fcc_dir)                      # extract EN.dat/HD.dat from the zip
    except SystemExit:
        _warn("Could not extract EN.dat/HD.dat from l_amat.zip.")
        _warn("Re-run this script when the FCC site is reachable.")
        return

    have_idx = (os.path.exists(os.path.join(fcc_dir, FCC_INDEX_META))
                and all(os.path.exists(os.path.join(fcc_dir, n)) for n in FCC_INDEX_NAMES)
                and os.path.exists(os.path.join(fcc_dir, "zipcodes.csv")))
    if have_idx:
        _cp("Prebuilt indexes already present — skipping rebuild")
    else:
        try:
            fcc_build_zip_table(fcc_dir)           # zipcodes.csv (needed for the grid index)
            fcc_build_index(fcc_dir, server_dir)   # EN.idx + EN_name.idx + EN_grid.idx + meta
        except SystemExit:
            _warn("Could not prebuild FCC indexes — the target will build them on")
            _warn("first setup (RAM-heavy on the Pi). Re-run this script online.")
            return
        if not fcc_indexes_ready(fcc_dir):
            _warn("Index prebuild incomplete — shipping the zip as a fallback; the")
            _warn("target will build indexes on first setup.")
            return
        _ok("Prebuilt EN.idx / EN_name.idx / EN_grid.idx + zipcodes.csv")

    # 3 · Ship EN.dat + HD.dat + prebuilt indexes directly, and DROP l_amat.zip.
    #     The zip was only a carrier for EN.dat — now redundant on the target,
    #     where it would otherwise be ~175 MB of bloat copied only to be deleted
    #     on first setup. Drop it IF AND ONLY IF the indexes verify against the
    #     shipped EN.dat (size + SHA-256 via EN.idx.meta); a failed/partial build
    #     keeps the zip so the target can still recover.
    if fcc_indexes_ready(fcc_dir):
        if os.path.exists(zip_path):
            os.remove(zip_path)
        _ok("Bundle ships EN.dat + HD.dat + prebuilt indexes (l_amat.zip dropped)")
    else:
        _warn("Indexes not verified against EN.dat — keeping l_amat.zip as recovery.")

# ── Build: copy project files ──────────────────────────────────────────────────
def load_ignore(profile="full"):
    """Load bundle-ignore patterns (base + optional per-profile overlay).

    Returns a flat list of gitignore-style patterns with comments/blank lines
    stripped and trailing/leading slashes normalised away. Missing files are
    skipped so a profile without an overlay just uses the base list.
    """
    paths = [BUNDLE_IGNORE]
    extra = BUNDLE_IGNORE_EXTRA.get(profile)
    if extra:
        paths.append(extra)
    patterns = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                s = s.strip("/")           # dir marker / leading anchor — we exclude either way
                if s:
                    patterns.append(s)
    return patterns


def is_ignored(rel, patterns):
    """True if the src-relative path `rel` matches any bundle-ignore pattern.

    A bare pattern (no '/') matches any path segment at any depth (so `__pycache__`
    prunes every such dir, `*.pyc` every such file); a pattern containing '/' is
    anchored to the bundle root (`server/wheels`). fnmatch globs work in both.
    """
    rel = rel.replace(os.sep, "/")
    segments = rel.split("/")
    for pat in patterns:
        if "/" in pat:
            if rel == pat or rel.startswith(pat + "/") or fnmatch.fnmatch(rel, pat):
                return True
        elif any(fnmatch.fnmatch(seg, pat) for seg in segments):
            return True
    return False


def _reap_orphans(dest, keep_rel):
    """Delete files in dest that build_copy did NOT just write and that are not
    owned by the download/build phases (PRESERVE_IN_DEST), then prune the dirs
    that leaves empty.

    This makes an incremental build behave like a mirror for the *source* tree:
    files renamed or removed in the repo stop lingering in the bundle (the old
    "stale start-server.bat / ghost file" problem) — while the heavy downloaded
    assets (offline-packages/, wheels/, zim/, maps/, _runtime/, …) are untouched.
    keep_rel is the set of dest-relative paths (normpath'd) build_copy just wrote.
    """
    preserve = {p.replace("/", os.sep) for p in PRESERVE_IN_DEST}

    def is_preserved(rel):
        parts = rel.split(os.sep)
        return any(os.sep.join(parts[:i]) in preserve for i in range(1, len(parts) + 1))

    reaped = 0
    for root, dirs, files in os.walk(dest, topdown=True):
        rel_root = os.path.relpath(root, dest)
        rel_root = "" if rel_root == "." else rel_root
        # Don't descend into preserved subtrees (offline-packages/, zim/, …).
        dirs[:] = [d for d in dirs
                   if not is_preserved(os.path.normpath(os.path.join(rel_root, d)))]
        for fname in files:
            rel = os.path.normpath(os.path.join(rel_root, fname))
            if rel in keep_rel or is_preserved(rel):
                continue
            try:
                os.remove(os.path.join(root, fname))
                reaped += 1
            except OSError:
                pass

    # Prune directories emptied by the reap (bottom-up; never the bundle root or
    # a preserved subtree). rmdir only removes already-empty dirs, so it's safe.
    for root, _dirs, _files in os.walk(dest, topdown=False):
        rel_root = os.path.relpath(root, dest)
        if rel_root == "." or is_preserved(os.path.normpath(rel_root)):
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except OSError:
            pass

    return reaped


# OS-generated metadata files that must never ship in a bundle — swept from the
# ENTIRE dest tree (including PRESERVE_IN_DEST subtrees like maps/ and zim/,
# which _reap_orphans skips) since Finder/Explorer can drop these into any
# directory a user browses, including ones populated by the download phases.
JUNK_FILE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _remove_junk_files(dest):
    """Delete OS metadata junk files anywhere under dest. Returns count removed."""
    removed = 0
    for root, _dirs, files in os.walk(dest):
        for fname in files:
            if fname in JUNK_FILE_NAMES:
                try:
                    os.remove(os.path.join(root, fname))
                    removed += 1
                except OSError:
                    pass
    return removed


def build_copy(dest, src=None, include_all_platforms=False, ignore=None, warn_missing_pi=True):
    """
    Copy the repo source tree into dest, honouring the bundle-ignore patterns.
    Package directories managed by the download phases (offline-packages/,
    server/wheels/, …) are excluded there — they are populated in dest by the
    download phases, not copied.
    Mirrors the source tree: after copying, orphaned files in dest (removed or
    renamed in the repo) are reaped, except for the download/build-managed
    subtrees in PRESERVE_IN_DEST. Does not wipe dest wholesale.
    src defaults to REPO_ROOT; pass a different path when calling from inside
    the bundle (where REPO_ROOT is the bundle itself).
    ignore defaults to the base (full-profile) bundle-ignore patterns; pass a
    profile-specific list (see load_ignore) for a leaner bundle.
    Non-default pmtiles binaries (macOS/Windows) are skipped unless
    include_all_platforms is set, so a committed repo copy doesn't bloat a
    Linux-only bundle (--all-platforms re-adds them via the download phase).
    """
    src = src or REPO_ROOT
    patterns = load_ignore() if ignore is None else ignore
    _section(f"Copying source files  →  {dest}")

    drop_pmtiles = set() if include_all_platforms else _pmtiles_nondefault_outs()
    copied = skipped = 0
    keep_rel = set()   # dest-relative paths we write — used to reap orphans below
    for root, dirs, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        rel_root = "" if rel_root == "." else rel_root
        # Prune ignored (and dotfile) directories so we never descend into them.
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and not is_ignored(os.path.join(rel_root, d) if rel_root else d, patterns)
        ]
        for fname in files:
            rel = os.path.join(rel_root, fname) if rel_root else fname
            if fname in drop_pmtiles or is_ignored(rel, patterns):
                skipped += 1
                continue
            dest_path = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(os.path.join(root, fname), dest_path)
            keep_rel.add(os.path.normpath(rel))
            copied += 1

    reaped = _reap_orphans(dest, keep_rel)
    junked = _remove_junk_files(dest)
    reap_note = f", {reaped} stale reaped" if reaped else ""
    junk_note = f", {junked} junk removed" if junked else ""
    _cp(f"{copied} files copied from repo to bundle  ({skipped} excluded{reap_note}{junk_note})")

    # Report anything that will hurt the Pi offline experience. Meaningless for the
    # tools-only bundle (which ships none of these), so callers pass warn_missing_pi=False.
    if not warn_missing_pi:
        return
    # RTL-SDR is now split into per-suite subdirs; check at least one suite exists.
    gw_dir = M.bundle_dir(dest, "graywolf")
    kw_dir = M.bundle_dir(dest, "kiwix")
    ws_dir = M.bundle_dir(dest, "webssh")
    # Pat may or may not be in the manifest; use the same logic as phase_pat.
    try:
        pt_dir = M.bundle_dir(dest, "pat")
    except KeyError:
        pt_dir = os.path.join(dest, "offline-packages", "pat")

    if not any(f.endswith(".deb") for f in (os.listdir(gw_dir) if os.path.isdir(gw_dir) else [])):
        _warn("GrayWolf .deb missing — Pi users will need internet to install GrayWolf.")
    if not any(f.endswith(".tar.gz") for f in (os.listdir(kw_dir) if os.path.isdir(kw_dir) else [])):
        _warn("Kiwix binary missing — Pi users will need internet to install Kiwix.")

    # RTL-SDR: verify at least one suite/arch slot has the driver .deb.
    rs_base = M.bundle_dir(dest, "rtl-sdr")
    rs_debs = []
    if os.path.isdir(rs_base):
        for suite in M.feature_suites("rtl-sdr"):
            suite_dir = os.path.join(rs_base, suite)
            if os.path.isdir(suite_dir):
                rs_debs += [f for f in os.listdir(suite_dir) if f.endswith(".deb")]
    if not any(f.startswith("rtl-sdr_") for f in rs_debs):
        _warn("RTL-SDR .deb packages missing — Pi users will need internet to install RTL-SDR.")
    if not any(f.startswith("socat_") for f in rs_debs):
        _warn("socat/tcpdump .deb packages missing — Pi users will need internet to enable the RTL-SDR feed.")

    if not any(f.startswith("ttyd.") for f in (os.listdir(ws_dir) if os.path.isdir(ws_dir) else [])):
        _warn("webssh (ttyd) static binaries missing — Pi users will need internet to install webssh.")
    if not any(f.endswith(".deb") for f in (os.listdir(pt_dir) if os.path.isdir(pt_dir) else [])):
        _warn("Pat .deb missing — Winlink users will need internet to install Pat.")

    ad_base = M.bundle_dir(dest, "dump1090-fa")
    ad_debs = []
    if os.path.isdir(ad_base):
        for suite in M.feature_suites("dump1090-fa"):
            suite_dir = os.path.join(ad_base, suite)
            if os.path.isdir(suite_dir):
                ad_debs += [f for f in os.listdir(suite_dir) if f.endswith(".deb")]
    if not any(f.startswith("dump1090-fa_") for f in ad_debs):
        _warn("ADS-B dump1090-fa .deb missing — ADS-B users will need internet to install decoder.")


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
        # gunicorn is a pure (none-any) wheel but POSIX-only — it imports fcntl
        # and needs os.fork(), so it must never be vendored into the Windows
        # runtime (app.py won't launch it on win32, but extracting it is dead
        # weight and misleading). Skip it explicitly.
        if lower.startswith("gunicorn"):
            continue
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

    if skip:
        _warn("Windows embedded runtime not requested (--for-windows). The bundle's "
              "start-server.bat will fall back to system Python on the target; pass "
              "--for-windows to ship a self-contained runtime.")
        return

    os.makedirs(win_dir, exist_ok=True)

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


# The "windows" (tools-only) bundle boots in the portable profile: only the
# daemon-free tools are shown, matching run-portable.bat's OASIS_FEATURES.
PORTABLE_FEATURES = "fcc,forms,repeaterbook"


# ── Build: launchers ───────────────────────────────────────────────────────────
def build_launchers(dest, profile="full"):
    _section("Writing launchers")

    scripts_dir = os.path.join(dest, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)

    # The tools-only bundle pins the portable feature set so daemon-backed cards
    # (Winlink, APRS, …) stay hidden; the full bundle leaves OASIS_FEATURES unset.
    features_line = (
        f"set \"OASIS_FEATURES={PORTABLE_FEATURES}\"\r\n\r\n"
        if profile == "windows" else ""
    )

    # Windows — prefer the bundled embedded Python (shipped only with
    # --for-windows); otherwise fall back to any system Python on PATH so a
    # Pi-targeted bundle still runs on a Windows box that has Python installed.
    bat = os.path.join(scripts_dir, "start-server.bat")
    with open(bat, "w", newline="\r\n") as f:
        f.write(
            "@echo off\r\n"
            "title OASIS\r\n"
            "cd /d \"%~dp0..\"\r\n"
            "\r\n"
            + features_line +
            "REM Prefer the bundled embedded Python; else fall back to system Python.\r\n"
            "set \"OASIS_PY=\"\r\n"
            "if exist \"_runtime\\windows\\python.exe\" set \"OASIS_PY=_runtime\\windows\\python.exe\"\r\n"
            "if not defined OASIS_PY (\r\n"
            "    for %%C in (python.exe py.exe python3.exe) do (\r\n"
            "        if not defined OASIS_PY (\r\n"
            "            where %%C >nul 2>&1 && set \"OASIS_PY=%%C\"\r\n"
            "        )\r\n"
            "    )\r\n"
            ")\r\n"
            "\r\n"
            "if not defined OASIS_PY (\r\n"
            "    echo.\r\n"
            "    echo   ERROR: No Python found.\r\n"
            "    echo   This bundle has no embedded runtime ^(_runtime\\windows\\python.exe missing^)\r\n"
            "    echo   and no system Python is on your PATH.\r\n"
            "    echo.\r\n"
            "    echo   Do ONE of these:\r\n"
            "    echo     - Rebuild the bundle with the Windows runtime:\r\n"
            "    echo         python3 scripts\\create-oasis-offline.py --rebuild --for-windows\r\n"
            "    echo     - Or install Python 3.9+ from https://python.org\r\n"
            "    echo       ^(tick \"Add Python to PATH\" during setup^), then re-run this file.\r\n"
            "    echo.\r\n"
            "    pause\r\n"
            "    exit /b 1\r\n"
            ")\r\n"
            "\r\n"
            "echo Starting OASIS... (using %OASIS_PY%)\r\n"
            "\"%OASIS_PY%\" server\\app.py\r\n"
            "pause\r\n"
        )
    _ok("scripts/start-server.bat")

    # Linux / macOS — bootstraps a venv from the bundled wheels on first run.
    sh = os.path.join(scripts_dir, "start-server.sh")
    with open(sh, "w", newline="\n") as f:
        f.write(
            "#!/bin/bash\n"
            "set -e\n"
            'DIR="$(cd "$(dirname "$0")/.." && pwd)"\n'
            'VENV="$DIR/_runtime/linux/.venv"\n'
            'WHEELS="$DIR/server/wheels"\n'
            "cd \"$DIR\"\n"
            + (f'export OASIS_FEATURES="{PORTABLE_FEATURES}"\n' if profile == "windows" else "")
            # The requirements install below pulls the FULL base requirements, not
            # a hand-picked subset — a stale subset once starved the runtime venv
            # of skyfield/numpy/sgp4 (satellites), so /passes 500'd though the
            # wheels shipped. requirements.txt is the single source.
            + r'''
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install it with your package manager."
  exit 1
fi

# Rebuild the venv when its interpreter is missing (check bin/python, NOT just
# the directory). A failed first run — no python3 venv module, or a filesystem
# that can't hold symlinks — leaves a stub dir with no bin/python; the old
# launcher skipped it and then tried to exec the interpreter that never existed.
if [ ! -x "$VENV/bin/python" ]; then
  echo "First run - setting up Python environment from bundled wheels (offline) ..."
  rm -rf "$VENV"
  if ! venv_err="$(python3 -m venv "$VENV" 2>&1)"; then
    echo ""
    echo "ERROR: could not create the Python environment ($VENV)."
    if [ -n "$venv_err" ]; then printf '%s\n' "$venv_err" | sed 's/^/    /'; fi
    echo ""
    case "$venv_err" in
      *ensurepip*|*python3-venv*|*"No module named venv"*)
        echo "  Cause: python3 is installed but its venv module is not."
        echo "  Fix:   Debian / Ubuntu / Raspberry Pi OS ->  sudo apt install python3-venv"
        echo "         (Fedora and Arch already include it.)" ;;
      *"not permitted"*|*"Read-only file system"*)
        echo "  Cause: this filesystem cannot hold a virtualenv's symlinks"
        echo "         (FAT32 / exFAT / NTFS USB sticks cannot)."
        echo "  Fix:   copy the bundle onto the machine's disk and run it from there:"
        echo "           cp -r \"$DIR\" ~/oasis-offline && cd ~/oasis-offline && ./run-portable.sh" ;;
      *)
        echo "  Debian/Ubuntu:  sudo apt install python3-venv"
        echo "  On a FAT/exFAT/NTFS USB stick:  copy the bundle to disk (ext4) first." ;;
    esac
    rm -rf "$VENV"
    exit 1
  fi
  if ! "$VENV/bin/pip" install --quiet --no-index --find-links "$WHEELS" -r "$DIR/scripts/requirements.txt"; then
    echo ""
    echo "ERROR: installing the bundled dependencies failed."
    echo "  Your python3: $(python3 --version 2>&1)"
    echo "  Bundled wheels cover Python 3.9-3.14 - retry with a python3 in that range."
    rm -rf "$VENV"
    exit 1
  fi
  echo "Done."
fi

echo "Starting OASIS - open http://localhost:8083 in your browser"
# app.py prefers gunicorn when installed, else the Flask dev server
# (see server/app.py __main__). The launcher stays dumb on purpose.
exec "$VENV/bin/python" server/app.py
'''
        )
    st = os.stat(sh)
    os.chmod(sh, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    _ok("scripts/start-server.sh  (executable)")


# ── Bundle integrity manifest ─────────────────────────────────────────────────
MANIFEST_NAME = "bundle-manifest.json"


def write_bundle_manifest(dest):
    """Walk dest, SHA-256 every file, write bundle-manifest.json.

    Skips bundle-manifest.json itself.  Called automatically at the end of a
    build so every USB copy ships with a checksum file that --verify can use
    to confirm the copy is intact on the target machine.
    """
    _section("Writing bundle integrity manifest")
    manifest_path = os.path.join(dest, MANIFEST_NAME)
    files = {}
    file_list = []
    for root, _dirs, fnames in os.walk(dest):
        for fname in fnames:
            fpath = os.path.join(root, fname)
            rel   = os.path.relpath(fpath, dest).replace(os.sep, "/")
            if rel == MANIFEST_NAME:
                continue
            file_list.append((rel, fpath))
    file_list.sort()  # deterministic order

    tty = sys.stdout.isatty()
    total = len(file_list)
    for i, (rel, fpath) in enumerate(file_list, 1):
        if tty:
            print(f"  ↳  Hashing {i}/{total} …\r", end="", flush=True)
        sha = hashlib.sha256()
        with open(fpath, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                sha.update(chunk)
        files[rel] = sha.hexdigest()

    if tty:
        print(" " * 40 + "\r", end="", flush=True)   # clear the progress line

    manifest = {
        "oasis_bundle": True,
        "built_at":     datetime.datetime.now(datetime.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file_count":   len(files),
        "files":        files,
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    _ok(f"{MANIFEST_NAME}  ({len(files)} files hashed)")


def cmd_verify(target_dir):
    """Verify every file in an oasis-offline bundle against bundle-manifest.json.

    Exit 1 on any missing or corrupt file so the command can be used as a gate
    before a field deployment (e.g. after copying the bundle to a USB drive).
    """
    print()
    print("  OASIS — bundle integrity verify")
    _hr()
    _info(f"Bundle : {target_dir}")

    manifest_path = os.path.join(target_dir, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        _fail(
            f"{MANIFEST_NAME} not found in {target_dir}\n"
            "     Run create-oasis-offline.py (without --verify) to build a bundle first."
        )

    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    expected_files = manifest.get("files", {})
    built_at       = manifest.get("built_at", "unknown")
    _info(f"Built  : {built_at}")
    _info(f"Files  : {len(expected_files)} expected")
    _section("Verifying checksums")

    missing, corrupt = [], []
    ok_count  = 0
    total     = len(expected_files)
    tty       = sys.stdout.isatty()

    for i, (rel, expected_hex) in enumerate(sorted(expected_files.items()), 1):
        if tty:
            print(f"  ↳  Checking {i}/{total} …\r", end="", flush=True)
        fpath = os.path.join(target_dir, rel.replace("/", os.sep))
        if not os.path.isfile(fpath):
            missing.append(rel)
            continue
        sha = hashlib.sha256()
        with open(fpath, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                sha.update(chunk)
        if sha.hexdigest() == expected_hex:
            ok_count += 1
        else:
            corrupt.append(rel)

    if tty:
        print(" " * 40 + "\r", end="", flush=True)   # clear progress line

    _section("Results")
    _ok(f"{ok_count}/{total} files OK")
    for f in missing:
        _warn(f"MISSING  {f}")
    for f in corrupt:
        _warn(f"CORRUPT  {f}")

    print()
    if not missing and not corrupt:
        _ok("Bundle integrity verified — all checksums match.")
        print()
    else:
        print(
            f"  ERROR: {len(missing) + len(corrupt)} integrity failure(s).",
            file=sys.stderr,
        )
        print()
        sys.exit(1)


# ── Build: summary ─────────────────────────────────────────────────────────────
def build_summary(dest, profile="full"):
    total = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, files in os.walk(dest)
        for f in files
    )
    _section("Bundle complete")
    _ok(f"Location : {dest}")
    _ok(f"Size     : {total / 1_048_576:.0f} MB")
    print()
    _info(f"Next step: copy the contents of {os.path.basename(dest)}/ to your USB drive")
    _info("")
    _info("Windows : double-click scripts\\start-server.bat")
    _info("Linux   : chmod +x scripts/start-server.sh && ./scripts/start-server.sh  (first run bootstraps venv)")
    _info("Browser : http://localhost:8083")
    print()
    if profile == "windows":
        _info("Tools-only bundle: FCC lookup, ICS forms, RepeaterBook, net logger,")
        _info("radio references, handbook and calculators — all offline.")
        _info("Winlink, APRS and the Pi-only cards are hidden (OASIS_FEATURES pinned).")
    else:
        _info("Maps, forms, FCC lookup, calculators and reference all work offline.")
        _warn("GrayWolf, Kiwix and the APRS API show DOWN — those are Pi-only services.")
        _warn("Install them on the Pi from offline-packages/ using the install scripts.")
    print()


# ── Commands ───────────────────────────────────────────────────────────────────
def cmd_check():
    """Refresh each feature's `version` in the manifest from upstream latest.

    No downloads, no file inspection. Updates scripts/offline-manifest.json in place:
    a feature with no version or a stale one is rewritten to the latest available
    (UPDATED); a matching one is left alone (CURRENT); one we can't reach upstream is
    left as-is and flagged for --update. The actual files are fetched later by a
    build / --update, which read these pinned versions.
    """
    print("\n  OASIS — create-oasis-offline  [--check]")
    _hr()
    _info(f"Manifest: {os.path.relpath(_MANIFEST_PATH)}")
    _section("Version check — manifest vs upstream latest")

    rows = check_versions()
    for feat, old, new, status in rows:
        if status == "updated":
            print(f"  ⬆  {feat:<10} {str(old or '(none)'):<12} -> {new:<10} UPDATED")
        elif status == "current":
            print(f"  ✓  {feat:<10} {str(new):<12}    CURRENT")
        else:  # uncheckable
            print(f"  ⋯  {feat:<10} could not reach upstream — version corrected on --update")

    updated = [f for f, *_rest, s in rows if s == "updated"]
    _hr()
    if updated:
        _ok(f"Manifest versions updated: {', '.join(updated)}.")
        _info("Run --update (or a build) to fetch the new versions.")
    else:
        _ok("All manifest versions current — nothing to update.")
    print()


def cmd_build(skip_windows, rebuild=False, all_platforms=False, profile="full"):
    """Download the offline assets into the profile's output dir, then build it.

    profile="full"    → the complete Pi bundle in oasis-offline/ (all phases).
    profile="windows" → a tools-only bundle in oasis-offline-windows/: no Pi
                        hardware / displays / APRS / Winlink / ZIM / maps, just
                        Flask + the standalone tools + FCC lookup, with the
                        Windows embedded Python always shipped.
    """
    windows = (profile == "windows")
    out_dir = OUT_DIR_WINDOWS if windows else OUT_DIR
    # The windows bundle is self-contained: always ship the embedded runtime.
    skip_windows = skip_windows and not windows

    print("\n  OASIS — create-oasis-offline")
    _hr()
    _info(f"Profile: {profile}")
    _info(f"Output : {out_dir}")
    _info(f"Mode   : {'clean rebuild (wiping existing)' if rebuild else 'incremental (reusing existing assets)'}")

    if rebuild and os.path.exists(out_dir):
        _info(f"Removing existing {os.path.basename(out_dir)}/ ...")
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # Phase 0: copy repo source files first (download-managed dirs are excluded by
    # bundle-ignore — the download phases below populate those in-place).
    build_copy(out_dir, include_all_platforms=all_platforms,
               ignore=load_ignore(profile), warn_missing_pi=not windows)

    # Download phases — each writes directly into its subdirectory.
    # update=True means "skip files already at current version" (always on by default).
    pkg_root = os.path.join(out_dir, "offline-packages")

    # Python wheels: full matrix for the Pi bundle; win_amd64 only for the tools bundle.
    win_targets = [t for t in TARGETS if t[0] == "win_amd64"]
    phase_wheels(os.path.join(out_dir, "server", "wheels"),
                 targets=win_targets if windows else None)

    # FCC lookup is a standalone tool — ships in both profiles.
    fcc_dir = os.path.join(out_dir, "services", "fcc_database", "data")
    phase_fcc(fcc_dir)

    # Pi-only assets: APRS/Winlink/RTL-SDR/display daemons, Wikipedia, maps.
    # None of these belong in the tools bundle.
    if not windows:
        phase_aprs_sprites(os.path.join(out_dir, "server", "map-assets"))
        phase_graywolf(pkg_root, update=True)
        phase_kiwix(pkg_root, update=True)
        phase_rtl_sdr(pkg_root, update=True)
        phase_webssh(pkg_root, update=True)
        phase_pat(pkg_root, update=True)
        phase_direwolf(pkg_root, update=True)
        phase_satellites_voice(pkg_root, update=True)
        phase_satellites_roster(out_dir)
        phase_adsb(pkg_root, update=True)
        phase_cm4stack(pkg_root, update=True)
        phase_wikipedia(os.path.join(out_dir, "zim"))
        phase_pmtiles(os.path.join(out_dir, "maps"), all_platforms=all_platforms)

    build_windows_runtime(out_dir, skip_windows)
    build_launchers(out_dir, profile=profile)
    write_bundle_manifest(out_dir)
    build_summary(out_dir, profile=profile)


# ── Update command ────────────────────────────────────────────────────────────
def cmd_update(target_dir, all_platforms=False):
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
        build_copy(target_dir, include_all_platforms=all_platforms)
    elif parent_has_script:
        build_copy(target_dir, src=parent_repo, include_all_platforms=all_platforms)

    pkg_root = os.path.join(target_dir, "offline-packages")
    phase_aprs_sprites(os.path.join(target_dir, "server", "map-assets"))
    phase_wheels(os.path.join(target_dir, "server", "wheels"))
    phase_graywolf(pkg_root, update=True)
    phase_kiwix(pkg_root, update=True)
    phase_fcc(os.path.join(target_dir, "services", "fcc_database", "data"))
    phase_rtl_sdr(pkg_root, update=True)
    phase_webssh(pkg_root, update=True)
    phase_pat(pkg_root, update=True)
    phase_direwolf(pkg_root, update=True)
    phase_satellites_voice(pkg_root, update=True)
    phase_satellites_roster(target_dir)
    phase_adsb(pkg_root, update=True)
    phase_cm4stack(pkg_root, update=True)
    phase_wikipedia(os.path.join(target_dir, "zim"))
    phase_pmtiles(os.path.join(target_dir, "maps"), all_platforms=all_platforms)

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
            "  python3 scripts/create-oasis-offline.py --for-windows         # include Windows Python\n"
            "  python3 scripts/create-oasis-offline.py --profile windows --rebuild  # tools-only Windows bundle → oasis-offline-windows/\n"
            "  python3 scripts/create-oasis-offline.py --all-platforms       # also vendor macOS/Windows pmtiles binaries\n"
            "  python3 scripts/create-oasis-offline.py --update              # update packages + sync source files"
            " into oasis-offline/\n"
            "  python3 scripts/create-oasis-offline.py --update --dir /mnt/usb  # update USB bundle\n"
            "  python3 scripts/create-oasis-offline.py --verify              # verify oasis-offline/ checksums\n"
            "  python3 scripts/create-oasis-offline.py --verify --dir /mnt/usb  # verify USB copy integrity\n"
        ),
    )
    ap.add_argument(
        "--check", action="store_true",
        help="Verify offline assets in oasis-offline/ and report findings. No changes (CI mode).",
    )
    ap.add_argument(
        "--for-windows", action="store_true",
        help="Also include the Windows embedded-Python runtime. Required for scripts/start-server.bat to work.",
    )
    ap.add_argument(
        "--all-platforms", action="store_true",
        help=(
            "Also vendor the macOS + Windows pmtiles converter binaries (~166 MB). "
            "By default only the Linux binaries ship, since the bundle targets the Pi."
        ),
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
            "Targets oasis-offline/ inside the repo by default; use --dir to specify a different path."
        ),
    )
    ap.add_argument(
        "--verify", action="store_true",
        help=(
            "Verify every file in an existing bundle against its bundle-manifest.json. "
            "Exits 1 if any file is missing or corrupt. "
            "Defaults to oasis-offline/ inside the repo; use --dir to point at a USB copy."
        ),
    )
    ap.add_argument(
        "--profile", choices=("full", "windows"), default="full",
        help=(
            "Bundle profile. 'full' (default) builds the complete Pi bundle in "
            "oasis-offline/. 'windows' builds a tools-only bundle in "
            "oasis-offline-windows/ (Flask + standalone tools + FCC lookup + "
            "embedded Windows Python; no Pi hardware, displays, APRS/Winlink, ZIM "
            "or maps)."
        ),
    )
    ap.add_argument(
        "--dir", metavar="DIR",
        help="Directory to target. Valid with --update or --verify. Defaults to oasis-offline/ inside the repo.",
    )
    args = ap.parse_args()

    if args.dir and not args.update and not args.verify:
        ap.error("--dir is only valid with --update or --verify")
    if args.profile != "full" and (args.update or args.verify or args.check):
        ap.error("--profile only applies to a build (not --update/--verify/--check)")

    if args.update:
        if args.dir:
            target = os.path.abspath(args.dir)
        elif os.path.isdir(OUT_DIR):
            target = OUT_DIR          # running from main repo → update oasis-offline/
        else:
            target = REPO_ROOT        # running from inside the bundle → update bundle root
        cmd_update(target, all_platforms=args.all_platforms)
    elif args.verify:
        if args.dir:
            target = os.path.abspath(args.dir)
        elif os.path.isdir(OUT_DIR):
            target = OUT_DIR
        else:
            target = REPO_ROOT
        cmd_verify(target)
    elif args.check:
        cmd_check()
    else:
        cmd_build(not args.for_windows, rebuild=args.rebuild,
                  all_platforms=args.all_platforms, profile=args.profile)


if __name__ == "__main__":
    main()
