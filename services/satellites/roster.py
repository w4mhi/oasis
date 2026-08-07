"""Satellite roster persisted to configuration/satellites.json. The list is
built by build-roster.py (SatNOGS + CelesTrak aggregation); this module only
loads/saves it and persists per-satellite selection. Labels/freqs are not in
TLEs, so they live in the records build-roster.py writes."""
import datetime
import json
import os
import tempfile
import threading

# Serializes the read-modify-write in set_selected/set_selected_many. Atomic
# save() prevents a TORN file; it does nothing about a LOST UPDATE — two threads
# both load the roster, each flips its own satellite, and the second write erases
# the first. The client fires a burst (reconcileSelection pushes every local-only
# pick on load; clearAll pushes every deselect), and gunicorn serves them on
# --threads 4, so selecting 20 birds landed 1-2 of them and the kiosk showed a
# fraction of what the operator had picked.
#
# A threading.Lock is the right tool *here specifically* because start-oasis.py
# pins gunicorn to --workers 1: one process, several threads. If this ever grows
# to multiple worker processes, this needs a file lock (fcntl.flock) instead —
# separate processes do not share a threading.Lock.
_write_lock = threading.RLock()


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
    """Set one satellite's monitored flag. Serialized against other writers."""
    return set_selected_many(path, {norad: selected})


def set_selected_many(path, selections):
    """Apply a whole `{norad: bool}` set in ONE load-modify-save.

    This is what the client should use for anything touching more than one bird.
    Sending N separate requests is not just N times slower — before the lock it
    silently dropped most of them, and even with the lock it leaves the roster
    observable in N intermediate states while a burst is in flight (the kiosk
    polls /api/satellites every 60 s and can sample the middle of one).

    JSON object keys are strings, so norads are coerced. Unknown norads are
    ignored rather than invented — the roster's membership is owned by
    build-roster.py, and a stale client must never be able to add rows to it.
    """
    wanted = {}
    for norad, value in (selections or {}).items():
        try:
            wanted[int(norad)] = bool(value)
        except (TypeError, ValueError):
            continue                      # unparseable key — skip, don't fail the batch
    with _write_lock:
        data = load(path)
        if wanted:
            for s in data["satellites"]:
                if s["norad"] in wanted:
                    s["selected"] = wanted[s["norad"]]
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
