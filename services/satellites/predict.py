"""Skyfield pass/track prediction. Offline: timescale is builtin (no fetch),
propagation is SGP4 from the TLE alone (no .bsp). Import-safe and pure —
functions take primitives so they unit-test without Flask or hardware."""
import datetime

from skyfield.api import EarthSatellite, load, wgs84

_TS = None


def _ts():
    global _TS
    if _TS is None:
        _TS = load.timescale(builtin=True)   # builtin → never touches the network
    return _TS


def make_satellite(name, line1, line2):
    return EarthSatellite(line1, line2, name, _ts())


def _altaz(sat, observer, t):
    alt, az, _ = (sat - observer).at(t).altaz()
    return alt.degrees, az.degrees


def compute_passes(sat, lat, lon, start_dt, hours=48, min_elev=10.0):
    """Passes over [start_dt, start_dt+hours] as seen from lat/lon. Events are
    found at the 0° horizon; only passes whose culmination reaches >= min_elev
    are returned (max_el filter). Times are ISO-8601 UTC strings."""
    ts = _ts()
    observer = wgs84.latlon(lat, lon)
    t0 = ts.from_datetime(start_dt)
    t1 = ts.from_datetime(start_dt + datetime.timedelta(hours=hours))
    times, events = sat.find_events(observer, t0, t1, altitude_degrees=0.0)

    passes, cur = [], {}
    for t, e in zip(times, events):
        if e == 0:  # rise
            az = _altaz(sat, observer, t)[1]
            cur = {"rise": t.utc_datetime(), "rise_az": az}
        elif e == 1 and "rise" in cur:  # culminate
            el, _ = _altaz(sat, observer, t)
            cur["peak"] = t.utc_datetime()
            cur["max_el"] = el
        elif e == 2 and "rise" in cur and "peak" in cur:  # set
            az = _altaz(sat, observer, t)[1]
            cur["set"] = t.utc_datetime()
            cur["set_az"] = az
            if cur["max_el"] >= min_elev:
                passes.append({
                    "rise": cur["rise"].isoformat(),
                    "rise_az": cur["rise_az"],
                    "peak": cur["peak"].isoformat(),
                    "max_el": cur["max_el"],
                    "set": cur["set"].isoformat(),
                    "set_az": cur["set_az"],
                    "duration_s": (cur["set"] - cur["rise"]).total_seconds(),
                })
            cur = {}
    return passes
