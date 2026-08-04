"""Load the map-warning catalog (maps/traffic/warnings.json) and expose the
type → (symbol_table, symbol) mapping used when building APRS object beacons,
plus the type → tactical-name abbreviation and the operator's station callsign.
"""
import json
import os

from common import config_paths


def load_symbol_map(repo_root):
    path = os.path.join(repo_root, "maps", "traffic", "warnings.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            catalog = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for entry in catalog if isinstance(catalog, list) else []:
        wid = entry.get("id")
        tbl = entry.get("symbol_table")
        code = entry.get("symbol")
        if wid and tbl and code:
            out[wid] = (tbl, code)
    return out


def load_abbr_map(repo_root):
    """{warning-type-id: tactical-name ABBR (uppercase, <=7 chars)}.

    Falls back to the first 7 chars of the id (uppercased) when a catalog
    entry has no "abbr" field.
    """
    path = os.path.join(repo_root, "maps", "traffic", "warnings.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            catalog = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for entry in catalog if isinstance(catalog, list) else []:
        wid = entry.get("id")
        if not wid:
            continue
        abbr = entry.get("abbr")
        out[wid] = str(abbr).upper() if abbr else str(wid)[:7].upper()
    return out


def station_callsign(repo_root):
    """The operator's station callsign from configuration/station.json, or
    None when the file is missing/unreadable or has no (or a blank) callsign."""
    path = config_paths.station_json(repo_root)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("callsign") or None
