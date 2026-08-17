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

Why there's a second, older vintage: the Census retires and redraws county
geography (Connecticut swapped its eight counties for planning regions in
2022; Alaska has folded and split census areas repeatedly; a handful of
independent cities have merged into their surrounding county), but NWS SAME
alerts keep using whatever county code was current when the encoder table was
built and never renumber to follow the Census. The result is live SAME traffic
keyed to FIPS codes the current Gazetteer no longer lists at all. Rather than
hand-type coordinates for the orphaned codes, we pull them from an older
Gazetteer vintage that still carries them — sourced, reproducible, and the
next re-run makes it obvious if the gap changed. The current vintage always
wins where it has an entry; the legacy vintage only fills what's missing.

Usage:
  python3 scripts/build-same-counties.py                          # download both vintages
  python3 scripts/build-same-counties.py --file <path>             # current vintage from disk
  python3 scripts/build-same-counties.py --legacy-file <path>      # legacy vintage from disk
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

# 2012 is the oldest vintage still published under this URL scheme (nothing
# earlier resolves). It still carries Connecticut's eight counties, two of
# the five retired Alaska census areas (Valdez-Cordova, Wade Hampton — the
# other three were dissolved before 2012 and are gone even here), Shannon
# County SD (renamed Oglala Lakota in 2015), and Bedford city VA (merged into
# Bedford County in 2013). Clifton Forge city VA merged in 2001, older than
# any vintage available at this URL, so it stays unresolved.
LEGACY_GAZETTEER_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
                        "2012_Gazetteer/2012_Gaz_counties_national.zip")
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


def _fetch(path, url, label):
    """Return the Gazetteer text from a local path if given, else download url."""
    if path:
        raw = open(path, "rb").read()
    else:
        print(f"downloading {label} vintage: {url}")
        with urllib.request.urlopen(url, timeout=60) as r:
            raw = r.read()

    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = [n for n in z.namelist() if n.endswith(".txt")][0]
            return z.read(name).decode("latin-1")
    return raw.decode("latin-1")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", help="local current-vintage Gazetteer .zip or .txt instead of downloading")
    p.add_argument("--legacy-file", help="local legacy-vintage Gazetteer .zip or .txt instead of downloading")
    args = p.parse_args()

    text = _fetch(args.file, GAZETTEER_URL, "current")
    table = build(text)
    if len(table) < 3000:
        print(f"ERROR: only {len(table)} counties parsed — expected ~3,144", file=sys.stderr)
        return 1
    base_count = len(table)

    legacy_text = _fetch(args.legacy_file, LEGACY_GAZETTEER_URL, "legacy")
    legacy_table = build(legacy_text)
    supplement_keys = sorted(k for k in legacy_table if k not in table)
    for k in supplement_keys:
        table[k] = legacy_table[k]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(table, fh, separators=(",", ":"), sort_keys=True)
        fh.write("\n")
    print(f"current vintage: {base_count} counties")
    print(f"legacy supplement: {len(supplement_keys)} counties not in the current vintage — {supplement_keys}")
    print(f"wrote {OUT} — {len(table)} counties, {os.path.getsize(OUT) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
