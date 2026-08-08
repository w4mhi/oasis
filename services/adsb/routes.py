"""
ADS-B route blueprint — same-origin proxies to the adsb-api recorder
(port 8086, installed by services/adsb/install.py).

**This layer is the API contract boundary** (docs/api-contract.md). The daemon on
127.0.0.1:8086 is an internal implementation detail: it ships as its own systemd
unit and is NOT restarted in lockstep with Flask, so normalising here means the
contract holds even against a daemon from an older bundle. It also puts the
envelope where a reader can see it (contract §10) instead of hiding it inside a
pass-through of opaque bytes.

Migrated: /api/adsb/{alerts,aircraft,recent,health}. /api/adsb/history still
passes the daemon's body through untouched and is listed in
tests/test_api_contract.py.
"""

import datetime
import json
import time

from flask import Blueprint, Response, jsonify, request

bp = Blueprint("adsb", __name__)

# Contract §4: a default limit is mandatory. The daemon's ring holds at most 200.
_ALERTS_DEFAULT_LIMIT = 50
_ALERTS_MAX_LIMIT = 200


def _iso(epoch):
    """Epoch seconds -> ISO-8601 UTC (contract §6). None for an unusable value."""
    try:
        return datetime.datetime.fromtimestamp(
            float(epoch), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def _clamp_limit(raw, default, maximum):
    """Contract §4: a nonsense `limit` must degrade to the default, never 500 and
    never quietly return everything."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return min(value, maximum)


def _adsb_json(path, timeout=10):
    """(payload, error_response) — fetch and PARSE the daemon's JSON.

    Unlike _adsb_proxy this reads the body, because a contract-bearing route has
    to reshape it. Exactly one of the two return slots is ever non-None.
    """
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:8086{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return None, (jsonify({
            "ok": False,
            "error": f"ADS-B API returned HTTP {e.code}.",
            "code": "ADSB_API_ERROR",
        }), 502)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return None, (jsonify({
            "ok": False,
            "error": f"ADS-B API unavailable ({reason}). Is the adsb-api service running?",
            "code": "ADSB_API_UNAVAILABLE",
        }), 503)
    except TimeoutError:
        return None, (jsonify({
            "ok": False, "error": "ADS-B API timed out.", "code": "ADSB_API_TIMEOUT",
        }), 503)
    try:
        return json.loads(raw), None
    except (ValueError, TypeError):
        # The daemon answered with something that isn't JSON — a 200 carrying an
        # unusable body is a failure, and must not be dressed up as success.
        return None, (jsonify({
            "ok": False,
            "error": "ADS-B API returned a malformed response.",
            "code": "ADSB_API_BAD_RESPONSE",
        }), 502)


def _adsb_proxy(path, timeout=10):
    import urllib.request, urllib.error
    url = f"http://127.0.0.1:8086{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return Response(resp.read(), status=200, content_type="application/json")
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"ADS-B API unavailable ({reason}). "
                                 "Is the adsb-api service running?"}), 503
    except TimeoutError:
        return jsonify({"ok": False, "error": "ADS-B API timed out."}), 503


@bp.route("/api/adsb/health")
def api_adsb_health():
    """Is ADS-B running, and is data flowing? Conforms to docs/api-contract.md.

    Contract §2 in its purest form. `ok` used to mean "the adsb-api daemon
    answered", so both consumers read `ok === false` as "ADS-B isn't installed" —
    the request-success flag carrying domain state, which makes "the call failed"
    and "the call worked and ADS-B is off" indistinguishable.

    Now `ok` is true whenever we managed to look, and the news lives in typed
    fields. A probe reporting "not running" has SUCCEEDED: ADS-B is an optional
    service, and 503 for a service the operator chose not to install would be
    telling the caller its own request was broken.

    `flowing` is deliberately tri-state. null means "we don't know" — dump1090
    writes stats.json a little after it starts — and false would claim no signal
    when we simply have not been told yet.
    """
    payload, error = _adsb_json("/health", timeout=3)
    detail = None
    if error:
        # error is (response, status) — recover its message for `detail` instead
        # of surfacing an unreachable optional service as a failed request.
        detail = str((error[0].get_json() or {}).get("error") or "ADS-B API unavailable.")
        payload = None
    elif payload.get("ok") is False:
        detail = str(payload.get("error") or "ADS-B API reported a failure.")
        payload = None

    # One exit, one literal dict: the envelope is visible at the return site (§10)
    # and the key list cannot drift between the up and down paths.
    return jsonify({
        "ok": True,
        "running": payload is not None,
        "flowing": payload.get("flowing") if payload and "flowing" in payload else None,
        "aircraft_count": payload.get("aircraft_count") if payload else None,
        "positioned": payload.get("positioned") if payload else None,
        "last_json_age_s": payload.get("last_json_age_s") if payload else None,
        "messages_per_min": payload.get("messages_per_min") if payload else None,
        "samples_per_sec": payload.get("samples_per_sec") if payload else None,
        "detail": detail,
    }), 200


# The map needs every aircraft it can see, so this bound exists to stop an
# unbounded response, not to trim normal operation — a busy station shows a few
# hundred. The assistant asks for a small `limit` explicitly.
_AIRCRAFT_DEFAULT_LIMIT = 500
_AIRCRAFT_MAX_LIMIT = 2000

# Fields the front-end renders. Listed explicitly so a record that is missing one
# still carries the key as null (§5) instead of the key vanishing — a model reads a
# missing key as a different world, and the UI's `a.alt_baro` would be undefined.
# NOTE: these keep dump1090's names. Renaming to altitude_ft / ground_speed_kt
# (§7) is a separate pass across the whole ADS-B surface.
_AIRCRAFT_FIELDS = ("flight", "lat", "lon", "alt_baro", "gs", "track",
                    "category", "squawk", "emergency", "baro_rate")

# History windows are a DB scan on a Pi; an unbounded one is an unbounded scan.
_RECENT_DEFAULT_HOURS = 24.0
_RECENT_MAX_HOURS = 24.0 * 30
# The map's list wants the WHOLE 24 h history — 500 silently dropped ~290 of 791
# aircraft on a real station. The bound exists to stop an unbounded response, not
# to trim the consumer that needs all of it; the assistant passes a small limit.
_RECENT_DEFAULT_LIMIT = 5000
_RECENT_MAX_LIMIT = 20000


def _aircraft_record(rec, last_seen_epoch, now):
    """One aircraft, in the single schema shared by live and history rows.

    Live (/aircraft) and history (/recent) describe the same thing and are merged
    into one list by the front-end, so they must not have different schemas — and
    a model must not have to learn two shapes for "an aircraft". Every key is
    always present, null when unknown (§5); SQL's '' for an unknown callsign is
    unknown, not an empty callsign.
    """
    out = {"hex": rec.get("hex")}
    for key in _AIRCRAFT_FIELDS:
        value = rec.get(key)
        if isinstance(value, str):
            value = value.strip() or None
        out[key] = value
    out["last_seen"] = _iso(last_seen_epoch) if last_seen_epoch is not None else None
    out["age_s"] = (int(now - last_seen_epoch)
                    if last_seen_epoch is not None else None)
    return out


@bp.route("/api/adsb/aircraft")
def api_adsb_aircraft():
    """Live aircraft, most recently heard first. Conforms to docs/api-contract.md.

    Resolves dump1090's `now` + per-aircraft `seen` into an absolute `last_seen`
    here rather than shipping the decoder's epoch clock and making every client
    subtract. That removes an epoch float from the wire (§6) and, more usefully,
    stops three separate pages depending on a clock they cannot verify.
    """
    payload, error = _adsb_json("/aircraft")
    if error:
        return error

    limit = _clamp_limit(request.args.get("limit"),
                         _AIRCRAFT_DEFAULT_LIMIT, _AIRCRAFT_MAX_LIMIT)
    raw = [a for a in (payload.get("aircraft") or [])
           if isinstance(a, dict) and a.get("hex")]

    try:
        decoder_now = float(payload.get("now"))
    except (TypeError, ValueError):
        decoder_now = time.time()

    def _seen(rec):
        try:
            return float(rec.get("seen"))
        except (TypeError, ValueError):
            return None

    # Most recently heard first; hex breaks ties so the order is TOTAL and two
    # identical polls can never come back differently (§4). Unknown age sorts last.
    ordered = sorted(
        raw, key=lambda r: (_seen(r) if _seen(r) is not None else float("inf"),
                            str(r.get("hex") or "")))

    aircraft = [
        _aircraft_record(rec,
                         (decoder_now - _seen(rec)) if _seen(rec) is not None else None,
                         decoder_now)
        for rec in ordered[:limit]
    ]

    return jsonify({
        "ok": True,
        "aircraft": aircraft,
        "total": len(raw),
        "truncated": len(raw) > len(aircraft),
        "limit": limit,
        "time": _iso(decoder_now),
    }), 200


@bp.route("/api/adsb/history")
def api_adsb_history_proxy():
    import urllib.parse
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    return _adsb_proxy(f"/history?{qs}")


@bp.route("/api/adsb/recent")
def api_adsb_recent():
    """Latest position per aircraft over the last `hours`. Contract-conforming.

    Same record schema as /api/adsb/aircraft: the front-end merges the two into a
    single aged list, and shipping two shapes for one concept meant two code paths
    client-side and two schemas for a model to learn.
    """
    try:
        hours = float(request.args.get("hours"))
        if hours <= 0:
            raise ValueError
    except (TypeError, ValueError):
        hours = _RECENT_DEFAULT_HOURS
    hours = min(hours, _RECENT_MAX_HOURS)
    limit = _clamp_limit(request.args.get("limit"),
                         _RECENT_DEFAULT_LIMIT, _RECENT_MAX_LIMIT)

    payload, error = _adsb_json(f"/recent?hours={hours}")
    if error:
        return error
    # The daemon reports an unreadable history DB as {"ok": false, …}; that must
    # surface as a failure, not as ok:true with an empty list.
    if payload.get("ok") is False:
        return jsonify({
            "ok": False,
            "error": str(payload.get("error") or "ADS-B history unavailable."),
            "code": "ADSB_HISTORY_UNAVAILABLE",
        }), 503

    raw = [r for r in (payload.get("aircraft") or [])
           if isinstance(r, dict) and r.get("hex")]
    now = time.time()

    def _ts(rec):
        try:
            return float(rec.get("ts"))
        except (TypeError, ValueError):
            return None

    ordered = sorted(
        raw, key=lambda r: (-(_ts(r) if _ts(r) is not None else float("-inf")),
                            str(r.get("hex") or "")))
    aircraft = [_aircraft_record(rec, _ts(rec), now) for rec in ordered[:limit]]

    return jsonify({
        "ok": True,
        "aircraft": aircraft,
        "total": len(raw),
        "truncated": len(raw) > len(aircraft),
        "limit": limit,
        "hours": hours,
    }), 200


@bp.route("/api/adsb/alerts")
def api_adsb_alerts():
    """Proximity/emergency alerts, newest first. Conforms to docs/api-contract.md.

    Ordering is newest-first and stable: the daemon's ring is oldest-last-appended,
    but insertion order is not an ordering (§4), so this sorts on the timestamp.
    Records the daemon couldn't fully populate still come back with every field
    present and `null` where unknown (§5) — a model reads a missing key as a
    different world, an explicit null as "not known".
    """
    payload, error = _adsb_json("/alerts")
    if error:
        return error

    limit = _clamp_limit(request.args.get("limit"),
                         _ALERTS_DEFAULT_LIMIT, _ALERTS_MAX_LIMIT)
    raw = payload.get("alerts") or []
    now = time.time()

    def _ts(rec):
        try:
            return float(rec.get("ts"))
        except (TypeError, ValueError, AttributeError):
            return float("-inf")           # undated records sort last, never crash

    ordered = sorted((r for r in raw if isinstance(r, dict)), key=_ts, reverse=True)
    alerts = []
    for rec in ordered[:limit]:
        ts = rec.get("ts")
        iso = _iso(ts)
        alerts.append({
            "time": iso,
            "age_s": int(now - float(ts)) if iso else None,
            "icao": rec.get("icao") or None,
            "kind": rec.get("kind") or None,
            "detail": rec.get("detail") or None,
        })

    return jsonify({
        "ok": True,
        "alerts": alerts,
        "total": len(raw),
        "truncated": len(raw) > len(alerts),
        "limit": limit,
    }), 200


