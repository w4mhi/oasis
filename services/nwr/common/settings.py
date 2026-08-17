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
    "speak": True,
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

    if "speak" in patch:
        current["speak"] = bool(patch["speak"])

    atomic_json.write_json(config_paths.nwr_json(repo_root), current)
    return current
