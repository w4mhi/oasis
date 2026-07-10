#!/usr/bin/env python3
"""
manifest.py — reader for the OASIS offline package manifest.

The manifest (scripts/offline-manifest.json) is the single source of truth for
every external package / driver / library OASIS installs. This module loads it
and exposes a small query API shared by its two consumers:

  • the BUILDER  (scripts/create-oasis-offline.py) — vendors packages into the
    offline bundle, per suite + per arch, and writes resolved versions back.
  • the INSTALLER (setup-oasis.py + common/<feature>.py)
    — on the Pi, resolves the best source (newest of apt vs bundle vs installed),
    enforces `min_version` capability gates, then installs.

Pure library: importing it has NO side effects and works on macOS / Windows /
Linux. It does not require `dpkg` to import — version comparison falls back to a
pure-Python comparator when dpkg is unavailable.
"""

import json
import os
import re
import shutil

# ── Reuse the project's dpkg comparator when importable; degrade gracefully so
#    this module still imports (and compares versions) on a build host without
#    dpkg (macOS / Windows). ───────────────────────────────────────────────────
try:                                            # imported as `common.manifest`
    from .oasis_lib import dpkg_cmp as _dpkg_cmp
except Exception:                               # imported with common/ on sys.path
    try:
        from oasis_lib import dpkg_cmp as _dpkg_cmp
    except Exception:
        _dpkg_cmp = None

_HAS_DPKG = shutil.which("dpkg") is not None and _dpkg_cmp is not None

# ── Paths ───────────────────────────────────────────────────────────────────────
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT   = os.path.dirname(_THIS_DIR)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
DEFAULT_MANIFEST = os.path.join(SCRIPTS_DIR, "offline-manifest.json")

_cache = {}


# ── Load / basic accessors ──────────────────────────────────────────────────────
def load_manifest(path=None):
    """Load and cache the manifest JSON. Returns the parsed dict."""
    path = path or DEFAULT_MANIFEST
    key = os.path.abspath(path)
    if key not in _cache:
        with open(path, encoding="utf-8") as f:
            _cache[key] = json.load(f)
    return _cache[key]


def _m(m):
    return m if m is not None else load_manifest()


def suites(m=None):
    return list(_m(m).get("suites", []))


def arches(m=None):
    return list(_m(m).get("arches", []))


def features(m=None):
    return list(_m(m).get("features", {}).keys())


def get_feature(feature, m=None):
    feats = _m(m).get("features", {})
    if feature not in feats:
        raise KeyError(f"unknown feature {feature!r} (have: {', '.join(feats)})")
    return feats[feature]


def source_type(feature, m=None):
    """'apt' | 'github-release' | 'pypi' | 'url' | 'data'."""
    return get_feature(feature, m).get("type")


def is_best_effort(feature, m=None):
    return bool(get_feature(feature, m).get("best_effort"))


def feature_suites(feature, m=None):
    f = get_feature(feature, m)
    return list(f.get("suites", _m(m).get("suites", [])))


def feature_arches(feature, m=None):
    f = get_feature(feature, m)
    return list(f.get("arches", _m(m).get("arches", [])))


# ── Package lists by source type ────────────────────────────────────────────────
def apt_packages(feature, suite=None, m=None):
    """Apt package names for an `apt`-type feature.

    Includes the suite-specific set when *suite* is given (e.g. `librtlsdr0` on
    bookworm vs `librtlsdr2` on trixie). With suite=None, returns common + the
    union of every suite-specific name.
    """
    f = get_feature(feature, m)
    if f.get("type") != "apt":
        raise ValueError(f"{feature} is type {f.get('type')!r}, not 'apt'")
    pkgs = f.get("packages", {})
    out = list(pkgs.get("common", []))
    by_suite = pkgs.get("by_suite", {})
    if suite is not None:
        out += [p for p in by_suite.get(suite, []) if p not in out]
    else:
        for names in by_suite.values():
            out += [p for p in names if p not in out]
    return out


def pypi_packages(feature, m=None):
    """List of {'name', 'version', ...} dicts for a `pypi`-type feature."""
    f = get_feature(feature, m)
    if f.get("type") != "pypi":
        raise ValueError(f"{feature} is type {f.get('type')!r}, not 'pypi'")
    return list(f.get("packages", []))


# ── Bundle layout (shared by builder + installer) ───────────────────────────────
def bundle_group(feature, m=None):
    """The offline-bundle directory group a feature's packages live in.

    Several manifest features can share one group: rtl-sdr, rtl-sdr-feed and
    rtl-sdr-diag all land in 'rtl-sdr'. Defaults to the feature's own name.
    """
    return get_feature(feature, m).get("bundle_group", feature)


def bundle_dir(root, feature, suite=None, m=None):
    """Path to a feature's vendored packages inside bundle *root*.

    Suite-scoped for apt features: <root>/<group>/<suite> (so a bookworm and a
    trixie set never collide). Pass suite=None for non-apt features
    (github-release / url / pypi), giving <root>/<group>. Both the BUILDER (writes
    here) and the INSTALLER (reads here) must call this — it is the one place the
    bundle layout is defined.
    """
    parts = [root, bundle_group(feature, m)]
    if suite is not None:
        parts.append(suite)
    return os.path.join(*parts)


# ── Capability gates (min_version) ──────────────────────────────────────────────
def min_version(feature, m=None):
    """The feature's min_version gate dict, or None.

    Shape: {pkg: {'min': '2.0', 'reason': '...'}} (reason optional; a bare
    version string is also accepted).
    """
    return get_feature(feature, m).get("min_version")


def check_min_version(feature, installed_versions, m=None):
    """Evaluate capability gates against what's installed/obtainable.

    *installed_versions* maps package name -> version string (or None). Returns a
    list of unmet gates: [{'package','required','installed','reason'}]. An empty
    list means every gate is satisfied (or there are none).
    """
    gates = min_version(feature, m) or {}
    unmet = []
    for pkg, spec in gates.items():
        req = spec.get("min") if isinstance(spec, dict) else spec
        inst = (installed_versions or {}).get(pkg)
        if not meets_min_version(inst, req):
            unmet.append({
                "package":  pkg,
                "required": req,
                "installed": inst,
                "reason":   spec.get("reason") if isinstance(spec, dict) else None,
            })
    return unmet


# ── Version comparison ──────────────────────────────────────────────────────────
def _py_ver_key(v):
    """Loose, dpkg-free version key: split into numeric/alpha chunks.

    Good enough for picking the newest among simple Debian/semver strings when
    dpkg isn't available (build hosts). Real targets use dpkg (see vcmp).
    """
    key = []
    for part in re.split(r"[.\-_+~:]", str(v).strip()):
        m = re.match(r"(\d*)(.*)", part)
        num = int(m.group(1)) if m.group(1) else 0
        key.append((1 if m.group(1) else 0, num, m.group(2)))
    return key


def vcmp(a, b):
    """Compare versions: return -1, 0, or 1 for a<b, a==b, a>b.

    Uses `dpkg --compare-versions` when available (authoritative Debian ordering),
    else a pure-Python fallback so the builder can still pick newest on macOS.
    """
    if a == b:
        return 0
    if _HAS_DPKG:
        try:
            if _dpkg_cmp(a, "gt", b):
                return 1
            if _dpkg_cmp(a, "lt", b):
                return -1
            return 0
        except Exception:
            pass
    ka, kb = _py_ver_key(a), _py_ver_key(b)
    return (ka > kb) - (ka < kb)


def version_ge(a, b):
    return vcmp(a, b) >= 0


def meets_min_version(installed, required):
    """True if *installed* (>=) satisfies *required*. Either may be None/empty."""
    if not required:
        return True
    if not installed:
        return False
    return version_ge(installed, required)


# ── Source resolution: newest-source-wins ───────────────────────────────────────
def resolve_source(installed, apt_candidate, bundled):
    """Decide where to install a package from, newest version wins.

    Args are version strings or None (None = unavailable / unknown):
      installed     - version already on the system (None if absent)
      apt_candidate - version apt would install (None if offline/unknown)
      bundled       - version in the offline bundle (None if not bundled)

    Returns:
      'apt'    - install/upgrade from apt
      'bundle' - install/upgrade from the offline bundle
      'skip'   - installed is already >= every obtainable source
      None     - nothing obtainable and nothing installed

    Ties prefer apt (it matches the host suite) over the bundle. This is the rule
    that stops a stale single-suite bundle (e.g. bookworm librtlsdr 0.6.0) from
    installing over a newer apt version on a different suite (trixie 2.0.2).
    """
    sources = []
    if apt_candidate:
        sources.append(("apt", apt_candidate))
    if bundled:
        sources.append(("bundle", bundled))
    if not sources:
        return "skip" if installed else None

    best_src, best_ver = sources[0]          # apt first → ties prefer apt
    for src, ver in sources[1:]:
        if vcmp(ver, best_ver) > 0:
            best_src, best_ver = src, ver

    if installed and version_ge(installed, best_ver):
        return "skip"
    return best_src
