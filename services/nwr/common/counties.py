"""FIPS county code -> name, state and a plottable position.

A SAME message names a COUNTY, not a place: there is no position anywhere in the
header, so a map marker needs this table. It is generated at build time by
scripts/build-same-counties.py from the US Census Gazetteer (public domain) and
committed — nothing here touches the network.

Keyed by the 5-digit GEOID (SSCCC). SAME sends the 6-digit PSSCCC whose leading
digit is a county subdivision, so every entry point here strips it.
"""
import json
import os
import threading

_lock = threading.Lock()
_cache = None

DATA_REL = os.path.join("services", "nwr", "data", "same-counties.json")


def load(repo_root):
    """The county table, read once and cached. {} when the file is missing —
    a station with no table still decodes and logs, it just cannot plot."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        path = os.path.join(repo_root, DATA_REL)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _cache = json.load(fh)
        except (OSError, ValueError):
            _cache = {}
        return _cache


def _fips5(fips6):
    """PSSCCC (6) -> SSCCC (5). Passes a 5-digit code through unchanged."""
    s = str(fips6 or "")
    return s[1:] if len(s) == 6 else s


def _entry(fips6, table):
    key = _fips5(fips6)
    if key.endswith("000"):
        # CCC == 000 is "the entire state". Real, but not a point — the caller
        # must handle it as a state-wide alert rather than plot a pin.
        return None
    return (table or {}).get(key)


def locate(fips6, table=None):
    """(lat, lon) for a PSSCCC code, or None.

    None is a legitimate answer with three distinct causes — a marine or
    coastal-waters pseudo-state, a state-wide code, or a county absent from this
    Gazetteer vintage. Callers must SAY there is no pin rather than drop the
    alert.
    """
    e = _entry(fips6, table)
    return (e["lat"], e["lon"]) if e else None


def describe(fips6, table=None):
    """{fips5, name, state, lat, lon, region_type} for a PSSCCC code, or None.

    region_type is what to SAY after the bare name — "Parish", "Borough",
    "" (the name is already complete, e.g. "District of Columbia"), or None
    when the table entry predates this field (an old-shape table), which
    callers must treat the same as "unknown, not in this table at all".
    """
    e = _entry(fips6, table)
    if not e:
        return None
    return {"fips5": _fips5(fips6), "name": e["n"], "state": e["s"],
            "lat": e["lat"], "lon": e["lon"], "region_type": e.get("t")}


def all_counties(table=None):
    """Every county as {fips5, name, state}, sorted by state then name — the
    Setup watch-list picker's source."""
    rows = [{"fips5": k, "name": v["n"], "state": v["s"]}
            for k, v in (table or {}).items()]
    rows.sort(key=lambda r: (r["state"], r["name"]))
    return rows
