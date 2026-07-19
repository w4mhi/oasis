#!/usr/bin/env python3
"""Download CelesTrak TLE groups into the OASIS TLE cache. ONLINE ONLY — run by
hand or a cron when the Pi has internet (every ~3 days). Runtime code never
calls this; it only reads the cache tle.load_cache() populates.

Usage:  python3 services/satellites/sync-tle.py [--cache DIR]
"""
import argparse
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import roster  # noqa: E402  (services/satellites/roster.py)
import tle  # noqa: E402
from common import config_paths  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
CATNR_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={}&FORMAT=tle"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=config_paths.tle_cache_dir(REPO_ROOT))
    args = ap.parse_args()
    os.makedirs(args.cache, exist_ok=True)
    for group, url in tle.GROUPS.items():
        print(f"⏳ {group} …", end=" ", flush=True)
        with urllib.request.urlopen(url, timeout=30) as r:
            text = r.read().decode("utf-8")
        if "1 " not in text:
            print("SKIP (no TLE data returned)")
            continue
        with open(os.path.join(args.cache, f"{group}.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"OK ({len(tle.parse_tle_text(text))} sats)")

    # Gap-fill: some roster satellites aren't in any group (e.g. NOAA POES
    # 15/18/19 are not in weather/amateur/stations). Fetch each missing roster
    # NORAD individually via a CATNR query and write them to extra.txt.
    present = set(tle.index_by_norad(tle.load_cache(args.cache)))
    wanted = {s["norad"] for s in roster.load(config_paths.satellites_json(REPO_ROOT))["satellites"]}
    missing = sorted(wanted - present)
    if missing:
        print(f"⏳ gap-fill {len(missing)} roster sat(s) not in the groups …", end=" ", flush=True)
        blocks = []
        for norad in missing:
            try:
                with urllib.request.urlopen(CATNR_URL.format(norad), timeout=30) as r:
                    t = r.read().decode("utf-8")
            except Exception:
                continue
            if t.lstrip().startswith("Invalid") or "1 " not in t:
                continue
            blocks.append(t.strip())
        if blocks:
            with open(os.path.join(args.cache, "extra.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(blocks) + "\n")
        print(f"OK ({len(blocks)} fetched)")
    print("Done. Cache:", args.cache)


if __name__ == "__main__":
    main()
