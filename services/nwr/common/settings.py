"""configuration/nwr.json — the operator's channel, gain and watch list.

A feature-scoped file rather than more keys in station.json, so uninstalling NWR
removes its configuration cleanly. Same reasoning as satellites.json.
"""
import re

from common import atomic_json, config_paths
from services.nwr.common import listener

DEFAULTS = {
    "channel_hz": 162550000,      # WX7, the most commonly assigned channel
    "gain": "auto",
    "ppm": 0,
    "watch_fips": [],             # empty means "act on everything" — see below
    "bell": False,                # opt-in: a watch that talks unprompted gets turned off
    "bell_override_until": 0,     # epoch; expires on its own at the next 07:00
    "pinned_channel": None,       # None = auto-scan picks the strongest
}

_FIPS_RE = re.compile(r"^\d{5,6}$")
_VALID_HZ = {hz for _, hz in listener.CHANNELS}


def _norm_fips(value):
    """5- or 6-digit accepted, 5-digit stored. The 6-digit PSSCCC form carries a
    leading county-subdivision digit that the county tables do not use."""
    s = str(value).strip()
    if not _FIPS_RE.match(s):
        raise ValueError(f"not a FIPS code: {value!r}")
    return s[1:] if len(s) == 6 else s


def load(repo_root):
    """Settings with defaults filled in. Unknown keys on disk are dropped."""
    data = atomic_json.read_json(config_paths.nwr_json(repo_root), default={})
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        for k in DEFAULTS:
            if k in data:
                out[k] = data[k]
        # v1 -> v2 migration: a stored `speak: true` becomes `bell: true`, so an
        # operator who had v1 speech on does not silently lose it now that
        # `speak` has dropped out of DEFAULTS. Idempotent by construction: it
        # only fires while the file has no explicit `bell` of its own, and the
        # first save() rewrites the whole file from this dict — which then DOES
        # carry an explicit `bell` — so the migration never re-fires after that.
        if "bell" not in data and data.get("speak"):
            out["bell"] = True
    out["watch_fips"] = list(out.get("watch_fips") or [])
    return out


def save(repo_root, patch):
    """Merge `patch` over the stored settings after validating it.

    Raises ValueError on bad input — the route turns that into a 400 rather than
    persisting something the listener would choke on later.
    """
    if not isinstance(patch, dict):
        raise ValueError("expected an object")
    current = load(repo_root)

    if "channel_hz" in patch:
        try:
            hz = int(patch["channel_hz"])
        except (TypeError, ValueError):
            raise ValueError("channel_hz must be an integer")
        if hz not in _VALID_HZ:
            raise ValueError("channel_hz is not one of the seven NWR channels")
        current["channel_hz"] = hz

    if "watch_fips" in patch:
        raw = patch["watch_fips"]
        if not isinstance(raw, list):
            raise ValueError("watch_fips must be a list")
        current["watch_fips"] = sorted({_norm_fips(v) for v in raw})

    if "gain" in patch:
        current["gain"] = str(patch["gain"])[:16]

    if "ppm" in patch:
        try:
            current["ppm"] = int(patch["ppm"])
        except (TypeError, ValueError):
            raise ValueError("ppm must be an integer")

    if "bell" in patch:
        current["bell"] = bool(patch["bell"])

    if "bell_override_until" in patch:
        try:
            current["bell_override_until"] = int(patch["bell_override_until"])
        except (TypeError, ValueError):
            raise ValueError("bell_override_until must be an integer")

    if "pinned_channel" in patch:
        val = patch["pinned_channel"]
        if val is not None:
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise ValueError("pinned_channel must be an integer or null")
            if val not in _VALID_HZ:
                raise ValueError("pinned_channel is not one of the seven NWR channels")
        current["pinned_channel"] = val

    atomic_json.write_json(config_paths.nwr_json(repo_root), current)
    return current
