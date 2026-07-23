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


def compute_passes(sat, lat, lon, start_dt, hours=48, min_elev=10.0, lookback_min=30):
    """Passes over [start_dt, start_dt+hours] as seen from lat/lon. Events are
    found at the 0° horizon; only passes whose culmination reaches >= min_elev
    are returned (max_el filter). Times are ISO-8601 UTC strings.

    The search actually starts lookback_min before start_dt so a pass already in
    progress at start_dt — whose rise event lies in the past — is still captured
    whole (rise→peak→set); without this the in-progress pass is silently dropped
    because the builder needs the rise first. lookback_min must exceed the longest
    pass in view (30 min comfortably covers RTL-range LEO/MEO). Passes that already
    ended before start_dt are then discarded, so the result is unchanged when
    nothing is overhead at start_dt."""
    ts = _ts()
    observer = wgs84.latlon(lat, lon)
    t0 = ts.from_datetime(start_dt - datetime.timedelta(minutes=lookback_min))
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
            if cur["max_el"] >= min_elev and cur["set"] > start_dt:
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


_C_KM_S = 299792.458


def compute_track(sat, lat, lon, start_dt, end_dt, step_s=10, downlink_hz=None):
    """Sample the sub-satellite ground track + observer az/el over
    [start_dt, end_dt] at step_s seconds. If downlink_hz is given, include the
    Doppler shift (Hz) from the range-rate: +shift approaching, -shift receding."""
    ts = _ts()
    observer = wgs84.latlon(lat, lon)
    total = (end_dt - start_dt).total_seconds()
    n = max(2, int(total // step_s) + 1)
    pts = []
    for i in range(n):
        t = ts.from_datetime(start_dt + datetime.timedelta(seconds=i * step_s))
        topo = (sat - observer).at(t)
        alt, az, _ = topo.altaz()
        sub = wgs84.subpoint(sat.at(t))
        doppler = None
        if downlink_hz:
            r = topo.position.km
            v = topo.velocity.km_per_s
            rng = (r[0] ** 2 + r[1] ** 2 + r[2] ** 2) ** 0.5
            range_rate = (r[0] * v[0] + r[1] * v[1] + r[2] * v[2]) / rng  # +=receding
            doppler = -range_rate / _C_KM_S * downlink_hz
        pts.append({
            "t": (start_dt + datetime.timedelta(seconds=i * step_s)).isoformat(),
            "lat": sub.latitude.degrees,
            "lon": sub.longitude.degrees,
            "el": alt.degrees,
            "az": az.degrees,
            "doppler_hz": doppler,
        })
    return pts
