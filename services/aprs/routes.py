"""
APRS route blueprint — same-origin proxies to the graywolf-api (port 8085)
plus the operator-placed map warnings store. Extracted verbatim from
server/app.py in the blueprint split; URLs unchanged.
"""

import datetime
import json
import os
import threading
import time
import uuid

from flask import Blueprint, Response, jsonify, request

import appconfig
from common import config_paths
from common.web_guard import require_oasis_request
from services.aprs.common import warning_catalog
from services.aprs.common.graywolf_client import GraywolfClient
from services.aprs.common.warning_broadcast import (
    WarningBroadcaster,
    clean_send_path,
    tactical_name,
)

SUITE_ROOT = appconfig.SUITE_ROOT

bp = Blueprint("aprs", __name__)

# Operator-placed map warnings (flood/fire/etc.) shared across every device that
# views the APRS map. Small JSON list on disk; serialized writes via a lock.
# Runtime state, not repo content — gitignored.
WARNINGS_FILE = os.path.join(SUITE_ROOT, "aprs-warnings.json")
_warnings_lock = threading.Lock()

_TEST_BROADCASTER = None          # tests inject a fake here
_broadcaster_cache = None
_broadcaster_lock = threading.Lock()
_reconcile_lock = threading.Lock()
_last_reconcile = [0.0]
_RECONCILE_MIN_INTERVAL = 120     # seconds
_reconcile_active = [False]       # single-flight: a reconcile thread is running
_reconcile_dirty = [False]        # a kick arrived while one was already running


def _broadcaster_send_path():
    """Best-effort read of the configured default send_path from
    graywolf_api.json (`"both"` fallback, absent/unreadable file included)."""
    cfg_path = config_paths.graywolf_api_json(SUITE_ROOT)
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return "both"
    return cfg.get("send_path") or "both"


def _get_broadcaster():
    """Return a cached WarningBroadcaster, or None when unconfigured/unavailable."""
    if _TEST_BROADCASTER is not None:
        return _TEST_BROADCASTER
    global _broadcaster_cache
    with _broadcaster_lock:
        if _broadcaster_cache is not None:
            return _broadcaster_cache
        cfg_path = config_paths.graywolf_api_json(SUITE_ROOT)
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            return None
        base = cfg.get("base_url") or "http://127.0.0.1:8080"
        user = cfg.get("username")
        pw = cfg.get("password")
        if not user or not pw:
            return None
        call = warning_catalog.station_callsign(SUITE_ROOT)
        if not call:
            # APRS requires a real callsign; without one there's no valid
            # source_callsign to own beacons under, so broadcasting is
            # simply unavailable (no None-fallback broadcaster).
            return None
        send_path = cfg.get("send_path") or "both"
        client = GraywolfClient(base, user, pw)
        symbols = warning_catalog.load_symbol_map(SUITE_ROOT)
        source_callsign = f"{call}-1"
        _broadcaster_cache = WarningBroadcaster(client, symbols, send_path=send_path,
                                                 source_callsign=source_callsign)
        return _broadcaster_cache

@bp.route("/api/aprs/health")
def api_aprs_health_proxy():
    """Proxy health check for the graywolf-api (port 8085).
    Same-origin so the browser doesn’t need to make a cross-origin request."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:8085/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False, "graywolf_reachable": False,
                        "error": str(reason)}), 503



# ── Contract boundary (docs/api-contract.md) ─────────────────────────────────
# The graywolf-api daemon on :8085 is an internal implementation detail: it ships
# as its own systemd unit, is not restarted in lockstep with Flask, and emits
# GrayWolf's native shapes. Normalising here means the contract holds regardless
# of which daemon build is installed, and puts the envelope where it can be read.
_STATIONS_DEFAULT_LIMIT = 5000
_STATIONS_MAX_LIMIT = 20000

# Fields the three consuming pages render. Listed so a sparse record still
# carries every key as null (§5) rather than the key vanishing.
_STATION_FIELDS = ("lat", "lon", "sym_table", "sym_code", "via", "comment",
                   "speed_mph", "course", "alt_m", "is_object")


def _iso_utc(value):
    """Any GrayWolf timestamp -> ISO-8601 UTC 'Z' (contract §6), or None.

    GrayWolf emits Go's format: '2026-08-07 20:08:04.45893926-07:00' — space
    separated, nanosecond precision, local offset. That is why
    common/js/traffic-list.js's lastHeardEpoch() runs three string replaces plus a
    Date parse PER ROW, over ~1600 rows on every render. Doing it once here
    deletes that work from every consumer and gives one timestamp format across
    the whole API.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" ", "T")
    # Python's fromisoformat handles at most microseconds; GrayWolf sends nanos.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            else:
                break
        text = head + tail[len(digits):]        # drop sub-second entirely
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round_coord(value):
    """5 dp is ~1 m — far finer than APRS reports, and ~18 bytes/station smaller
    than the 17 significant digits GrayWolf sends. Over 1100 stations that is
    real weight on a response three pages fetch every 15 s."""
    try:
        return round(float(value), 5)
    except (TypeError, ValueError):
        return None


def _station_record(rec):
    out = {"callsign": rec.get("callsign")}
    for key in _STATION_FIELDS:
        out[key] = rec.get(key)
    out["lat"] = _round_coord(rec.get("lat"))
    out["lon"] = _round_coord(rec.get("lon"))
    path = rec.get("path")
    out["path"] = path if isinstance(path, list) else []
    out["last_heard"] = _iso_utc(rec.get("last_heard"))
    return out


def _clamp_limit(raw, default, maximum):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return default if value < 1 else min(value, maximum)


def _graywolf_json(path, timeout=10):
    """(payload, error_response) — fetch and PARSE the daemon's JSON."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8085{path}", timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return None, (jsonify({"ok": False, "code": "APRS_API_ERROR",
                               "error": f"APRS API returned HTTP {e.code}."}), 502)
    except urllib.error.URLError as e:
        return None, (jsonify({
            "ok": False, "code": "APRS_API_UNAVAILABLE",
            "error": f"APRS API unavailable ({getattr(e, 'reason', e)}). "
                     "Is the graywolf-api service running?"}), 503)
    except TimeoutError:
        return None, (jsonify({"ok": False, "code": "APRS_API_TIMEOUT",
                               "error": "APRS API timed out."}), 503)
    try:
        return json.loads(raw), None
    except (ValueError, TypeError):
        return None, (jsonify({"ok": False, "code": "APRS_API_BAD_RESPONSE",
                               "error": "APRS API returned a malformed response."}), 502)


@bp.route("/api/aprs/stations")
def api_aprs_stations():
    """Heard APRS stations, most recently heard first. Contract-conforming.

    The heaviest response OASIS serves — ~1100 stations, fetched every 15 s by
    index.html, maps/traffic/map.html and the kiosk dashboard.
    """
    payload, error = _graywolf_json("/api/aprs/stations")
    if error:
        return error
    if payload.get("ok") is False:
        return jsonify({
            "ok": False, "code": "APRS_STATIONS_UNAVAILABLE",
            "error": str(payload.get("error") or "APRS stations unavailable."),
        }), 503

    limit = _clamp_limit(request.args.get("limit"),
                         _STATIONS_DEFAULT_LIMIT, _STATIONS_MAX_LIMIT)
    raw = [s for s in (payload.get("stations") or [])
           if isinstance(s, dict) and s.get("callsign")]
    records = [_station_record(s) for s in raw]
    # Most recently heard first; callsign breaks ties so the order is TOTAL and
    # two identical polls cannot come back differently (§4). Undated sorts last.
    # Three stable passes, applied in reverse priority order: callsign ASCENDING
    # as the tie-break, then time DESCENDING, then undated last. A single
    # reverse=True sort would have flipped the callsign order too.
    records.sort(key=lambda s: str(s["callsign"]))
    records.sort(key=lambda s: s["last_heard"] or "", reverse=True)
    records.sort(key=lambda s: s["last_heard"] is None)
    shown = records[:limit]

    return jsonify({
        "ok": True,
        "stations": shown,
        "total": len(raw),
        "count": len(raw),          # legacy alias — three pages still read it
        "truncated": len(raw) > len(shown),
        "limit": limit,
    }), 200


@bp.route("/api/aprs/track")
def api_aprs_track_proxy():
    """Proxy station position history from the graywolf-api (port 8085).
    Forwards ?callsign= and ?minutes= query params verbatim."""
    import urllib.request
    import urllib.error
    import urllib.parse
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    url = f"http://127.0.0.1:8085/api/aprs/track?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"APRS API unavailable ({reason})."}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "APRS API timed out."}), 503


@bp.route("/api/aprs/system")
def api_aprs_system_proxy():
    """Proxy system stats (CPU/RAM/temp) from the graywolf-api (port 8085).
    The value is server-cached (5 s sampler) so this is a cheap, fast read."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:8085/api/system"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return Response(resp.read(), status=200,
                            content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"APRS API unavailable ({reason})."}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "APRS API timed out."}), 503


# ── Operator map warnings (shared, persisted) ─────────────────────────────────
# Flood/fire/etc. markers an operator drops on the APRS map. Stored server-side
# so every device viewing the map sees the same set. Small JSON list on disk,
# writes serialized through _warnings_lock. Owned by this server (not the APRS
# API), so warnings work even when the APRS chain is offline.
_WARN_TYPE_MAX = 64    # max length of a warning "type" id
_WARN_NOTE_MAX = 50    # note character cap (mirrors the UI maxlength)
_WARN_MAX      = 500   # hard cap on total stored warnings


def _load_warnings():
    """Return the warnings list from disk ([] if absent/unreadable)."""
    try:
        with open(WARNINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_warnings(warnings):
    """Write the list atomically (temp file + os.replace)."""
    tmp = WARNINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(warnings, fh)
    os.replace(tmp, WARNINGS_FILE)


def _clean_note(value):
    """Single-line, trimmed, length-capped note string."""
    return str(value or "").replace("\n", " ").replace("\r", " ").strip()[:_WARN_NOTE_MAX]


def _run_reconcile(b, warnings):
    """Run one reconcile pass against `warnings`, then write back the
    resulting `gw_beacon_id`s and drop confirmed-removed tombstones.

    The reconciler owns `gw_beacon_id`; this write-back never touches
    operator-intent fields (broadcast/pending_delete/note). Best-effort —
    never raises, so a hung/broken GrayWolf never surfaces here.
    """
    try:
        result = b.reconcile(warnings)
    except Exception:  # noqa: BLE001
        return
    try:
        removed = set(result.get("removed") or [])
        snap = {w["id"]: w for w in warnings}
        with _warnings_lock:
            disk = _load_warnings()
            kept = []
            for d in disk:
                if d.get("id") in removed:
                    continue
                s = snap.get(d.get("id"))
                if s is not None:
                    d["gw_beacon_id"] = s.get("gw_beacon_id")
                    d["gw_send_path"] = s.get("gw_send_path")   # reconciler-owned
                kept.append(d)
            _save_warnings(kept)
    except Exception:  # noqa: BLE001
        pass


def _kick_reconcile():
    """Single-flight, non-throttled reconcile trigger. Safe to call often
    (e.g. after every mutation) — if a reconcile is already running, this
    just marks it dirty so it loops again rather than piling up threads."""
    b = _get_broadcaster()
    if b is None:
        return
    with _reconcile_lock:
        if _reconcile_active[0]:
            _reconcile_dirty[0] = True
            return
        _reconcile_active[0] = True

    def _loop():
        try:
            while True:
                _run_reconcile(b, _load_warnings())
                with _reconcile_lock:
                    if _reconcile_dirty[0]:
                        _reconcile_dirty[0] = False
                        continue
                    _reconcile_active[0] = False
                    return
        except Exception:  # noqa: BLE001
            with _reconcile_lock:
                _reconcile_active[0] = False
    threading.Thread(target=_loop, daemon=True).start()


def _maybe_reconcile():
    """Throttled backstop reconcile for the GET path — even with zero
    broadcast warnings on disk, so an orphan object beacon (e.g. from a
    failed delete) still gets killed."""
    if _get_broadcaster() is None:
        return
    now = time.time()
    with _reconcile_lock:
        if now - _last_reconcile[0] < _RECONCILE_MIN_INTERVAL:
            return
        _last_reconcile[0] = now
    _kick_reconcile()


@bp.route("/api/aprs/warnings", methods=["GET"])
def api_aprs_warnings_list():
    _maybe_reconcile()
    return jsonify({"ok": True, "warnings": _load_warnings(),
                    "broadcast_available": _get_broadcaster() is not None})


@bp.route("/api/aprs/warnings", methods=["POST"])
@require_oasis_request
def api_aprs_warnings_add():
    body = request.get_json(silent=True) or {}
    try:
        lon = float(body.get("lon"))
        lat = float(body.get("lat"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lon/lat required (numeric)"}), 400
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return jsonify({"ok": False, "error": "lon/lat out of range"}), 400
    wtype = str(body.get("type") or "").strip()[:_WARN_TYPE_MAX]
    if not wtype:
        return jsonify({"ok": False, "error": "type required"}), 400
    note = _clean_note(body.get("note"))
    abbr_map = warning_catalog.load_abbr_map(SUITE_ROOT)
    call = warning_catalog.station_callsign(SUITE_ROOT)
    src = f"{call}-1" if call else None
    with _warnings_lock:
        warnings = _load_warnings()
        if len(warnings) >= _WARN_MAX:
            return jsonify({"ok": False, "error": "warning limit reached"}), 409
        existing_names = {w.get("aprs_name") for w in warnings}
        abbr = abbr_map.get(wtype) or wtype[:7].upper()
        aprs_name = tactical_name(abbr, existing_names)
        if not note:
            note = f"{aprs_name} inserted by {src}" if src else aprs_name
        send_path = clean_send_path(body.get("send_path"), _broadcaster_send_path())
        item = {
            "id":   uuid.uuid4().hex,
            "type": wtype,
            "lon":  lon,
            "lat":  lat,
            "note": note,
            "ts":   int(time.time()),
            "broadcast": bool(body.get("broadcast")),
            "gw_beacon_id": None,
            "aprs_name": aprs_name,
            "send_path": send_path,
        }
        warnings.append(item)
        _save_warnings(warnings)
    if item["broadcast"]:
        _kick_reconcile()
    return jsonify({"ok": True, "warning": item})


@bp.route("/api/aprs/warnings/<wid>", methods=["PATCH"])
@require_oasis_request
def api_aprs_warnings_update(wid):
    """Intent-only: record the note/broadcast toggle locally and return
    immediately. No GrayWolf calls happen while `_warnings_lock` is held —
    a broadcast-flag change kicks the reconcile driver, and a note edit on
    an already-on-air warning best-effort-pushes the new comment from a
    detached daemon thread, outside the lock."""
    body = request.get_json(silent=True) or {}
    has_note = "note" in body
    has_bcast = "broadcast" in body
    has_path = "send_path" in body
    note = _clean_note(body.get("note")) if has_note else None
    bcast_changed = False
    path_changed = False
    with _warnings_lock:
        warnings = _load_warnings()
        found = None
        for w in warnings:
            if w.get("id") == wid:
                found = w
                break
        if found is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        if has_note:
            found["note"] = note
        if has_path:
            # Destination change (IS/RF/both). The reconciler compares this to
            # the on-air send_path (gw_send_path) and re-advertises if it moved.
            new_path = clean_send_path(body.get("send_path"), _broadcaster_send_path())
            if new_path != found.get("send_path"):
                found["send_path"] = new_path
                path_changed = True
        if has_bcast:
            want = bool(body.get("broadcast"))
            if want != bool(found.get("broadcast")):
                # Toggling off does NOT clear gw_beacon_id here — leaving it
                # set is the signal the reconciler uses to kill the beacon.
                found["broadcast"] = want
                bcast_changed = True
        _save_warnings(warnings)
        result = dict(found)
    if bcast_changed or path_changed:
        _kick_reconcile()
    elif has_note and result.get("broadcast") and result.get("gw_beacon_id"):
        b = _get_broadcaster()
        if b is not None:
            gw_id = result["gw_beacon_id"]
            new_note = result["note"]

            def _push():
                try:
                    b.c.update_beacon(gw_id, {"comment": new_note})
                except Exception:  # noqa: BLE001
                    pass
            threading.Thread(target=_push, daemon=True).start()
    return jsonify({"ok": True, "warning": result})


@bp.route("/api/aprs/warnings/<wid>", methods=["DELETE"])
@require_oasis_request
def api_aprs_warnings_delete(wid):
    """Intent-only: a warning that is actually killable (has a beacon id,
    or is broadcast-intent with a broadcaster configured) is marked
    `pending_delete` and KEPT — the reconciler removes it from disk only
    once GrayWolf confirms the beacon is killed (delete succeeded, or
    nothing to delete). A local-only warning, or a broadcast warning on a
    GrayWolf-less box (nothing to kill), is removed immediately."""
    with _warnings_lock:
        warnings = _load_warnings()
        victim = next((w for w in warnings if w.get("id") == wid), None)
        if victim is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        killable = bool(victim.get("gw_beacon_id")) or (
            bool(victim.get("broadcast")) and _get_broadcaster() is not None)
        if killable or victim.get("pending_delete"):
            victim["pending_delete"] = True
            _save_warnings(warnings)
        else:
            kept = [w for w in warnings if w.get("id") != wid]
            _save_warnings(kept)
    _kick_reconcile()
    return jsonify({"ok": True})


