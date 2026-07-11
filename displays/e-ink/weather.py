#!/usr/bin/env python3
"""
Weather data layer for the OASIS e-ink monitor (screen 4).

Fetches current conditions + a 5-day forecast from OpenWeatherMap (free 2.5
endpoints) and active severe-weather alerts from the NWS (no key, US-only), then
normalizes everything into one flat dict for render.py. stdlib only (urllib);
every call is defensive -- a timeout or error becomes a stale/empty result and
never raises. Results are cached to disk so the screen degrades to a
"last-known / stale" reading when offline. See docs/eink-weather-screen.md.
"""

import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

_UA = "oasis-e-ink (https://github.com/w4mhi/oasis-emcomm)"

_CARD = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
         "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

_SEV_RANK = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1, "Unknown": 0}


def _int(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _cardinal(deg):
    try:
        return _CARD[int((float(deg) % 360) / 22.5 + 0.5) % 16]
    except (TypeError, ValueError):
        return ""


def _icon_key(owm_id, icon):
    """OWM condition id (+ d/n icon suffix) -> our drawn-icon key."""
    night = isinstance(icon, str) and icon.endswith("n")
    try:
        owm_id = int(owm_id)
    except (TypeError, ValueError):
        owm_id = 800
    if 200 <= owm_id < 300:
        return "storm"
    if 300 <= owm_id < 400:
        return "drizzle"
    if 500 <= owm_id < 600:
        return "rain"
    if 600 <= owm_id < 700:
        return "snow"
    if 700 <= owm_id < 800:
        return "fog"
    if owm_id == 800:
        return "moon" if night else "clear"
    if owm_id in (801, 802):
        return "partly_night" if night else "partly"
    return "cloud"


def _fmt_local(epoch, tz_offset_s):
    try:
        return datetime.fromtimestamp(int(epoch) + int(tz_offset_s), timezone.utc).strftime("%H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _normalize_current(cur, units):
    main = cur.get("main", {}) or {}
    wind = cur.get("wind", {}) or {}
    w0 = (cur.get("weather") or [{}])[0] or {}
    sysd = cur.get("sys", {}) or {}
    tz = cur.get("timezone", 0)
    icon = w0.get("icon", "")
    return {
        "temp": _int(main.get("temp")),
        "feels": _int(main.get("feels_like")),
        "humidity": _int(main.get("humidity")),
        "pressure": _int(main.get("pressure")),
        "wind_mph": _int(wind.get("speed")),
        "wind_dir": _cardinal(wind.get("deg")),
        "cond": _icon_key(w0.get("id", 800), icon),
        "cond_text": str(w0.get("description", "")).title(),
        "night": isinstance(icon, str) and icon.endswith("n"),
        "sunrise": _fmt_local(sysd.get("sunrise"), tz),
        "sunset": _fmt_local(sysd.get("sunset"), tz),
        "hi": None,
        "lo": None,
    }


def _aggregate_forecast(fc, days, now_date):
    """Group OWM 3-hourly forecast steps into per-day hi/lo + a midday condition.

    Returns (future_days, today_hilo): future_days is the list of day entries
    strictly after now_date (up to `days`); today_hilo is (hi, lo) for now_date.
    """
    tz = (fc.get("city") or {}).get("timezone", 0)
    groups = {}
    order = []
    for item in fc.get("list") or []:
        try:
            local = datetime.fromtimestamp(int(item["dt"]) + int(tz), timezone.utc)
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            continue
        d = local.date()
        g = groups.get(d)
        if g is None:
            g = {"dow": local.strftime("%a"), "hi": None, "lo": None,
                 "pop": 0.0, "cond": "cloud", "wind_dir": "", "noon_gap": 99}
            groups[d] = g
            order.append(d)
        main = item.get("main", {}) or {}
        for k in ("temp_max", "temp"):
            v = main.get(k)
            if v is not None:
                g["hi"] = v if g["hi"] is None else max(g["hi"], v)
                break
        for k in ("temp_min", "temp"):
            v = main.get(k)
            if v is not None:
                g["lo"] = v if g["lo"] is None else min(g["lo"], v)
                break
        g["pop"] = max(g["pop"], float(item.get("pop") or 0))
        gap = abs(local.hour - 12)
        if gap < g["noon_gap"]:
            g["noon_gap"] = gap
            w0 = (item.get("weather") or [{}])[0] or {}
            g["cond"] = _icon_key(w0.get("id", 800), w0.get("icon", ""))
            g["wind_dir"] = _cardinal((item.get("wind") or {}).get("deg"))

    future = []
    today_hilo = None
    for d in order:
        g = groups[d]
        entry = {"dow": g["dow"], "hi": _int(g["hi"]), "lo": _int(g["lo"]),
                 "cond": g["cond"], "wind_dir": g["wind_dir"],
                 "pop": int(round(g["pop"] * 100))}
        if d == now_date:
            today_hilo = (entry["hi"], entry["lo"])
        elif d > now_date:
            future.append(entry)
    return future[:days], today_hilo


def _fmt_iso_local(s):
    if not s:
        return ""
    try:
        return datetime.fromisoformat(str(s)).strftime("%H:%M")
    except ValueError:
        return ""


def _normalize_alert(nws):
    """Highest-severity active NWS alert -> {event, severity, until, headline}."""
    if not isinstance(nws, dict):
        return None
    best, best_rank = None, -1
    for f in nws.get("features") or []:
        p = (f or {}).get("properties", {}) or {}
        rank = _SEV_RANK.get(p.get("severity", "Unknown"), 0)
        if rank > best_rank:
            best, best_rank = p, rank
    if best is None:
        return None
    return {
        "event": best.get("event", "Alert"),
        "severity": best.get("severity", "Unknown"),
        "until": _fmt_iso_local(best.get("ends") or best.get("expires")),
        "headline": best.get("headline", ""),
    }


def _abs(base_dir, path):
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(base_dir, path))


def _timeout(cfg):
    return cfg.get("oasis", {}).get("http_timeout_s", 5)


def _resolve_key(cfg, base_dir):
    wcfg = cfg.get("weather", {})
    env = wcfg.get("owm_api_key_env", "OWM_API_KEY")
    if env and os.environ.get(env):
        return os.environ[env].strip() or None
    kf = wcfg.get("owm_api_key_file")
    if kf:
        try:
            with open(_abs(base_dir, kf), encoding="utf-8") as fh:
                return (json.load(fh).get("owm_api_key") or "").strip() or None
        except (OSError, ValueError):
            return None
    return None


def _latlon(station):
    try:
        return float(station.get("lat")), float(station.get("lon"))
    except (TypeError, ValueError, AttributeError):
        return None, None


def _load_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_cache(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
    except OSError:
        pass


def _fetch_json(url, timeout, headers=None):
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", resp.getcode())
            if status != 200:
                return None
            return json.loads(resp.read())
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        return None


_OWM_BASE = "https://api.openweathermap.org/data/2.5"


def _stale(cached, now):
    data = dict(cached.get("data") or {})
    data["stale_s"] = int(now - cached.get("fetched_at", 0))
    return data


def weather_status(cfg, station):
    """Normalized weather for render.py. Never raises; degrades to stale/empty."""
    wcfg = cfg.get("weather", {})
    if not wcfg.get("enabled", True):
        return {"ok": False, "stale_s": None, "error": "weather off",
                "location": "", "now": None, "days": [], "alert": None}
    base_dir = cfg.get("_config_dir", ".")
    cache_path = _abs(base_dir, wcfg.get("cache_path", "weather-cache.json"))
    cache_s = wcfg.get("cache_s", 900)
    units = wcfg.get("units", "imperial")
    now = time.time()

    cached = _load_cache(cache_path)
    if cached and (now - cached.get("fetched_at", 0)) < cache_s:
        data = dict(cached.get("data") or {})
        data["stale_s"] = None
        return data

    key = _resolve_key(cfg, base_dir)
    lat, lon = _latlon(station)
    if not key or lat is None:
        if cached:
            return _stale(cached, now)
        return {"ok": False, "stale_s": None,
                "error": "no API key" if not key else "no location",
                "location": "", "now": None, "days": [], "alert": None}

    q = f"?lat={lat}&lon={lon}&units={units}&appid={key}"
    cur = _fetch_json(f"{_OWM_BASE}/weather{q}", _timeout(cfg))
    fc = _fetch_json(f"{_OWM_BASE}/forecast{q}", _timeout(cfg))

    alert = None
    acfg = wcfg.get("alerts", {})
    if acfg.get("enabled", True):
        aurl = acfg.get("url", "https://api.weather.gov/alerts/active")
        nws = _fetch_json(f"{aurl}?point={lat},{lon}", _timeout(cfg),
                          headers={"Accept": "application/geo+json"})
        alert = _normalize_alert(nws)

    if not cur or not fc:
        if cached:
            data = _stale(cached, now)
            if alert:                     # a fresh alert still surfaces on stale data
                data["alert"] = alert
            return data
        return {"ok": False, "stale_s": None, "error": "fetch failed",
                "location": "", "now": None, "days": [], "alert": alert}

    tz = cur.get("timezone", 0)
    now_block = _normalize_current(cur, units)
    try:
        now_date = datetime.fromtimestamp(int(cur.get("dt", now)) + int(tz), timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        now_date = datetime.now(timezone.utc).date()
    days, today_hilo = _aggregate_forecast(fc, wcfg.get("forecast_days", 5), now_date)
    if today_hilo:
        now_block["hi"], now_block["lo"] = today_hilo

    data = {"ok": True, "stale_s": None,
            "location": wcfg.get("location_label") or cur.get("name", ""),
            "now": now_block, "days": days, "alert": alert}
    _save_cache(cache_path, {"fetched_at": now, "data": {**data, "stale_s": None}})
    return data
