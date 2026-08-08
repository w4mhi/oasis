#!/usr/bin/env python3
"""Aggregate SatNOGS (identity + transmitters + up/down freqs) and CelesTrak
(TLE) into the OASIS satellite list. ONLINE ONLY — replaces sync-tle.py. One
pass writes configuration/satellites.json (records) and configuration/tle-cache/
(raw TLE). Runtime never calls this; it only reads what this produces.

Usage:  python3 services/satellites/build-roster.py [--cache DIR] [--config FILE]
"""
import argparse
import json
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import satnogs  # noqa: E402
import tle  # noqa: E402
import roster  # noqa: E402
from common import config_paths  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8")


def _atomic_write(path, text):
    tmp = f"{path}.{os.getpid()}.tmp"   # pid-unique so concurrent runs don't clash
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _match_dir_owner(path):
    """When this runs as root (the privileged installer worker), leave `path`
    owned by whoever owns its parent configuration/ dir — the web-server user —
    so the server can REWRITE it later. satellites.json in particular is updated
    by /api/satellites/select; a root-owned file makes that endpoint 500 (EACCES)
    and selections silently never persist. No-op off-root or on any error."""
    try:
        if os.geteuid() != 0:
            return
    except AttributeError:
        return   # os.geteuid is Unix-only
    try:
        st = os.stat(os.path.dirname(path))
        os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass


def _gapfill_blocks():
    """CATNR-fetch the group-less APT weather birds (tle.GAPFILL_NORADS). A
    single bird failing is non-fatal (skip it) — unlike a whole-group failure."""
    blocks = []
    for norad in tle.GAPFILL_NORADS:
        try:
            t = _get(tle.CATNR_URL.format(norad))
        except Exception:  # noqa: BLE001 — one missing bird must not abort the build
            continue
        if t.lstrip().startswith("Invalid") or "1 " not in t:
            continue
        blocks.append(t.strip())
    return blocks


def _prune_stale_cache(cache_dir):
    """Remove cache *.txt files this build no longer produces (e.g. a leftover
    group from an older version) so load_cache() can't merge stale orbits."""
    known = {f"{g}.txt" for g in tle.GROUPS} | {"extra.txt"}
    for fn in os.listdir(cache_dir):
        if fn.endswith(".txt") and fn not in known:
            os.remove(os.path.join(cache_dir, fn))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default=config_paths.tle_cache_dir(REPO_ROOT),
                    help="TLE cache dir (default: configuration/tle-cache)")
    ap.add_argument("--config", default=config_paths.satellites_json(REPO_ROOT),
                    help="satellites.json path (default: configuration/satellites.json)")
    args = ap.parse_args()

    # Fetch EVERYTHING first; write nothing until all succeed, so a mid-fetch
    # failure (offline / host unreachable) leaves the previous data intact.
    try:
        sats_raw = json.loads(_get(satnogs.SAT_API))
        txs_raw = json.loads(_get(satnogs.TX_API))
        tle_texts = {g: _get(u) for g, u in tle.GROUPS.items()}
        extra_blocks = _gapfill_blocks()
    except Exception as e:  # noqa: BLE001 — any network/parse failure is a no-op
        print(f"build-roster: fetch failed ({e}); leaving existing data untouched",
              file=sys.stderr)
        return 1

    os.makedirs(args.cache, exist_ok=True)
    for group, text in tle_texts.items():
        if "1 " in text:
            _atomic_write(os.path.join(args.cache, f"{group}.txt"), text)
    if extra_blocks:
        _atomic_write(os.path.join(args.cache, "extra.txt"),
                      "\n".join(extra_blocks) + "\n")
    _prune_stale_cache(args.cache)

    tle_index = tle.index_by_norad(tle.load_cache(args.cache))
    sats = satnogs.parse_satellites(sats_raw)
    txs = satnogs.parse_transmitters(txs_raw)

    prev = roster.load(args.config)
    # Everything the OPERATOR owns (monitored + pass-alert bell), carried across
    # the rebuild as a set. build_records() writes each record from a fixed key
    # set, so a flag that isn't in here is gone the moment this script runs.
    records, facet = satnogs.build_records(sats, txs, tle_index,
                                           roster.operator_state(prev))
    diff = satnogs.diff_rosters(prev.get("satellites", []), records)

    data = {"updated": roster._now(), "source": "satnogs+celestrak",
            "labels": facet, "satellites": records}
    os.makedirs(os.path.dirname(args.config), exist_ok=True)
    _atomic_write(args.config, json.dumps(data, indent=2))
    _match_dir_owner(args.config)   # server must rewrite this on /select — not root-owned

    print(json.dumps({"count": len(records), "labels": facet, "changes": diff}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
