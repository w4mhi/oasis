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
    rows.sort(key=_by_state_name)
    return rows


def _by_state_name(row):
    return (row["state"], row["name"])


# A picker types into this. Anything longer than a county name plus a state is
# not a query anyone is typing, and truncating beats scanning 3234 entries
# against it.
_MAX_QUERY = 48


def search(query, table=None):
    """Counties matching `query` — name-PREFIX matches first, then the rest.

    A station watches a handful of counties, not three thousand, so this is
    built for "type two letters and pick it" rather than for scrolling: the
    query matches a county-name prefix, a name substring, a two-letter state
    code, or a FIPS prefix, and "king, wa" narrows by both. An empty query is
    the whole table, in all_counties() order — the caller still bounds it.

    Ordering is deterministic (contract §4): prefix group then substring group,
    each by state then name. Prefix-first because a picker that answers "ki"
    with Alabama is a picker nobody types into twice.

    Filtering the raw table rather than all_counties() is deliberate: this
    builds a dict per MATCH, not 3234 of them per keystroke, on a box whose
    supported minimum is a Pi 3.
    """
    q = str(query or "").strip().lower()[:_MAX_QUERY]
    if not q:
        return all_counties(table)
    name_q, _, state_q = q.partition(",")
    name_q, state_q = name_q.strip(), state_q.strip()
    lead, rest = [], []
    for key, v in (table or {}).items():
        state = str(v.get("s") or "").lower()
        if state_q and state != state_q:
            continue
        name = str(v.get("n") or "").lower()
        row = {"fips5": key, "name": v["n"], "state": v["s"]}
        # No name to match on means the comma form ("…, wa"): the state IS the
        # whole query, so every county in it is a lead hit.
        if not name_q or name.startswith(name_q) or key.startswith(name_q):
            lead.append(row)
        elif name_q in name or (not state_q and state == name_q):
            # A bare "wa" is as much a state as it is the start of a county
            # name, so it is tried as both — Walla Walla first, then the rest
            # of Washington.
            rest.append(row)
    lead.sort(key=_by_state_name)
    rest.sort(key=_by_state_name)
    return lead + rest


def resolve(codes, table=None):
    """([{fips5, name, state}], [unknown fips5]) for an explicit list of codes.

    The watch list's other direction: it is STORED as codes, and a person
    reading it back needs names. Duplicates collapse and both the 5- and the
    6-digit form are accepted, exactly as settings.save() normalises them.

    A code the table does not carry comes back in `unknown` rather than being
    dropped. Four Alaskan codes lost between Gazetteer vintages (02201, 02232,
    02280 and 51560) and every marine zone land there, and they are entirely
    legitimate things to watch — not being able to NAME or PLOT an area is a
    display fact, and an alert still matters when the map cannot draw it.
    """
    tbl = table or {}
    rows, unknown, seen = [], [], set()
    for code in codes or []:
        key = _fips5(str(code).strip())
        if not key or key in seen:
            continue
        seen.add(key)
        entry = tbl.get(key)
        if entry:
            rows.append({"fips5": key, "name": entry["n"], "state": entry["s"]})
        else:
            unknown.append(key)
    rows.sort(key=_by_state_name)
    unknown.sort()
    return rows, unknown
