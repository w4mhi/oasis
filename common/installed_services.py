"""Single owner of installed-services.json's schema.

The manifest records which features are installed (`features`, a sorted list the
dashboard and Setup page read) and, per feature, a `removal` record describing how
to tear it down (see common/removal.py for the record schema). Removal data is
written by the installer at install time and read back by remove-oasis.py / the
installer worker — one source of truth, never a separate hand-maintained table.

All writes go through write(), which is atomic (write-temp + os.replace) so a
crash mid-write can never leave a half-written manifest on the Pi.
"""
import json
import os
import time

from common import config_paths


def _path(repo_root):
    return config_paths.installed_services_json(repo_root)


def read(repo_root):
    """Return the full manifest dict, or {} if absent/unreadable/not a dict."""
    try:
        with open(_path(repo_root), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def installed_features(repo_root):
    """Return the set of installed feature keys."""
    feats = read(repo_root).get("features")
    return {str(k) for k in feats} if isinstance(feats, list) else set()


def removal_map(repo_root):
    """Return the per-feature removal record map ({} if none)."""
    rm = read(repo_root).get("removal")
    return dict(rm) if isinstance(rm, dict) else {}


def write(repo_root, features, removal):
    """Atomically write the manifest: sorted `features`, the `removal` map, and a
    fresh `updated` timestamp."""
    payload = {
        "features": sorted(features),
        "removal": removal or {},
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    os.makedirs(config_paths.config_dir(repo_root), exist_ok=True)
    dst = _path(repo_root)
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, dst)  # atomic on the same filesystem


def add_installed(repo_root, keys, records):
    """Union `keys` into `features` and merge `records` into the removal map.

    Additive by design: installed features are never dropped here as a side
    effect. Overwrites a feature's removal record if `records` supplies a new one.
    No-op (no write) when nothing changes."""
    feats = installed_features(repo_root)
    rmap = removal_map(repo_root)
    new_feats = feats | set(keys)
    new_rmap = dict(rmap)
    new_rmap.update(records or {})
    if new_feats == feats and new_rmap == rmap:
        return
    write(repo_root, new_feats, new_rmap)


def remove_installed(repo_root, keys):
    """Drop `keys` from both `features` and the removal map. No-op when nothing
    changes."""
    drop = set(keys)
    feats = installed_features(repo_root)
    rmap = removal_map(repo_root)
    new_feats = feats - drop
    new_rmap = {k: v for k, v in rmap.items() if k not in drop}
    if new_feats == feats and new_rmap == rmap:
        return
    write(repo_root, new_feats, new_rmap)
