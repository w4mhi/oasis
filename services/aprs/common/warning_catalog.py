"""Load the map-warning catalog (maps/traffic/warnings.json) and expose the
type → (symbol_table, symbol) mapping used when building APRS object beacons.
"""
import json
import os


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
