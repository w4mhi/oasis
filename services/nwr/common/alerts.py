"""The NWR alert store — received official alerts, kept apart from the
operator's own markers.

DELIBERATELY NOT aprs-warnings.json. That file holds markers the operator PLACED:
they carry tactical names, a broadcast flag, and a reconcile loop that beacons
them as APRS objects over RF. Putting received NWS alerts in there would let one
stray click retransmit an official warning under the operator's callsign, and it
would tangle SAME purge-time expiry with the APRS tombstone machinery, which has
entirely different semantics.

Writes go through common/atomic_json (a torn read is how satellites.json once
wiped itself) and are serialised on a module lock.
"""
import threading
import uuid

from common import atomic_json
from common import config_paths
from services.nwr.common import counties, event_map, same

MAX_RECORDS = 500            # hard cap; this is a JSON file on a 2 GB Pi
KEEP_EXPIRED_S = 7 * 24 * 3600   # expired alerts stay readable for a week

_lock = threading.Lock()


def identity(rec):
    """Dedupe key: originator, event, the area set, and the issue time.

    SAME transmits every message three times back to back. dsame3's docs claim
    multimon-ng will not decode the same alert in succession, but that is a
    property of a tool we do not control, so the key does the work.
    """
    return "|".join([rec.get("org", ""), rec.get("event", ""),
                     ",".join(sorted(rec.get("fips", []))),
                     rec.get("raw_jjjhhmm", "")])


def watch_match(fips_list, watch_fips):
    """True when an alert's areas intersect the operator's watch list.

    An EMPTY watch list matches everything, so an unconfigured box degrades
    safely rather than silently going deaf.

    A state-wide code (CCC == 000) matches any watched county in that state —
    "all of Washington" must reach someone watching King County.

    Watch entries may be written 5- or 6-digit; both are normalised.
    """
    if not watch_fips:
        return True
    watch = {(w[1:] if len(str(w)) == 6 else str(w)) for w in watch_fips}
    states = {w[:2] for w in watch}
    for f in fips_list or []:
        key = f[1:] if len(f) == 6 else f
        if key in watch:
            return True
        if key.endswith("000") and key[:2] in states:
            return True
    return False


def _area(fips6, table):
    """One area entry: always named if we can, plotted only when we honestly can.

    `why` records the reason a real alert has no pin, so the decode log can SAY
    it instead of the marker just not appearing.
    """
    d = counties.describe(fips6, table=table)
    if d:
        return {"fips": fips6, "name": d["name"], "state": d["state"],
                "lat": d["lat"], "lon": d["lon"], "why": None}
    key = fips6[1:] if len(fips6) == 6 else fips6
    why = "statewide" if key.endswith("000") else "no-coordinates"
    return {"fips": fips6, "name": same.county_name(fips6) or key,
            "state": same.state_name(fips6) or "", "lat": None, "lon": None,
            "why": why}


def build(parsed, repo_root, watch_fips, now):
    """A full alert record from a parsed header. Pure apart from reading the
    county table."""
    table = counties.load(repo_root)
    issued, expires, suspect = same.derive_times(parsed["jjjhhmm"],
                                                 parsed["purge"], now)
    return {
        "id":            uuid.uuid4().hex,
        "org":           parsed["org"],
        "org_name":      same.org_name(parsed["org"]),
        "event":         parsed["event"],
        "event_name":    same.event_name(parsed["event"]),
        "fips":          list(parsed["fips"]),
        "areas":         [_area(f, table) for f in parsed["fips"]],
        "station":       parsed["station"],
        "raw":           parsed["raw"],
        "raw_jjjhhmm":   parsed["jjjhhmm"],
        "raw_purge":     parsed["purge"],
        "issued":        issued,
        "expires":       expires,
        "clock_suspect": suspect,
        "type":          event_map.warning_type(parsed["event"]),
        "matched":       watch_match(parsed["fips"], watch_fips),
        "received":      int(now),
    }


def load(repo_root):
    """Every stored alert, newest first. [] when absent or unreadable."""
    data = atomic_json.read_json(config_paths.nwr_alerts_json(repo_root),
                                 default={"alerts": []})
    recs = data.get("alerts") if isinstance(data, dict) else None
    return recs if isinstance(recs, list) else []


def _save(repo_root, recs):
    atomic_json.write_json(config_paths.nwr_alerts_json(repo_root),
                           {"alerts": recs})


def active(records, now):
    """Unexpired alerts — what belongs on the map right now.

    A record whose clock is suspect is NEVER retired by its own derived expiry.
    Both OASIS Pis have booted weeks stale with every health check green; a
    bogus timestamp must not be allowed to quietly clear a live tornado warning.
    """
    out = []
    for r in records or []:
        if r.get("clock_suspect"):
            out.append(r)
            continue
        exp = r.get("expires")
        if exp is None or exp > now:
            out.append(r)
    return out


def prune(records, now):
    """Newest first, long-expired dropped, capped at MAX_RECORDS."""
    recs = sorted(records or [], key=lambda r: r.get("received", 0), reverse=True)
    kept = []
    for r in recs:
        exp = r.get("expires")
        if (not r.get("clock_suspect")) and exp is not None \
                and exp + KEEP_EXPIRED_S <= now:
            continue
        kept.append(r)
    return kept[:MAX_RECORDS]


def record(repo_root, parsed, watch_fips, now, data_root=None):
    """(added, record). Deduped on identity(); False means we already had it.

    A write failure is logged by the caller and must NEVER kill the decode
    loop — an unwritable file loses a record, not the watch.
    """
    rec = build(parsed, data_root or repo_root, watch_fips, now)
    key = identity(rec)
    with _lock:
        recs = load(repo_root)
        for existing in recs:
            if identity(existing) == key:
                return False, existing
        recs.append(rec)
        _save(repo_root, prune(recs, now))
    return True, rec
