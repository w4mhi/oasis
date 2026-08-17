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


# (Census suffix to strip, word to SPEAK) — checked in order, first match
# wins. Order matters: "City and Borough" must be checked before "Borough"
# alone, because "Borough" is itself a suffix of "City and Borough" — Juneau,
# Sitka, Wrangell and Yakutat would otherwise lose "City and" and be left
# with the truncated, garbage name "Juneau City and". The stripped suffix and
# the spoken word differ for Puerto Rico: the Census spells it "Municipio",
# but English-language NWS broadcasts and press call these "Municipality" —
# that's a deliberate translation, not a typo.
REGION_SUFFIXES = (
    (" City and Borough", "City and Borough"),
    (" Census Area", "Census Area"),
    (" Planning Region", "Planning Region"),   # CT's post-2022 replacement for counties
    (" Parish", "Parish"),                     # Louisiana has no counties
    (" Borough", "Borough"),                   # Alaska
    (" Municipality", "Municipality"),         # Alaska (Anchorage, Skagway)
    (" Municipio", "Municipality"),            # Puerto Rico — see comment above
    (" County", "County"),
    (" city", "City"),                         # VA/MD/MO/NV independent cities
)


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
        region_type = ""    # "" = no suffix to strip (DC, Carson City NV):
                             # the Gazetteer name is already complete, so this
                             # must be recorded as KNOWN-bare, not left absent
                             # (absent means "unknown region type" downstream
                             # in announce.py, which is a different thing).
        for suffix, spoken in REGION_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                region_type = spoken
                break
        out[geoid] = {"n": name, "s": r.get("USPS", ""), "lat": lat, "lon": lon,
                      "t": region_type}
    return out


def merge_legacy(current, legacy):
    """Fill gaps in `current` from `legacy` without ever overwriting a current
    entry. Pure (no I/O), so the "current vintage always wins" safety property
    can be unit-tested directly instead of only re-verified by a manual run.

    Returns (merged_table, supplement_keys) — merged_table is a new dict,
    supplement_keys is the sorted list of keys that came from legacy only.
    """
    merged = dict(current)
    supplement_keys = sorted(k for k in legacy if k not in current)
    for k in supplement_keys:
        merged[k] = legacy[k]
    return merged, supplement_keys


def _decode(raw):
    """Decode Gazetteer bytes as UTF-8, falling back to latin-1.

    The 2023 vintage (and every recent one we've checked) is UTF-8 — that's
    what "DoÃ±a Ana" turning up in the committed table was: genuine UTF-8
    bytes for "Doña Ana" wrongly forced through a latin-1 decode, which never
    raises (latin-1 maps every byte 0-255 to a codepoint) so nothing ever
    caught it. But Census vintages have not always been consistent — the 2012
    legacy file this script also reads really is latin-1 and fails a strict
    UTF-8 decode — so the fallback stays, guarded by an actual decode error
    instead of being the unconditional first choice.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


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
            return _decode(z.read(name))
    return _decode(raw)


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
    # The 2012 file is a full Gazetteer snapshot too (3,221 counties when this
    # was written), not just the handful of retired codes we're after — so the
    # same floor as the current vintage applies. Without it, a bad download
    # (an HTTP error page that doesn't raise, a column-header change) parses
    # to zero or near-zero rows, `supplement_keys` comes back empty, and the
    # table gets silently rewritten without the legacy codes — the exact
    # "Connecticut decodes but never plots" bug this supplement exists to fix,
    # now reintroduced by a green run instead of caught by one.
    if len(legacy_table) < 3000:
        print(f"ERROR: only {len(legacy_table)} legacy counties parsed — expected ~3,200", file=sys.stderr)
        return 1

    table, supplement_keys = merge_legacy(table, legacy_table)

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
