"""ADS-B history persistence — SQLite, mirrors the GrayWolf history-DB pattern."""
import os
import sqlite3

# The `aircraft` table doubles as a latest-position snapshot (one row per
# aircraft, kept current by record()). recent() reads from it directly instead
# of GROUP-BY-scanning the whole observations table — the scan cost grew with
# total history size (months of per-second rows) and pegged a Pi core for ~2 s
# per /recent poll, tripping the resource guardian. See idx_aircraft_last_heard.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS aircraft (
    icao TEXT PRIMARY KEY,
    callsign TEXT,
    first_heard REAL,
    last_heard REAL,
    lat REAL, lon REAL, alt INTEGER, speed REAL, track REAL, squawk TEXT
);
CREATE INDEX IF NOT EXISTS idx_aircraft_last_heard ON aircraft(last_heard);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    icao TEXT NOT NULL,
    ts REAL NOT NULL,
    lat REAL, lon REAL, alt INTEGER, speed REAL, track REAL, squawk TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_icao_ts ON observations(icao, ts);
CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts);
CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    icao TEXT,
    kind TEXT NOT NULL,
    detail TEXT
);
"""

# Snapshot columns added to `aircraft` after its original (icao/callsign/
# first_heard/last_heard) release. Pre-snapshot DBs on deployed Pis predate
# these, so migrate in place — CREATE TABLE IF NOT EXISTS never alters an
# existing table. Backfill is unnecessary: each aircraft's snapshot self-heals
# the next time it's heard (seconds), and recent()'s window is only hours wide.
_AIRCRAFT_SNAPSHOT_COLS = {
    "lat": "REAL", "lon": "REAL", "alt": "INTEGER",
    "speed": "REAL", "track": "REAL", "squawk": "TEXT",
}

def _migrate(conn):
    have = {row[1] for row in conn.execute("PRAGMA table_info(aircraft)")}
    for col, decl in _AIRCRAFT_SNAPSHOT_COLS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE aircraft ADD COLUMN {col} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aircraft_last_heard "
                 "ON aircraft(last_heard)")

def open_writer(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn

def _clean(v):
    return v.strip() if isinstance(v, str) else v

def record(conn, ac, ts):
    icao = ac.get("hex")
    if not icao:
        return
    callsign = _clean(ac.get("flight"))
    lat, lon = ac.get("lat"), ac.get("lon")
    alt, gs, track, squawk = ac.get("alt_baro"), ac.get("gs"), ac.get("track"), ac.get("squawk")

    # Track breadcrumb — recorded BEFORE the snapshot update (which still holds
    # the previous position), and ONLY when this frame carries a real position
    # that DIFFERS from the last recorded one. The dump1090 poll repeats a
    # plane's unchanged lat/lon every second and Mode-S-only frames have no
    # position at all — together ~80% of frames — none of which add a point to
    # the flight path. `IS` is SQLite's NULL-safe equality; NOT EXISTS is true
    # for a first sighting (no aircraft row yet), so the first point is always
    # kept.
    if lat is not None and lon is not None:
        conn.execute(
            "INSERT INTO observations(icao, ts, lat, lon, alt, speed, track, squawk) "
            "SELECT ?,?,?,?,?,?,?,? WHERE NOT EXISTS ("
            "  SELECT 1 FROM aircraft WHERE icao=? AND lat IS ? AND lon IS ? AND alt IS ?)",
            (icao, ts, lat, lon, alt, gs, track, squawk, icao, lat, lon, alt))

    # Snapshot — always kept current (cheap one-row upsert). The WHERE guard
    # makes it monotonic: an out-of-order (older) frame never overwrites a newer
    # position, and last_heard advances every frame so recent()/last_heard stay
    # live even while the plane sits at one position.
    conn.execute(
        "INSERT INTO aircraft(icao, callsign, first_heard, last_heard, "
        "                     lat, lon, alt, speed, track, squawk) "
        "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(icao) DO UPDATE SET "
        "callsign=COALESCE(excluded.callsign, aircraft.callsign), "
        "last_heard=excluded.last_heard, lat=excluded.lat, lon=excluded.lon, "
        "alt=excluded.alt, speed=excluded.speed, track=excluded.track, "
        "squawk=excluded.squawk "
        "WHERE excluded.last_heard >= aircraft.last_heard",
        (icao, callsign, ts, ts, lat, lon, alt, gs, track, squawk))

def open_reader(db_path):
    if not os.path.exists(db_path):
        return None, f"Database not found at {db_path}"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn, None
    except Exception as exc:
        return None, str(exc)

def history(conn, since_ts, icao=None):
    q = ("SELECT o.icao, a.callsign, o.ts, o.lat, o.lon, o.alt, o.speed, "
         "o.track, o.squawk FROM observations o "
         "LEFT JOIN aircraft a ON a.icao = o.icao WHERE o.ts >= ?")
    args = [since_ts]
    if icao:
        q += " AND o.icao = ?"
        args.append(icao)
    q += " ORDER BY o.ts ASC"
    return [dict(r) for r in conn.execute(q, args).fetchall()]

def recent(conn, since_ts):
    """One row per aircraft — its latest position since *since_ts* (epoch s).

    Shaped like a live `/aircraft` entry (hex/flight/lat/lon/alt_baro/gs/track/
    squawk) plus the absolute `ts` of that observation, so the front-end folds
    hours of out-of-range history and live state into one aged list.

    Reads the `aircraft` snapshot (last position per aircraft, kept current by
    record()) via idx_aircraft_last_heard, so the cost is O(aircraft in window)
    — NOT a GROUP-BY scan of the multi-million-row observations table, which is
    what used to peg a Pi core for ~2 s per poll and arm the resource guardian.
    """
    q = (
        "SELECT icao, callsign, last_heard AS ts, lat, lon, alt, speed, "
        "track, squawk FROM aircraft WHERE last_heard >= ? "
        "ORDER BY last_heard DESC"
    )
    return [{
        "hex": r["icao"],
        "flight": r["callsign"] or "",
        "ts": r["ts"],
        "lat": r["lat"], "lon": r["lon"],
        "alt_baro": r["alt"],
        "gs": r["speed"], "track": r["track"],
        "squawk": r["squawk"] or "",
    } for r in conn.execute(q, (since_ts,)).fetchall()]


def prune(conn, older_than_ts):
    """Delete observations older than *older_than_ts* (epoch s); return the row
    count removed. Bounds the detailed-track table (idx_obs_ts makes this a
    ranged delete). The aircraft snapshot is one row per aircraft and left
    intact, so recent() still serves aged targets after their tracks age out."""
    cur = conn.execute("DELETE FROM observations WHERE ts < ?", (older_than_ts,))
    return cur.rowcount
