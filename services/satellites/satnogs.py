"""Aggregate SatNOGS DB (identity + transmitters) into OASIS satellite records.
PURE TRANSFORMS ONLY — no network here (build-roster.py does the fetching), so
every function is unit-testable offline."""
import math
import re

SAT_API = "https://db.satnogs.org/api/satellites/?format=json"
TX_API = "https://db.satnogs.org/api/transmitters/?format=json"

# RTL-SDR tunable range. SatNOGS lists S-band/Ku telemetry (up to ~15 GHz) an
# RTL-SDR can't receive; a transmitter is kept only if its downlink is in range.
RTL_MIN_HZ = 24_000_000
RTL_MAX_HZ = 1_766_000_000

# Closed tag vocabulary — records may only carry these tokens (stable order).
LABELS = ("WEATHER", "VOICE", "FM", "APRS", "SSTV", "LINEAR", "SSB", "DATA", "CREWED")

# A single mode TOKEN (upper-cased) -> labels it contributes. Mode strings are
# messy ("GMSK USP", "FSK AX.100 MODE 5"), so we match on split tokens.
_MODE_LABELS = {
    "APT": ("WEATHER",), "LRPT": ("WEATHER",),
    "FM": ("VOICE", "FM"), "FMN": ("VOICE", "FM"), "NFM": ("VOICE", "FM"),
    "SSTV": ("SSTV",),
    # SSB mode implies a linear/SSB channel; CW/USB/LSB do NOT (a CW telemetry
    # beacon is not a transponder). A linear transponder is caught by `type`.
    "SSB": ("LINEAR", "SSB"),
    "BPSK": ("DATA",), "GMSK": ("DATA",), "AFSK": ("DATA",),
    "FSK": ("DATA",), "GFSK": ("DATA",), "MSK": ("DATA",),
}

# description keyword (upper-cased) -> label. APRS/SSTV intent lives in the
# free-text description, not the `mode` field.
_DESC_LABELS = (("APRS", "APRS"), ("SSTV", "SSTV"))

# 137.0-138.0 MHz is the meteorological-satellite VHF downlink band (NOAA APT,
# METEOR LRPT, Direct Sounder Broadcast). SatNOGS labels these downlinks with
# assorted modes (APT/DSB/FSK/FM), so the frequency is the reliable WEATHER
# signal, not the mode string.
_WX_BAND_MHZ = (137.0, 138.0)

# The amateur-satellite voice/transponder bands (2 m, 70 cm, 23 cm). The
# "workable" labels below only mean an operator can actually USE the bird when
# the downlink sits here — the same frequency-not-mode principle as WEATHER. A
# `mode: FM`/`SSB` on a non-amateur service (SARSAT L-band 1544.5, weather DCP
# 1703, military/telemetry on 400 MHz, GEO HRIT on 1.6-1.7 GHz) is NOT amateur
# voice, so those must not earn VOICE/FM and surface as workable sats.
_AMATEUR_BANDS_MHZ = ((144.0, 148.0), (420.0, 450.0), (1240.0, 1300.0))
_WORKABLE_LABELS = frozenset(("VOICE", "FM", "SSTV", "LINEAR", "SSB"))


def _out_of_amateur_band(dl):
    """True ONLY when the downlink is KNOWN to be outside the amateur bands
    (a frequency is present and matches none). A missing frequency is unknowable,
    so it is not treated as out-of-band — we only strip a workable label when we
    can prove the transmitter is non-amateur (e.g. Arktika's 1544.5/1703 MHz)."""
    freq = (dl or {}).get("freq_mhz")
    if freq is None:
        return False
    return not any(lo <= freq <= hi for lo, hi in _AMATEUR_BANDS_MHZ)


def _mode_tokens(mode):
    return [tok for tok in re.split(r"[^A-Z0-9]+", (mode or "").upper()) if tok]


def _tx_labels(t):
    """Labels contributed by ONE transmitter (mode tokens + type + description)."""
    dl = t.get("downlink")
    # Labels inferred from the MODE STRING are the ambiguous ones: SatNOGS uses
    # "FM"/"SSB" for the modulation of non-amateur services too (SARSAT, weather
    # DCP, telemetry). They only mean a WORKABLE amateur channel in an amateur
    # band, so gate them on the downlink frequency (same principle as WEATHER).
    mode_tags = set()
    for tok in _mode_tokens(t.get("mode")):
        mode_tags.update(_MODE_LABELS.get(tok, ()))
    if _out_of_amateur_band(dl):
        mode_tags -= _WORKABLE_LABELS
    tags = mode_tags
    # Type / description / band signals are reliable and NOT gated: a SatNOGS
    # "Transponder" is an amateur linear transponder; APRS/SSTV intent lives in
    # the description; 137-138 MHz is the meteorological WEATHER band.
    desc = (t.get("description") or "").upper()
    for kw, lab in _DESC_LABELS:
        if kw in desc:
            tags.add(lab)
    if dl and dl.get("freq_mhz") is not None \
            and _WX_BAND_MHZ[0] <= dl["freq_mhz"] <= _WX_BAND_MHZ[1]:
        tags.add("WEATHER")
    if (t.get("type") or "") == "Transponder":
        tags.update(("LINEAR", "SSB"))
    # An AFSK/FSK packet channel described as APRS is APRS, not generic DATA.
    if "APRS" in tags:
        tags.discard("DATA")
    return tags


def labels_for(transmitters, norad):
    """Union the per-transmitter labels for a bird, returned in LABELS order
    (never insertion order) so tag filtering is stable."""
    tags = set()
    for t in transmitters:
        tags |= _tx_labels(t)
    if norad == 25544:
        tags.add("CREWED")
    return [lab for lab in LABELS if lab in tags]


def parse_satellites(raw):
    """SatNOGS /api/satellites/ list -> {norad: {name, sat_id, status}} for
    birds that are alive AND have a NORAD id."""
    out = {}
    for s in raw:
        norad = s.get("norad_cat_id")
        if norad is None or (s.get("status") or "").lower() != "alive":
            continue
        out[int(norad)] = {
            "name": s.get("name") or str(norad),
            "sat_id": s.get("sat_id"),
            "status": s.get("status"),
        }
    return out


def _mhz(hz):
    return round(hz / 1_000_000.0, 6) if hz else None


def _direction(low, high):
    """One up/down leg -> {freq_mhz, freq_high_mhz} or None when absent."""
    if not low:
        return None
    return {"freq_mhz": _mhz(low), "freq_high_mhz": _mhz(high) if high else None}


def parse_transmitters(raw):
    """SatNOGS /api/transmitters/ list -> {norad: [transmitter, ...]}. Kept iff
    ACTIVE and the downlink is within the RTL-SDR range (so we can receive it).
    Each keeps mode/type/description/invert/baud + downlink/uplink legs."""
    out = {}
    for t in raw:
        if (t.get("status") or "").lower() != "active":
            continue
        norad = t.get("norad_cat_id")
        low = t.get("downlink_low")
        if norad is None or not low or not (RTL_MIN_HZ <= low <= RTL_MAX_HZ):
            continue
        out.setdefault(int(norad), []).append({
            "mode": t.get("mode"),
            "type": t.get("type"),
            "description": t.get("description"),
            "invert": bool(t.get("invert")),
            "baud": t.get("baud"),
            "downlink": _direction(t.get("downlink_low"), t.get("downlink_high")),
            "uplink": _direction(t.get("uplink_low"), t.get("uplink_high")),
        })
    return out


# Orbit class straight from the TLE — line 2 carries eccentricity (cols 27-33,
# implied leading "0.") and mean motion (cols 53-63, rev/day), which give the
# semi-major axis and apogee/perigee. No SGP4/skyfield dependency — this module
# stays a pure transform. HEO = highly elliptical (Molniya-type dwell); GEO =
# ~stationary; otherwise LEO/MEO by apogee altitude.
_MU_KM3_S2 = 398600.4418      # Earth GM
_EARTH_R_KM = 6378.137


def orbit_class(tle_line2):
    """'LEO' | 'MEO' | 'HEO' | 'GEO' from a TLE line 2, or None if unparseable."""
    try:
        ecc = float("0." + tle_line2[26:33].strip())
        n_rev_day = float(tle_line2[52:63])
    except (ValueError, IndexError, TypeError):
        return None
    if n_rev_day <= 0:
        return None
    n_rad_s = n_rev_day * 2 * math.pi / 86400.0
    a = (_MU_KM3_S2 / (n_rad_s * n_rad_s)) ** (1 / 3)   # semi-major axis, km
    apogee = a * (1 + ecc) - _EARTH_R_KM
    perigee = a * (1 - ecc) - _EARTH_R_KM
    if ecc >= 0.25:
        return "HEO"
    if perigee >= 35000:
        return "GEO"
    if apogee < 2000:
        return "LEO"
    return "MEO"


def build_records(sats, txs, tle_index, prev_selected=None):
    """Intersect SatNOGS identity + active transmitters + CelesTrak TLE index
    into records. A bird is included iff it is alive (in `sats`), has >=1 active
    transmitter (in `txs`), AND has a TLE (in `tle_index`) — no TLE means no
    pass prediction, so the record would be useless. Returns (records, facet)."""
    prev_selected = prev_selected or {}
    records = []
    for norad in sorted(sats):
        transmitters = txs.get(norad)
        if not transmitters or norad not in tle_index:
            continue
        labels = labels_for(transmitters, norad)
        if not labels:
            # No recognized service after amateur-band gating — e.g. an L-band
            # weather/SAR bird or a defunct CW-beacon-only sat. Not actionable in
            # this FM / SSB / data / weather roster, so it is excluded (which also
            # keeps it out of the "in coverage" view even when it is overhead).
            continue
        orbit = orbit_class(tle_index[norad][2])   # tle_index[norad] = (name, l1, l2)
        if orbit == "GEO":
            # GEO birds don't pass (fixed in the sky), so the whole pass-based
            # feature — footprint, Doppler, LOS pills, pass alerts, listen-window
            # — is meaningless for them and leaves dead "no pass 24h" rows. The
            # GEO sats SatNOGS lists within RTL range are all non-amateur weather
            # telemetry (GOES / Elektro / FENGYUN). The one amateur GEO worth
            # working — QO-100 / Es'hail-2 — downlinks at 10.5 GHz and is already
            # excluded by the RTL-range gate above; supporting it would be a
            # dedicated fixed-target + LNB-downconversion feature, not a pass
            # roster entry. So exclude GEO here.
            continue
        meta = sats[norad]
        name = f"{meta['name']} [{orbit}]" if orbit else meta["name"]
        records.append({
            "name": name,
            "norad": norad,
            "sat_id": meta["sat_id"],
            "status": meta["status"],
            "labels": labels,
            "orbit": orbit,
            "transmitters": transmitters,
            "selected": bool(prev_selected.get(norad, False)),
        })
    counts = {}
    for r in records:
        for lab in r["labels"]:
            counts[lab] = counts.get(lab, 0) + 1
    facet = {lab: counts[lab] for lab in LABELS if lab in counts}
    return records, facet


def diff_rosters(old, new):
    """By-NORAD diff of two record lists for operator change-flagging.
    `changed` = same NORAD whose name/status/labels/transmitters differ."""
    o = {s["norad"]: s for s in old}
    n = {s["norad"]: s for s in new}
    changed = [norad for norad in sorted(set(o) & set(n))
               if any(o[norad].get(k) != n[norad].get(k)
                      for k in ("name", "status", "labels", "transmitters"))]
    return {"added": sorted(set(n) - set(o)),
            "removed": sorted(set(o) - set(n)),
            "changed": changed}
