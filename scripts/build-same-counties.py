#!/usr/bin/env python3
"""Build services/nwr/data/same-counties.json from the Census Gazetteer.

BUILD TIME ONLY. OASIS never fetches this at runtime — the generated JSON is
committed. Re-run only when the Census publishes a new vintage.

Why the Gazetteer's INTPTLAT/INTPTLONG and not a computed centroid: the
"internal point" is guaranteed by the Census to lie INSIDE the county polygon.
A true centroid falls outside for crescent- and horseshoe-shaped counties,
which would plant a tornado warning in the neighbouring county or in open water.

Keys are the 5-digit GEOID (SSCCC), matching both dsame3's US_SAME_CODE and the
SAME PSSCCC code with its leading subdivision digit stripped.

Usage:
  python3 scripts/build-same-counties.py                 # download the default vintage
  python3 scripts/build-same-counties.py --file <path>   # use an already-downloaded file
"""
import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile

GAZETTEER_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
                 "2023_Gazetteer/2023_Gaz_counties_national.zip")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "services", "nwr", "data", "same-counties.json")


def _rows(text):
    """Yield dicts from the Gazetteer's tab-separated file (header row first)."""
    lines = text.splitlines()
    header = [h.strip() for h in lines[0].split("\t")]
    for line in lines[1:]:
        if not line.strip():
            continue
        yield dict(zip(header, [c.strip() for c in line.split("\t")]))


def build(text):
    out = {}
    for r in _rows(text):
        geoid = r.get("GEOID")
        try:
            lat = round(float(r["INTPTLAT"]), 4)
            lon = round(float(r["INTPTLONG"]), 4)
        except (KeyError, ValueError):
            continue
        if not geoid or len(geoid) != 5:
            continue
        name = r.get("NAME", "")
        for suffix in (" County", " Parish", " Borough", " Census Area",
                       " Municipality", " city", " City and Borough"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        out[geoid] = {"n": name, "s": r.get("USPS", ""), "lat": lat, "lon": lon}
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", help="local Gazetteer .zip or .txt instead of downloading")
    args = p.parse_args()

    if args.file:
        raw = open(args.file, "rb").read()
    else:
        print(f"downloading {GAZETTEER_URL}")
        with urllib.request.urlopen(GAZETTEER_URL, timeout=60) as r:
            raw = r.read()

    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = [n for n in z.namelist() if n.endswith(".txt")][0]
            text = z.read(name).decode("latin-1")
    else:
        text = raw.decode("latin-1")

    table = build(text)
    if len(table) < 3000:
        print(f"ERROR: only {len(table)} counties parsed — expected ~3,144", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(table, fh, separators=(",", ":"), sort_keys=True)
        fh.write("\n")
    print(f"wrote {OUT} — {len(table)} counties, {os.path.getsize(OUT) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
