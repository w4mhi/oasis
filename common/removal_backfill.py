"""Regenerate per-feature removal records from the installers on demand.

installed-services.json is the single source of truth for removal, but boxes
installed before the `removal` map existed (or via the CLI) have only a flat
feature list. Because each feature's removal_record() is derivable from its
installer's constants, we can rebuild any missing record without a reinstall —
the manifest self-heals. record_for() builds one; ensure() fills every gap for a
set of installed features and persists the result.
"""
from common import installed_services
from common import setup_registry as SR

# Features intentionally without a removal record (see the design's carve-outs):
# server serves the Setup page; wikipedia's ZIM lifecycle is a separate concern.
EXCLUDED = {"server", "wikipedia"}


def record_for(repo_root, key):
    """Return the removal record for one installed feature, or None if the feature
    is excluded, unknown, or its record cannot be generated."""
    if key in EXCLUDED:
        return None
    spec = SR.build_registry(repo_root, payload={}).get(key)
    if spec is None or spec.removal_record_fn is None:
        return None
    try:
        return spec.removal_record_fn()
    except Exception:
        # A feature module that can't be imported/evaluated here must not break
        # backfill for the rest — skip it (removal falls back to an empty record).
        return None


def ensure(repo_root, keys):
    """For each key lacking a stored removal record, regenerate and persist it.
    Returns the full record map now available for `keys`."""
    have = installed_services.removal_map(repo_root)
    new = {}
    for key in keys:
        if key in have:
            continue
        rec = record_for(repo_root, key)
        if rec is not None:
            new[key] = rec
    if new:
        installed_services.add_installed(repo_root, set(), new)
    out = {k: have[k] for k in keys if k in have}
    out.update(new)
    return out
