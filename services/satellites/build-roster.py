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
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


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
    except Exception as e:  # noqa: BLE001 — any network/parse failure is a no-op
        print(f"build-roster: fetch failed ({e}); leaving existing data untouched",
              file=sys.stderr)
        return 1

    os.makedirs(args.cache, exist_ok=True)
    for group, text in tle_texts.items():
        if "1 " in text:
            _atomic_write(os.path.join(args.cache, f"{group}.txt"), text)

    tle_index = tle.index_by_norad(tle.load_cache(args.cache))
    sats = satnogs.parse_satellites(sats_raw)
    txs = satnogs.parse_transmitters(txs_raw)

    prev = roster.load(args.config)
    prev_selected = {s["norad"]: s.get("selected", False)
                     for s in prev.get("satellites", [])}
    records, facet = satnogs.build_records(sats, txs, tle_index, prev_selected)
    diff = satnogs.diff_rosters(prev.get("satellites", []), records)

    data = {"updated": roster._now(), "source": "satnogs+celestrak",
            "labels": facet, "satellites": records}
    os.makedirs(os.path.dirname(args.config), exist_ok=True)
    _atomic_write(args.config, json.dumps(data, indent=2))

    print(json.dumps({"count": len(records), "labels": facet, "changes": diff}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
