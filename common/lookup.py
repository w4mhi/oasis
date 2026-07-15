"""
common/lookup.py
----------------
Shared engine for the off-grid FCC amateur-radio lookup.

This module is the root-common home for the FCC lookup functionality so it is
used consistently by the server and any other callers.
"""

import csv
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "services", "fcc_database", "data"))

EN_DAT_PATH   = os.path.join(DATA_DIR, "EN.dat")
HD_DAT_PATH   = os.path.join(DATA_DIR, "HD.dat")
INDEX_PATH    = os.path.join(DATA_DIR, "EN.idx")
NAME_IDX_PATH = os.path.join(DATA_DIR, "EN_name.idx")
GRID_IDX_PATH = os.path.join(DATA_DIR, "EN_grid.idx")
ZIP_PATH      = os.path.join(DATA_DIR, "zipcodes.csv")

COL_CALLSIGN = 4
COL_ENTITY_NAME = 7
COL_FIRST_NAME = 8
COL_LAST_NAME = 10
COL_CITY = 16
COL_STATE = 17
COL_ZIP = 18
COL_USI = 1

HD_COL_USI = 1
HD_COL_CALLSIGN = 4
HD_COL_LICENSE_STATUS = 5
ACTIVE_STATUS = "A"


def load_zip_table():
    """Load the ZIP -> (lat, lon) table into a dict. Returns {} if missing."""
    table = {}
    if not os.path.exists(ZIP_PATH):
        return table
    with open(ZIP_PATH, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            zip5 = (row.get("zip") or "").strip()[:5]
            if not zip5:
                continue
            try:
                table[zip5] = (float(row["lat"]), float(row["lon"]))
            except (KeyError, ValueError):
                continue
    return table


def load_active_usis(hd_path=HD_DAT_PATH):
    if not os.path.exists(hd_path):
        return None
    active = set()
    with open(hd_path, "rb") as fh:
        for raw in fh:
            parts = raw.split(b"|", HD_COL_LICENSE_STATUS + 1)
            if len(parts) > HD_COL_LICENSE_STATUS:
                status = parts[HD_COL_LICENSE_STATUS].strip().upper()
                if status == ACTIVE_STATUS.encode("ascii"):
                    usi = parts[HD_COL_USI].strip()
                    if usi:
                        active.add(usi)
    return active


def build_index(en_path=EN_DAT_PATH, index_path=INDEX_PATH, hd_path=HD_DAT_PATH):
    if not os.path.exists(en_path):
        raise FileNotFoundError(
            f"Could not find {en_path}. Download l_amat.zip from the FCC, "
            f"unzip it, and place EN.dat in the data/ directory."
        )
    active_usis = load_active_usis(hd_path)
    filtered = active_usis is not None
    entries = []
    with open(en_path, "rb") as fh:
        offset = 0
        for raw in fh:
            parts = raw.split(b"|", COL_CALLSIGN + 1)
            if len(parts) > COL_CALLSIGN:
                callsign = parts[COL_CALLSIGN].strip().upper()
                usi = parts[COL_USI].strip()
                keep = bool(callsign) and (not filtered or usi in active_usis)
                if keep:
                    entries.append((callsign, offset))
            offset += len(raw)
    entries.sort(key=lambda e: e[0])
    with open(index_path, "wb") as out:
        for callsign, off in entries:
            out.write(callsign + b"|" + str(off).encode("ascii") + b"\n")
    return len(entries), filtered


def _find_offset(callsign, index_path=INDEX_PATH):
    target = callsign.strip().upper().encode("ascii", "ignore")
    if not target:
        return None
    file_size = os.path.getsize(index_path)
    with open(index_path, "rb") as fh:
        lo, hi = 0, file_size
        while hi - lo > 256:
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()
            line_start = fh.tell()
            if line_start >= hi:
                hi = mid
                continue
            line = fh.readline()
            if not line:
                hi = mid
                continue
            key = line.split(b"|", 1)[0]
            if key == target:
                try:
                    return int(line.split(b"|", 1)[1])
                except (IndexError, ValueError):
                    return None
            elif key < target:
                lo = line_start + len(line)
            else:
                hi = line_start
        fh.seek(lo)
        while fh.tell() < hi:
            line = fh.readline()
            if not line:
                break
            key = line.split(b"|", 1)[0]
            if key == target:
                try:
                    return int(line.split(b"|", 1)[1])
                except (IndexError, ValueError):
                    return None
            if key > target:
                break
    return None


def _find_prefix_range(prefix, index_path=INDEX_PATH):
    target = prefix.strip().upper().encode("ascii", "ignore")
    if not target:
        return None, None
    file_size = os.path.getsize(index_path)

    def _lower_bound(fh):
        lo, hi = 0, file_size
        while hi - lo > 256:
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()
            line_start = fh.tell()
            if line_start >= hi:
                hi = mid
                continue
            line = fh.readline()
            if not line:
                hi = mid
                continue
            key = line.split(b"|", 1)[0]
            if key < target:
                lo = line_start + len(line)
            else:
                hi = line_start
        fh.seek(lo)
        while fh.tell() < hi:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                break
            key = line.split(b"|", 1)[0]
            if key >= target:
                return pos
        return hi

    upper_target = target[:-1] + bytes([target[-1] + 1])
    with open(index_path, "rb") as fh:
        range_start = _lower_bound(fh)
        if range_start >= file_size:
            return None, None
        lo, hi = 0, file_size
        ut = upper_target
        while hi - lo > 256:
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()
            line_start = fh.tell()
            if line_start >= hi:
                hi = mid
                continue
            line = fh.readline()
            if not line:
                hi = mid
                continue
            key = line.split(b"|", 1)[0]
            if key < ut:
                lo = line_start + len(line)
            else:
                hi = line_start
        fh.seek(lo)
        range_end = lo
        while fh.tell() < hi:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                break
            key = line.split(b"|", 1)[0]
            if key >= ut:
                range_end = pos
                break
            range_end = fh.tell()
        else:
            range_end = fh.tell()
    if range_start >= range_end:
        return None, None
    return range_start, range_end


def _parse_record(raw, zip_table):
    fields = raw.decode("latin-1").rstrip("\n").rstrip("\r").split("|")

    def get(i):
        return fields[i].strip() if i < len(fields) else ""

    callsign = get(COL_CALLSIGN)
    first = get(COL_FIRST_NAME)
    last = get(COL_LAST_NAME)
    entity = get(COL_ENTITY_NAME)
    name = " ".join(p for p in (first, last) if p).strip() or entity

    city = get(COL_CITY)
    state = get(COL_STATE)
    zip_full = get(COL_ZIP)
    zip5 = zip_full[:5]

    grid = None
    lat = None
    lon = None
    if zip5 in zip_table:
        from common.maidenhead import latlon_to_grid
        lat, lon = zip_table[zip5]
        grid = latlon_to_grid(lat, lon, precision=6)

    return {
        "callsign": callsign,
        "name": name,
        "city": city,
        "state": state,
        "zip": zip5,
        "grid": grid,
        "lat": lat,
        "lon": lon,
    }


def lookup(callsign, zip_table=None, en_path=EN_DAT_PATH, index_path=INDEX_PATH):
    if zip_table is None:
        zip_table = load_zip_table()
    if not os.path.exists(index_path):
        raise FileNotFoundError(
            "Index not built yet. Run:  python3 scripts/install-fcc-database.py"
        )
    offset = _find_offset(callsign, index_path)
    if offset is None:
        return None
    with open(en_path, "rb") as fh:
        fh.seek(offset)
        raw = fh.readline()
    if not raw:
        return None
    return _parse_record(raw, zip_table)

MAX_PREFIX_RESULTS = 50


def lookup_prefix(prefix, zip_table=None, limit=MAX_PREFIX_RESULTS,
                  en_path=EN_DAT_PATH, index_path=INDEX_PATH):
    if zip_table is None:
        zip_table = load_zip_table()
    if not os.path.exists(index_path):
        raise FileNotFoundError(
            "Index not built yet. Run:  python3 scripts/install-fcc-database.py"
        )
    clean = prefix.strip().upper().rstrip("*")
    if not clean:
        return []
    range_start, range_end = _find_prefix_range(clean, index_path)
    if range_start is None:
        return []
    results = []
    with open(index_path, "rb") as idx_fh, open(en_path, "rb") as en_fh:
        idx_fh.seek(range_start)
        while idx_fh.tell() < range_end and len(results) < limit:
            line = idx_fh.readline()
            if not line:
                break
            parts = line.split(b"|", 1)
            if len(parts) != 2:
                continue
            try:
                offset = int(parts[1])
            except ValueError:
                continue
            en_fh.seek(offset)
            raw = en_fh.readline()
            if raw:
                results.append(_parse_record(raw, zip_table))
    return results


def build_name_index(en_path=EN_DAT_PATH, index_path=NAME_IDX_PATH,
                     hd_path=HD_DAT_PATH):
    if not os.path.exists(en_path):
        return 0
    active_usis = load_active_usis(hd_path)
    filtered = active_usis is not None
    zip_table = load_zip_table()
    entries = []
    with open(en_path, "rb") as fh:
        offset = 0
        for raw in fh:
            parts = raw.split(b"|")
            if len(parts) > max(COL_CALLSIGN, COL_LAST_NAME, COL_USI):
                usi = parts[COL_USI].strip()
                callsign = parts[COL_CALLSIGN].strip().upper()
                keep = bool(callsign) and (not filtered or usi in active_usis)
                if keep:
                    last = parts[COL_LAST_NAME].strip().upper()
                    first = parts[COL_FIRST_NAME].strip().upper() if len(parts) > COL_FIRST_NAME else b""
                    entity = parts[COL_ENTITY_NAME].strip().upper() if len(parts) > COL_ENTITY_NAME else b""
                    if not last:
                        last = entity
                    if last:
                        key = last + b"\t" + first
                        entries.append((key, callsign, offset))
            offset += len(raw)
    entries.sort(key=lambda e: e[0])
    with open(index_path, "wb") as out:
        for key, callsign, off in entries:
            out.write(key + b"|" + callsign + b"|" + str(off).encode("ascii") + b"\n")
    return len(entries)


MAX_NAME_RESULTS = 50
MAX_GRID_RESULTS = 100


def lookup_by_name(last, first=None, limit=MAX_NAME_RESULTS,
                   zip_table=None, en_path=EN_DAT_PATH,
                   index_path=NAME_IDX_PATH):
    if zip_table is None:
        zip_table = load_zip_table()
    if not os.path.exists(index_path):
        raise FileNotFoundError(
            "Name index not built yet. Run:  python3 scripts/install-fcc-database.py"
        )
    last_clean = last.strip().upper()
    first_clean = (first or "").strip().upper()
    last_upper = last_clean.encode("ascii", "ignore")
    first_upper = first_clean.encode("ascii", "ignore")
    range_start, range_end = _find_prefix_range(last_clean, index_path)
    if range_start is None:
        return []
    results = []
    with open(index_path, "rb") as idx_fh, open(en_path, "rb") as en_fh:
        idx_fh.seek(range_start)
        while idx_fh.tell() < range_end and len(results) < limit:
            line = idx_fh.readline()
            if not line:
                break
            parts = line.rstrip(b"\n").split(b"|")
            if len(parts) != 3:
                continue
            key_part = parts[0]
            actual_last = key_part.split(b"\t", 1)[0]
            if not actual_last.startswith(last_upper):
                continue
            if first_upper:
                fname_in_key = key_part.split(b"\t", 1)[1] if b"\t" in key_part else b""
                if not fname_in_key.startswith(first_upper):
                    continue
            try:
                offset = int(parts[2])
            except ValueError:
                continue
            en_fh.seek(offset)
            raw = en_fh.readline()
            if raw:
                results.append(_parse_record(raw, zip_table))
    return results


def lookup_by_grid(grid_prefix, limit=MAX_GRID_RESULTS,
                   zip_table=None, en_path=EN_DAT_PATH,
                   index_path=GRID_IDX_PATH):
    if zip_table is None:
        zip_table = load_zip_table()
    if not os.path.exists(index_path):
        raise FileNotFoundError(
            "Grid index not built yet. Run:  python3 scripts/install-fcc-database.py"
        )
    clean = grid_prefix.strip().upper()
    if not clean:
        return []
    range_start, range_end = _find_prefix_range(clean, index_path)
    if range_start is None:
        return []
    results = []
    with open(index_path, "rb") as idx_fh, open(en_path, "rb") as en_fh:
        idx_fh.seek(range_start)
        while idx_fh.tell() < range_end and len(results) < limit:
            line = idx_fh.readline()
            if not line:
                break
            parts = line.rstrip(b"\n").split(b"|")
            if len(parts) != 3:
                continue
            try:
                offset = int(parts[2])
            except ValueError:
                continue
            en_fh.seek(offset)
            raw = en_fh.readline()
            if raw:
                results.append(_parse_record(raw, zip_table))
    return results
