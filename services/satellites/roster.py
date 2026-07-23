"""Satellite roster persisted to configuration/satellites.json. The list is
built by build-roster.py (SatNOGS + CelesTrak aggregation); this module only
loads/saves it and persists per-satellite selection. Labels/freqs are not in
TLEs, so they live in the records build-roster.py writes."""
import datetime
import json
import os


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def load(path):
    """Return the roster. When the file is missing or garbled, seed an empty
    envelope (build-roster.py populates it on the first online run)."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("satellites"), list):
                return data
        except (ValueError, OSError):
            pass
    data = {"updated": _now(), "source": None, "labels": {}, "satellites": []}
    save(path, data)
    return data


def set_selected(path, norad, selected):
    data = load(path)
    for s in data["satellites"]:
        if s["norad"] == norad:
            s["selected"] = bool(selected)
    data["updated"] = _now()
    save(path, data)
    return data


def _display_mode(t):
    """A meaningful mode label for a button: prefer a recognizable SERVICE named in
    the description (APRS) over the raw SatNOGS modulation (AFSK/FM). SatNOGS has no
    'APRS' mode — it's 1200-baud AFSK with 'APRS' only in the description — so the
    modulation alone reads as 'AFSK' where an operator expects 'APRS'."""
    if "APRS" in (t.get("description") or "").upper():
        return "APRS"
    return t.get("mode")


def legacy_downlinks(sat):
    """Phase-1 compat view: flatten the on-disk `transmitters` list into the old
    `downlinks` shape [{"mode","freq_mhz"}] that routes/listen/UI still read.
    Transmitters with no downlink leg are skipped. De-duplicated by (mode, freq):
    SatNOGS often lists several active transmitters at the same downlink freq+mode
    (e.g. ISS's FM voice), which would otherwise render as duplicate buttons.
    Phase 2 migrates consumers to `transmitters`/uplink and this helper goes away."""
    out, seen = [], set()
    for t in sat.get("transmitters", []):
        dl = t.get("downlink")
        if not (dl and dl.get("freq_mhz") is not None):
            continue
        mode = _display_mode(t)
        key = ((mode or "").strip().lower(), round(float(dl["freq_mhz"]), 6))
        if key in seen:
            continue
        seen.add(key)
        out.append({"mode": mode, "freq_mhz": dl["freq_mhz"]})
    return out
