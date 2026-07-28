"""Satellite roster persisted to configuration/satellites.json. The list is
built by build-roster.py (SatNOGS + CelesTrak aggregation); this module only
loads/saves it and persists per-satellite selection. Labels/freqs are not in
TLEs, so they live in the records build-roster.py writes."""
import datetime
import json
import os
import tempfile


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _empty():
    return {"updated": _now(), "source": None, "labels": {}, "satellites": []}


def save(path, data):
    # Atomic write: json.dump straight to the target truncates it first, so a
    # concurrent reader (gunicorn runs multiple threads; boot() fires several
    # /select calls at once) can catch a half-written file, fail to parse it, and
    # — via load()'s garbled path — persist an EMPTY roster over the real one.
    # Write to a UNIQUE temp file (mkstemp — a per-PID name collides across
    # threads and tears itself), fsync, then os.replace() so readers only ever see
    # a complete roster, old or new, never a torn one. Last writer wins (a benign
    # lost update on the `selected` flag); the roster itself is never truncated.
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".satellites-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path):
    """Return the roster. Missing → seed an empty envelope (build-roster.py fills
    it on the first online run). Present-but-garbled → return an empty view but
    DO NOT overwrite: never clobber a roster that may be mid-write or recoverable
    (with atomic save() a torn read is practically impossible; this is a guard)."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("satellites"), list):
                return data
        except (ValueError, OSError):
            pass
        return _empty()                     # garbled — read-only, leave the file alone
    data = _empty()
    save(path, data)                        # genuinely missing — seed it
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
