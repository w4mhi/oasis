#!/usr/bin/env python3
"""
Contract-only HTTP client for the OASIS server.

Everything the panel shows is read over localhost HTTP (default
127.0.0.1:8083) — the API is the only tie to OASIS. stdlib only (urllib), no
`requests`, no external deps, per offline-first. Every call is defensive: a
timeout, connection error, or bad status becomes a 'down'/empty result and
never raises to the caller, so the panel keeps rendering when OASIS is down.
"""

import json
import socket
import urllib.error
import urllib.request


def _get(url, timeout):
    """(status, body_bytes) or raise on transport error."""
    req = urllib.request.Request(url, headers={"User-Agent": "oasis-e-ink"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return getattr(resp, "status", resp.getcode()), resp.read()


def _get_json(base, path, timeout):
    """(reached, data). reached=True on HTTP 200; data=None if not JSON."""
    try:
        status, body = _get(base + path, timeout)
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        return False, None
    if status != 200:
        return False, None
    try:
        return True, json.loads(body)
    except ValueError:
        return True, None


def _timeout(cfg):
    return cfg.get("oasis", {}).get("http_timeout_s", 5)


def probe(cfg, name):
    """True if service `name` is up.

    'internet' services (NET) count as up on any 2xx/3xx from their probe URL;
    'oasis' services are up on HTTP 200 unless the JSON body says {ok:false}.
    """
    spec = cfg["services"][name]
    if spec.get("source") == "internet":
        try:
            status, _ = _get(spec["probe"], min(_timeout(cfg), 4))
            return 200 <= status < 400
        except (urllib.error.URLError, socket.timeout, OSError, ValueError):
            return False
    reached, data = _get_json(cfg["oasis"]["base_url"], spec["probe"], _timeout(cfg))
    if not reached:
        return False
    if isinstance(data, dict) and data.get("ok") is False:
        return False
    return True


def services_status(cfg, names=None):
    """{name: bool} for the configured service order (or a subset)."""
    order = names if names is not None else cfg["services"].get("order", [])
    return {n: probe(cfg, n) for n in order}


def _round(v):
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return None


def _gps_fix(data):
    """(up, label) from the /api/system 'gps' block.

    gps is None when gpsd is unreachable ('off'); otherwise a dict whose `mode`
    is 0 (no fix), 2 (2D) or 3 (3D). Up = an actual 2D/3D fix.
    """
    gps = data.get("gps") if isinstance(data, dict) else None
    if not isinstance(gps, dict):
        return False, "off"
    mode = gps.get("mode", 0)
    if mode == 3:
        return True, "3D"
    if mode == 2:
        return True, "2D"
    return False, "no fix"


def system_status(cfg):
    """{cpu, ram, temp, ip, gps_up, gps_fix} from /api/system.

    One fetch serves both the system row and the GPS dot. Values are None (and
    gps 'off') when unavailable. Field names match the OASIS /api/system
    contract: {ip, cpu_pct, cpu_temp_c, ram:{pct}, gps:{mode,...}}.
    """
    path = cfg.get("system", {}).get("source", "/api/system")
    reached, data = _get_json(cfg["oasis"]["base_url"], path, _timeout(cfg))
    if not reached or not isinstance(data, dict):
        return {"cpu": None, "ram": None, "temp": None, "ip": None,
                "gps_up": False, "gps_fix": "off"}
    ram = data.get("ram") if isinstance(data.get("ram"), dict) else {}
    gps_up, gps_fix = _gps_fix(data)
    return {
        "cpu": _round(data.get("cpu_pct")),
        "ram": _round(ram.get("pct")),
        "temp": _round(data.get("cpu_temp_c")),
        "ip": data.get("ip"),
        "gps_up": gps_up,
        "gps_fix": gps_fix,
    }


def _parse_iso(s):
    """Parse graywolf timestamps ('YYYY-MM-DD HH:MM:SS.fffffffff±HH:MM' — space
    separator, nanosecond precision) and plain ISO 8601."""
    if not s:
        return None
    import re
    from datetime import datetime
    text = str(s).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)   # nanoseconds -> microseconds
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def stations_status(cfg):
    """{ok, count, last} for the FEED dot + the screen-1 station card.

    `last` is the most-recently-heard station's full record (callsign, sym_table,
    sym_code, lat, lon, alt/alt_m, speed_mph, course, via, comment, last_heard).
    ok=False means the feed is offline/unreachable (mirrors the dashboard); an
    empty station list is live-but-idle (ok=True, count=0), NOT offline.
    """
    reached, data = _get_json(cfg["oasis"]["base_url"], "/api/aprs/stations", _timeout(cfg))
    if not reached or not isinstance(data, dict) or data.get("ok") is False:
        return {"ok": False, "count": 0, "last": None, "list": []}
    stations = data.get("stations") or []
    count = data.get("count", len(stations))
    # Sort most-recently-heard first; stations without a parseable time go last.
    have = [s for s in stations if _parse_iso(s.get("last_heard"))]
    have.sort(key=lambda s: _parse_iso(s.get("last_heard")), reverse=True)
    ordered = have + [s for s in stations if not _parse_iso(s.get("last_heard"))]
    return {"ok": True, "count": count,
            "last": ordered[0] if ordered else None, "list": ordered}
