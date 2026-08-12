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


# When a satellite is overhead at start_dt but no computed pass covers it (its
# rise predates the normal lookback — a HEO/Molniya bird can be HOURS into an
# apogee dwell), we recover the current pass with a targeted wide backsearch for
# THAT ONE sat. 12 h covers any realistic amateur pass; the common (LEO/MEO,
# nothing overhead) case never runs it, so there is no global cost.
_INPROGRESS_LOOKBACK_H = 12


def _passes_from_events(sat, observer, times, events, start_dt, min_elev):
    """Build pass dicts from a rise/peak/set event stream. Keeps only passes that
    culminate >= min_elev and have not already ended by start_dt. Chronological."""
    out, cur = [], {}
    for t, e in zip(times, events):
        if e == 0:  # rise
            cur = {"rise": t.utc_datetime(), "rise_az": _altaz(sat, observer, t)[1]}
        elif e == 1 and "rise" in cur:  # culminate
            cur["peak"] = t.utc_datetime()
            # Both halves of the same look, where only the elevation was kept.
            # WHERE a pass peaks decides whether you can work it: 40 degrees over
            # the hill to the north is unusable and 40 degrees over open water is
            # a good pass, and rise_az/set_az cannot tell them apart — a pass
            # rising NNE and setting SSW peaks somewhere the operator has to
            # guess. This is the cheap 80% of a horizon mask: no config, no new
            # maths, and the operator knows their own skyline.
            cur["max_el"], cur["peak_az"] = _altaz(sat, observer, t)
        elif e == 2 and "rise" in cur and "peak" in cur:  # set
            cur["set"] = t.utc_datetime()
            cur["set_az"] = _altaz(sat, observer, t)[1]
            if cur["max_el"] >= min_elev and cur["set"] > start_dt:
                out.append({
                    "rise": cur["rise"].isoformat(),
                    "rise_az": cur["rise_az"],
                    "peak": cur["peak"].isoformat(),
                    "max_el": cur["max_el"],
                    "peak_az": cur["peak_az"],
                    "set": cur["set"].isoformat(),
                    "set_az": cur["set_az"],
                    "duration_s": (cur["set"] - cur["rise"]).total_seconds(),
                })
            cur = {}
    return out


def compute_passes(sat, lat, lon, start_dt, hours=48, min_elev=10.0, lookback_min=30):
    """Passes over [start_dt, start_dt+hours] as seen from lat/lon. Events are
    found at the 0° horizon; only passes whose culmination reaches >= min_elev are
    returned (max_el filter). Times are ISO-8601 UTC strings.

    The search starts lookback_min before start_dt so a pass in progress whose
    rise lies in the recent past is captured whole (rise→peak→set) — LEO/MEO
    passes fit in 30 min. A LONG pass (HEO/Molniya, hours) whose rise predates
    that window would still be dropped, leaving the bird overhead yet the roster
    showing its NEXT rise; so if we are above min_elev at start_dt and no pass
    covers now, a targeted wide backsearch recovers the current pass and puts it
    first. Passes that already ended before start_dt are discarded, so the result
    is unchanged when nothing is overhead."""
    ts = _ts()
    observer = wgs84.latlon(lat, lon)
    t1 = ts.from_datetime(start_dt + datetime.timedelta(hours=hours))
    t0 = ts.from_datetime(start_dt - datetime.timedelta(minutes=lookback_min))
    times, events = sat.find_events(observer, t0, t1, altitude_degrees=0.0)
    passes = _passes_from_events(sat, observer, times, events, start_dt, min_elev)

    covered = passes and datetime.datetime.fromisoformat(passes[0]["rise"]) <= start_dt
    if not covered and _altaz(sat, observer, ts.from_datetime(start_dt))[0] >= min_elev:
        tw = ts.from_datetime(start_dt - datetime.timedelta(hours=_INPROGRESS_LOOKBACK_H))
        wtimes, wevents = sat.find_events(observer, tw, t1, altitude_degrees=0.0)
        for p in _passes_from_events(sat, observer, wtimes, wevents, start_dt, min_elev):
            rise = datetime.datetime.fromisoformat(p["rise"])
            set_ = datetime.datetime.fromisoformat(p["set"])
            if rise <= start_dt <= set_:          # the pass covering start_dt
                passes.insert(0, p)
                break
    return passes


_C_KM_S = 299792.458


def range_rate_factor(position_km, velocity_km_s):
    """The dimensionless Doppler factor for a topocentric position/velocity
    pair: -range_rate/c, positive while approaching and negative while receding.

    The FACTOR is what travels, not a frequency. Multiply it by whatever carrier
    is armed and you have the shift in Hz, so one sample serves 145.8 and 437.8
    MHz alike and nothing downstream has to guess which downlink the operator
    picked — the guess is exactly what made /track's doppler_hz wrong by ~3x.

    Zero for a degenerate (zero-range) pair rather than dividing by it."""
    r, v = position_km, velocity_km_s
    rng = (r[0] ** 2 + r[1] ** 2 + r[2] ** 2) ** 0.5
    if not rng:
        return 0.0
    range_rate = (r[0] * v[0] + r[1] * v[1] + r[2] * v[2]) / rng   # + = receding
    return -range_rate / _C_KM_S


def compute_doppler_curve(sat, lat, lon, start_dt, end_dt, step_s=1):
    """[(t_offset_s, factor)] over [start_dt, end_dt] — the Doppler curve a
    capture is tracked against, sampled fine enough to interpolate.

    ONE vectorised propagation for the whole window, not one per sample. The
    tracker re-aims the NCO ten times a second and must never touch a propagator
    in that loop, so the entire curve is computed up front (~1200 samples for a
    20-minute worst case, ~12 ms on a laptop) and doppler.curve_at interpolates
    it from there.

    Offsets are SECONDS FROM start_dt rather than timestamps: a capture measures
    its own age off a monotonic clock, and handing it wall-clock times would make
    the correction jump if the station's clock stepped mid-pass — which on an
    offline box waiting for a GPS or RTC fix is not hypothetical.

    Shares range_rate_factor with compute_track rather than repeating the dot
    product, so the readout on the page and the shift applied to the dongle can
    never disagree about which way the bird is going."""
    ts = _ts()
    observer = wgs84.latlon(lat, lon)
    total = (end_dt - start_dt).total_seconds()
    n = max(2, int(total // step_s) + 1)
    offsets = [i * step_s for i in range(n)]
    times = ts.from_datetimes([start_dt + datetime.timedelta(seconds=o) for o in offsets])
    topo = (sat - observer).at(times)
    r, v = topo.position.km, topo.velocity.km_per_s      # both (3, n)
    return [(offsets[i], range_rate_factor((r[0][i], r[1][i], r[2][i]),
                                           (v[0][i], v[1][i], v[2][i])))
            for i in range(n)]


def compute_track(sat, lat, lon, start_dt, end_dt, step_s=10, downlink_hz=None):
    """Sample the sub-satellite ground track + observer az/el over
    [start_dt, end_dt] at step_s seconds.

    Every sample carries `factor`, the dimensionless Doppler factor (see
    range_rate_factor) — unconditionally, because it does not depend on any
    frequency. `doppler_hz` is that factor times downlink_hz when one is given,
    and is retained only for callers that predate the factor."""
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
        factor = range_rate_factor(topo.position.km, topo.velocity.km_per_s)
        pts.append({
            "t": (start_dt + datetime.timedelta(seconds=i * step_s)).isoformat(),
            "lat": sub.latitude.degrees,
            "lon": sub.longitude.degrees,
            "el": alt.degrees,
            "az": az.degrees,
            "factor": factor,
            "doppler_hz": (factor * downlink_hz) if downlink_hz else None,
        })
    return pts
