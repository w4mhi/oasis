"""Aggregate SatNOGS DB (identity + transmitters) into OASIS satellite records.
PURE TRANSFORMS ONLY — no network here (build-roster.py does the fetching), so
every function is unit-testable offline."""
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


def _mode_tokens(mode):
    return [tok for tok in re.split(r"[^A-Z0-9]+", (mode or "").upper()) if tok]


def _tx_labels(t):
    """Labels contributed by ONE transmitter (mode tokens + type + description)."""
    tags = set()
    for tok in _mode_tokens(t.get("mode")):
        tags.update(_MODE_LABELS.get(tok, ()))
    desc = (t.get("description") or "").upper()
    for kw, lab in _DESC_LABELS:
        if kw in desc:
            tags.add(lab)
    dl = t.get("downlink")
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
        meta = sats[norad]
        records.append({
            "name": meta["name"],
            "norad": norad,
            "sat_id": meta["sat_id"],
            "status": meta["status"],
            "labels": labels_for(transmitters, norad),
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
