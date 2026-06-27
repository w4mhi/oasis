"""
lookup.py
---------
Core engine for the off-grid FCC amateur-radio lookup.

Two responsibilities:
  1. build_index()  -- run ONCE after downloading a fresh FCC dump.
                       Produces a sorted callsign -> byte-offset index file.
  2. lookup()       -- fast per-query lookup using binary search over the
                       index, then a single seek into EN.dat.

No database engine is used. Everything is flat files, which keeps the
memory footprint tiny -- important on a Raspberry Pi.

EN.dat field positions (pipe-delimited, 1-based per the FCC spec):
   5  = Call Sign
   8  = Entity Name (used for clubs/orgs)
   9  = First Name
  11  = Last Name
  17  = City
  18  = State
  19  = Zip Code
"""

import csv
import os

# Paths are resolved relative to this file so the app works regardless of
# the current working directory when launched (e.g. via systemd on the Pi).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Data lives in the sibling fcc-offline-database/data/ directory.
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "fcc-offline-database", "data"))

EN_DAT_PATH   = os.path.join(DATA_DIR, "EN.dat")
HD_DAT_PATH   = os.path.join(DATA_DIR, "HD.dat")
INDEX_PATH    = os.path.join(DATA_DIR, "EN.idx")
NAME_IDX_PATH = os.path.join(DATA_DIR, "EN_name.idx")
GRID_IDX_PATH = os.path.join(DATA_DIR, "EN_grid.idx")
ZIP_PATH      = os.path.join(DATA_DIR, "zipcodes.csv")

# 0-based column indices into the pipe-delimited EN.dat record.
COL_CALLSIGN = 4
COL_ENTITY_NAME = 7
COL_FIRST_NAME = 8
COL_LAST_NAME = 10
COL_CITY = 16
COL_STATE = 17
COL_ZIP = 18

# EN.dat and HD.dat share the Unique System Identifier at column index 1.
# This is the join key that ties an entity record to its license header.
COL_USI = 1

# 0-based column indices into the pipe-delimited HD.dat record.
HD_COL_USI = 1
HD_COL_CALLSIGN = 4
HD_COL_LICENSE_STATUS = 5

# License Status code for an active license.
ACTIVE_STATUS = "A"


# --------------------------------------------------------------------------
# ZIP -> lat/long table (loaded once into memory; it is small).
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Index builder -- run once per FCC data refresh.
# --------------------------------------------------------------------------
def load_active_usis(hd_path=HD_DAT_PATH):
    """
    Read HD.dat and return a set of Unique System Identifiers (as bytes)
    whose License Status is 'A' (active).

    HD.dat is the license-header file. A call sign can appear more than once
    across EN.dat because the FCC retains entity records for expired/cancelled
    licenses. Filtering by active status via the shared system identifier lets
    us keep only the current licensee for each call sign.

    Returns a set of USI byte strings, or None if HD.dat is not present (in
    which case build_index falls back to indexing everything, unfiltered).
    """
    if not os.path.exists(hd_path):
        return None

    active = set()
    with open(hd_path, "rb") as fh:
        for raw in fh:
            # Split only as far as the status column (index 5).
            parts = raw.split(b"|", HD_COL_LICENSE_STATUS + 1)
            if len(parts) > HD_COL_LICENSE_STATUS:
                status = parts[HD_COL_LICENSE_STATUS].strip().upper()
                if status == ACTIVE_STATUS.encode("ascii"):
                    usi = parts[HD_COL_USI].strip()
                    if usi:
                        active.add(usi)
    return active


def build_index(en_path=EN_DAT_PATH, index_path=INDEX_PATH, hd_path=HD_DAT_PATH):
    """
    Scan EN.dat once and write a sorted index of "CALLSIGN|byte_offset",
    keeping only records for licenses that are currently active per HD.dat.

    The byte offset lets lookup() seek directly to a record instead of
    rescanning the whole file. The index is sorted by call sign so lookups
    can use binary search.

    Returns a tuple: (number_of_indexed_callsigns, filtered_bool).
    'filtered_bool' is True when HD.dat was used to keep active-only records,
    False when HD.dat was absent and all records were indexed.
    """
    if not os.path.exists(en_path):
        raise FileNotFoundError(
            f"Could not find {en_path}. Download l_amat.zip from the FCC, "
            f"unzip it, and place EN.dat in the data/ directory."
        )

    # Load the set of active system identifiers from HD.dat first.
    active_usis = load_active_usis(hd_path)
    filtered = active_usis is not None

    entries = []
    # Read in binary so byte offsets are exact regardless of encoding.
    with open(en_path, "rb") as fh:
        offset = 0
        for raw in fh:
            # We need the call sign (index 4) and the system identifier
            # (index 1), so split far enough to reach the call sign.
            parts = raw.split(b"|", COL_CALLSIGN + 1)
            if len(parts) > COL_CALLSIGN:
                callsign = parts[COL_CALLSIGN].strip().upper()
                usi = parts[COL_USI].strip()
                # Keep this record only if it is active (or if we have no
                # HD.dat to filter against, keep everything as before).
                keep = bool(callsign) and (
                    not filtered or usi in active_usis
                )
                if keep:
                    entries.append((callsign, offset))
            offset += len(raw)

    entries.sort(key=lambda e: e[0])

    with open(index_path, "wb") as out:
        for callsign, off in entries:
            out.write(callsign + b"|" + str(off).encode("ascii") + b"\n")

    return len(entries), filtered


# --------------------------------------------------------------------------
# Binary search over the sorted index file.
# --------------------------------------------------------------------------
def _find_offset(callsign, index_path=INDEX_PATH):
    """
    Binary-search the sorted index file for an exact call-sign match.
    Returns the byte offset into EN.dat, or None if not found.
    """
    target = callsign.strip().upper().encode("ascii", "ignore")
    if not target:
        return None

    file_size = os.path.getsize(index_path)
    with open(index_path, "rb") as fh:
        lo, hi = 0, file_size
        # Bisect down to a small window, then scan it linearly. The linear
        # scan guarantees we never skip a short line during alignment.
        while hi - lo > 256:
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()  # discard partial line
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

        # Linear scan of the remaining ≤256-byte window [lo, hi).
        # 256 bytes covers ~15–25 index lines (each line is 10–18 bytes for
        # a 3–6 char call sign + "|"+offset+"\n"), which is enough to guarantee
        # we land cleanly on the target without over-reading.
        # 'lo' is always already at a line boundary (it is only ever set to 0
        # or to the byte just past a complete line), so do NOT realign here.
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
                break  # sorted: gone past where it would be
    return None


# --------------------------------------------------------------------------
# Prefix-range binary search (supports wildcard/partial-callsign queries).
# --------------------------------------------------------------------------
def _find_prefix_range(prefix, index_path=INDEX_PATH):
    """
    Binary-search the sorted index to find the contiguous byte range that
    contains every line whose key starts with *prefix*.

    Returns (range_start, range_end) as byte offsets into the index file,
    where range_start is the offset of the first matching line and range_end
    is the offset just past the last matching line.  Returns (None, None) if
    no lines match.

    Algorithm:
      - Lower bound: find the first key >= prefix.
      - Upper bound: find the first key >= (prefix with last char incremented
        by one), which is the first key that cannot start with prefix.
    The two binary searches share the same helper so the index is opened once.
    """
    target = prefix.strip().upper().encode("ascii", "ignore")
    if not target:
        return None, None

    file_size = os.path.getsize(index_path)

    def _lower_bound(fh):
        """Return byte offset of first line whose key >= target."""
        lo, hi = 0, file_size
        while hi - lo > 256:
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()          # skip partial line
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
        # Linear scan of remaining window.
        fh.seek(lo)
        while fh.tell() < hi:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                break
            key = line.split(b"|", 1)[0]
            if key >= target:
                return pos
        return hi  # nothing found in range

    # Upper-bound target: first key that sorts after all prefix matches.
    # Increment the last byte of the prefix (e.g. "W7" -> "W8").
    upper_target = target[:-1] + bytes([target[-1] + 1])

    with open(index_path, "rb") as fh:
        range_start = _lower_bound(fh)
        if range_start >= file_size:
            return None, None

        # For the upper bound, reuse the same logic with upper_target.
        # Temporarily patch the closure target.
        saved = target
        # We re-run _lower_bound logic inline with upper_target.
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


# --------------------------------------------------------------------------
# Public lookup.
# --------------------------------------------------------------------------
def _parse_record(raw, zip_table):
    """Turn a raw EN.dat line (bytes) into a result dict."""
    # FCC data is Latin-1; decode leniently so odd bytes never crash us.
    fields = raw.decode("latin-1").rstrip("\n").rstrip("\r").split("|")

    def get(i):
        return fields[i].strip() if i < len(fields) else ""

    callsign = get(COL_CALLSIGN)
    first = get(COL_FIRST_NAME)
    last = get(COL_LAST_NAME)
    entity = get(COL_ENTITY_NAME)

    # Individuals have first/last names; clubs use the entity name.
    name = " ".join(p for p in (first, last) if p).strip() or entity

    city = get(COL_CITY)
    state = get(COL_STATE)
    zip_full = get(COL_ZIP)
    zip5 = zip_full[:5]

    grid = None
    lat = None
    lon = None
    if zip5 in zip_table:
        from maidenhead import latlon_to_grid
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
    """
    Look up a single call sign (exact match). Returns a result dict or None.
    """
    if zip_table is None:
        zip_table = load_zip_table()

    if not os.path.exists(index_path):
        raise FileNotFoundError(
            "Index not built yet. Run:  python3 scripts/setup-fcc-database.py"
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
    """
    Return up to *limit* result dicts for call signs that start with *prefix*.

    The search is case-insensitive.  A trailing ``*`` is accepted and stripped
    so callers can pass either ``"W7"`` or ``"W7*"``; both are equivalent.

    Returns a list (possibly empty) of the same dicts that ``lookup()`` returns.
    Raises ``FileNotFoundError`` if the index has not been built yet.
    """
    if zip_table is None:
        zip_table = load_zip_table()

    if not os.path.exists(index_path):
        raise FileNotFoundError(
            "Index not built yet. Run:  python3 scripts/setup-fcc-database.py"
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


# --------------------------------------------------------------------------
# Name index builder — sorted by LASTNAME\tFIRSTNAME|callsign|offset.
# --------------------------------------------------------------------------
def build_name_index(en_path=EN_DAT_PATH, index_path=NAME_IDX_PATH,
                     hd_path=HD_DAT_PATH):
    """
    Build EN_name.idx: each line is  LASTNAME\tFIRSTNAME|CALLSIGN|byte_offset
    sorted lexicographically so binary / prefix search by last name works.
    Only active-license records are included (HD.dat filtered, same as build_index).
    Clubs/orgs with no last name are indexed under their entity name in the
    LASTNAME field with an empty FIRSTNAME.
    Returns the number of entries written.
    """
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
                usi      = parts[COL_USI].strip()
                callsign = parts[COL_CALLSIGN].strip().upper()
                keep = bool(callsign) and (not filtered or usi in active_usis)
                if keep:
                    last  = parts[COL_LAST_NAME].strip().upper()
                    first = parts[COL_FIRST_NAME].strip().upper() if len(parts) > COL_FIRST_NAME else b""
                    entity = parts[COL_ENTITY_NAME].strip().upper() if len(parts) > COL_ENTITY_NAME else b""
                    # Use entity name for clubs when no last name
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


# --------------------------------------------------------------------------
# Grid index builder — sorted by GRID4|callsign|offset.
# --------------------------------------------------------------------------
def build_grid_index(en_path=EN_DAT_PATH, index_path=GRID_IDX_PATH,
                     hd_path=HD_DAT_PATH, zip_path=ZIP_PATH):
    """
    Build EN_grid.idx: each line is  GRID4|CALLSIGN|byte_offset  sorted by
    the 4-character grid square derived from the licensee's ZIP code centroid.
    Only active-license records with a known ZIP (and therefore a grid) are
    included.
    Returns the number of entries written.
    """
    if not os.path.exists(en_path):
        return 0

    active_usis = load_active_usis(hd_path)
    filtered = active_usis is not None

    # Load ZIP table; grid derived via maidenhead module.
    zip_table = {}
    if os.path.exists(zip_path):
        with open(zip_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                zip5 = (row.get("zip") or "").strip()[:5]
                try:
                    zip_table[zip5] = (float(row["lat"]), float(row["lon"]))
                except (KeyError, ValueError):
                    continue

    from maidenhead import latlon_to_grid

    entries = []
    with open(en_path, "rb") as fh:
        offset = 0
        for raw in fh:
            parts = raw.split(b"|")
            if len(parts) > max(COL_CALLSIGN, COL_ZIP, COL_USI):
                usi      = parts[COL_USI].strip()
                callsign = parts[COL_CALLSIGN].strip().upper()
                keep = bool(callsign) and (not filtered or usi in active_usis)
                if keep:
                    zip5 = parts[COL_ZIP].strip().decode("ascii", "ignore")[:5]
                    if zip5 in zip_table:
                        lat, lon = zip_table[zip5]
                        grid6 = latlon_to_grid(lat, lon, precision=6)
                        if grid6:
                            entries.append((grid6.upper().encode("ascii"), callsign, offset))
            offset += len(raw)

    entries.sort(key=lambda e: e[0])

    with open(index_path, "wb") as out:
        for grid6, callsign, off in entries:
            out.write(grid6 + b"|" + callsign + b"|" + str(off).encode("ascii") + b"\n")

    return len(entries)


# --------------------------------------------------------------------------
# Name lookup — prefix search over EN_name.idx.
# --------------------------------------------------------------------------
MAX_NAME_RESULTS = 50


def lookup_by_name(last, first=None, limit=MAX_NAME_RESULTS,
                   zip_table=None, en_path=EN_DAT_PATH,
                   index_path=NAME_IDX_PATH):
    """
    Search active licenses by last name (prefix match, case-insensitive).
    Optionally further filter by first-name prefix.
    Returns up to *limit* result dicts sorted by last name then first name.
    Raises FileNotFoundError if the name index has not been built.
    """
    if zip_table is None:
        zip_table = load_zip_table()

    if not os.path.exists(index_path):
        raise FileNotFoundError(
            "Name index not built yet. Run:  python3 scripts/setup-fcc-database.py"
        )

    last_clean   = last.strip().upper()
    first_clean  = (first or "").strip().upper()
    last_upper   = last_clean.encode("ascii", "ignore")
    first_upper  = first_clean.encode("ascii", "ignore")

    # _find_prefix_range expects a str (it encodes internally). Pass just the
    # last name — the range will include SMITHSON when searching SMITH, so we
    # add an exact prefix check on the last-name part inside the linear scan.
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
            # Format: LASTNAME\tFIRSTNAME|CALLSIGN|offset
            parts = line.rstrip(b"\n").split(b"|")
            if len(parts) != 3:
                continue
            key_part = parts[0]   # LASTNAME\tFIRSTNAME
            # Exact last-name prefix check (range may include SMITHSON for SMITH)
            actual_last = key_part.split(b"\t", 1)[0]
            if not actual_last.startswith(last_upper):
                continue
            # Apply first-name filter if requested
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


# --------------------------------------------------------------------------
# Grid lookup — prefix search over EN_grid.idx.
# --------------------------------------------------------------------------
MAX_GRID_RESULTS = 100


def lookup_by_grid(grid_prefix, limit=MAX_GRID_RESULTS,
                   zip_table=None, en_path=EN_DAT_PATH,
                   index_path=GRID_IDX_PATH):
    """
    Return up to *limit* active licenses whose grid square starts with
    *grid_prefix* (e.g. "CN87" returns all licensees in that 4-char square;
    "CN" returns everyone in the CN field).
    Returns a list of result dicts sorted by grid then callsign.
    Raises FileNotFoundError if the grid index has not been built.
    """
    if zip_table is None:
        zip_table = load_zip_table()

    if not os.path.exists(index_path):
        raise FileNotFoundError(
            "Grid index not built yet. Run:  python3 scripts/setup-fcc-database.py"
        )

    # _find_prefix_range expects a str (it encodes internally).
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
            # Format: GRID4|CALLSIGN|offset
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
