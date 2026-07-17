"""ADS-B history persistence — SQLite, mirrors the GrayWolf history-DB pattern."""
import os
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS aircraft (
    icao TEXT PRIMARY KEY,
    callsign TEXT,
    first_heard REAL,
    last_heard REAL
);
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

def open_writer(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn

def _clean(v):
    return v.strip() if isinstance(v, str) else v

def record(conn, ac, ts):
    icao = ac.get("hex")
    if not icao:
        return
    callsign = _clean(ac.get("flight"))
    conn.execute(
        "INSERT INTO aircraft(icao, callsign, first_heard, last_heard) "
        "VALUES(?,?,?,?) ON CONFLICT(icao) DO UPDATE SET "
        "callsign=COALESCE(excluded.callsign, aircraft.callsign), last_heard=excluded.last_heard",
        (icao, callsign, ts, ts))
    conn.execute(
        "INSERT INTO observations(icao, ts, lat, lon, alt, speed, track, squawk) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (icao, ts, ac.get("lat"), ac.get("lon"), ac.get("alt_baro"),
         ac.get("gs"), ac.get("track"), ac.get("squawk")))

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
    """One row per aircraft — its latest observation since *since_ts* (epoch s).

    Shaped like a live `/aircraft` entry (hex/flight/lat/lon/alt_baro/gs/track/
    squawk) plus the absolute `ts` of that observation, so the front-end folds
    24 h of out-of-range history and live state into one aged list. Mirrors
    GrayWolf's latest-position-per-station read; the inner GROUP BY rides the
    idx_obs_icao_ts index so the payload stays one row per aircraft (Pi-cheap).
    """
    q = (
        "SELECT o.icao, a.callsign, o.ts, o.lat, o.lon, o.alt, o.speed, "
        "o.track, o.squawk FROM observations o "
        "JOIN (SELECT icao, MAX(ts) AS mts FROM observations "
        "      WHERE ts >= ? GROUP BY icao) m "
        "  ON o.icao = m.icao AND o.ts = m.mts "
        "LEFT JOIN aircraft a ON a.icao = o.icao "
        "ORDER BY o.ts DESC"
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
