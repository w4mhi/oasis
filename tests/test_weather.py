#!/usr/bin/env python3
"""Self-tests for displays/e-ink/weather.py (plain unittest, stdlib only)."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_EINK = os.path.join(os.path.dirname(_HERE), "displays", "e-ink")
if _EINK not in sys.path:
    sys.path.insert(0, _EINK)

import weather  # noqa: E402


class TestHelpers(unittest.TestCase):
    def test_cardinal(self):
        self.assertEqual(weather._cardinal(0), "N")
        self.assertEqual(weather._cardinal(90), "E")
        self.assertEqual(weather._cardinal(248), "WSW")
        self.assertEqual(weather._cardinal(None), "")
        self.assertEqual(weather._cardinal("x"), "")

    def test_icon_key(self):
        self.assertEqual(weather._icon_key(800, "01d"), "clear")
        self.assertEqual(weather._icon_key(800, "01n"), "moon")
        self.assertEqual(weather._icon_key(802, "03d"), "partly")
        self.assertEqual(weather._icon_key(802, "03n"), "partly_night")
        self.assertEqual(weather._icon_key(804, "04d"), "cloud")
        self.assertEqual(weather._icon_key(500, "10d"), "rain")
        self.assertEqual(weather._icon_key(311, "09d"), "drizzle")
        self.assertEqual(weather._icon_key(601, "13d"), "snow")
        self.assertEqual(weather._icon_key(741, "50d"), "fog")
        self.assertEqual(weather._icon_key(211, "11d"), "storm")

    def test_int(self):
        self.assertEqual(weather._int(72.4), 72)
        self.assertEqual(weather._int("8"), 8)
        self.assertIsNone(weather._int(None))
        self.assertIsNone(weather._int("nope"))


_CUR = {
    "weather": [{"id": 802, "main": "Clouds",
                 "description": "scattered clouds", "icon": "03d"}],
    "main": {"temp": 72.3, "feels_like": 74.1, "temp_min": 70,
             "temp_max": 75, "pressure": 1013, "humidity": 55},
    "wind": {"speed": 8.2, "deg": 248},
    "sys": {"sunrise": 1751371200, "sunset": 1751425200, "country": "US"},
    "name": "Redmond", "dt": 1751385600, "timezone": -25200,
}


class TestNormalizeCurrent(unittest.TestCase):
    def test_fmt_local(self):
        # 1751385600 UTC + (-25200s) -> local clock string.
        self.assertRegex(weather._fmt_local(1751385600, -25200), r"^\d\d:\d\d$")
        self.assertEqual(weather._fmt_local(None, 0), "")
        self.assertEqual(weather._fmt_local("bad", 0), "")

    def test_normalize_current(self):
        now = weather._normalize_current(_CUR, "imperial")
        self.assertEqual(now["temp"], 72)
        self.assertEqual(now["feels"], 74)
        self.assertEqual(now["humidity"], 55)
        self.assertEqual(now["pressure"], 1013)
        self.assertEqual(now["wind_mph"], 8)
        self.assertEqual(now["wind_dir"], "WSW")
        self.assertEqual(now["cond"], "partly")
        self.assertEqual(now["cond_text"], "Scattered Clouds")
        self.assertFalse(now["night"])
        self.assertRegex(now["sunrise"], r"^\d\d:\d\d$")
        self.assertIsNone(now["hi"])

    def test_normalize_current_empty(self):
        now = weather._normalize_current({}, "imperial")
        self.assertIsNone(now["temp"])
        self.assertEqual(now["wind_dir"], "")
        self.assertEqual(now["cond"], "clear")  # defaults to id 800 day


from datetime import date, datetime as _dt, timezone  # noqa: E402


def _fc_item(epoch, tmin, tmax, cid, icon, deg, pop):
    return {"dt": epoch, "main": {"temp": (tmin + tmax) / 2,
            "temp_min": tmin, "temp_max": tmax},
            "weather": [{"id": cid, "icon": icon}],
            "wind": {"deg": deg}, "pop": pop}


class TestAggregateForecast(unittest.TestCase):
    def _fc(self):
        tz = -25200
        # Day 0 = 2025-07-01 local; two steps. Day 1 = 07-02; Day 2 = 07-03.
        base = 1751385600  # 2025-07-01 12:00 local given tz -25200
        step = 3 * 3600
        items = [
            _fc_item(base, 60, 78, 802, "03d", 250, 0.1),         # 07-01 midday
            _fc_item(base + step, 58, 70, 500, "10d", 260, 0.4),  # 07-01 later
            _fc_item(base + 24 * 3600, 61, 80, 800, "01d", 200, 0.0),  # 07-02
            _fc_item(base + 48 * 3600, 55, 74, 601, "13d", 300, 0.6),  # 07-03
        ]
        return {"list": items, "city": {"name": "Redmond", "timezone": tz}}

    def test_aggregate(self):
        now_date = _dt.fromtimestamp(1751385600 - 25200, timezone.utc).date()
        days, today = weather._aggregate_forecast(self._fc(), 5, now_date)
        # today = the now_date group (07-01): hi 78, lo 58 across its two steps.
        self.assertEqual(today, (78, 58))
        # future days exclude today -> 07-02, 07-03.
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0]["hi"], 80)
        self.assertEqual(days[0]["lo"], 61)
        self.assertEqual(days[0]["cond"], "clear")
        self.assertEqual(days[0]["wind_dir"], "SSW")
        self.assertEqual(days[1]["cond"], "snow")
        self.assertEqual(days[1]["pop"], 60)

    def test_aggregate_respects_days_cap(self):
        now_date = _dt.fromtimestamp(1751385600 - 25200, timezone.utc).date()
        days, _ = weather._aggregate_forecast(self._fc(), 1, now_date)
        self.assertEqual(len(days), 1)

    def test_aggregate_empty(self):
        days, today = weather._aggregate_forecast({}, 5, date(2025, 7, 1))
        self.assertEqual(days, [])
        self.assertIsNone(today)


class TestNormalizeAlert(unittest.TestCase):
    def test_picks_most_severe(self):
        nws = {"features": [
            {"properties": {"event": "Flood Watch", "severity": "Moderate",
                            "ends": "2025-07-01T20:00:00-07:00", "headline": "h1"}},
            {"properties": {"event": "Tornado Warning", "severity": "Extreme",
                            "ends": "2025-07-01T15:15:00-07:00", "headline": "h2"}},
        ]}
        a = weather._normalize_alert(nws)
        self.assertEqual(a["event"], "Tornado Warning")
        self.assertEqual(a["severity"], "Extreme")
        self.assertEqual(a["until"], "15:15")

    def test_falls_back_to_expires(self):
        nws = {"features": [{"properties": {
            "event": "Heat Advisory", "severity": "Minor",
            "expires": "2025-07-01T21:00:00-07:00"}}]}
        self.assertEqual(weather._normalize_alert(nws)["until"], "21:00")

    def test_none_when_empty(self):
        self.assertIsNone(weather._normalize_alert({"features": []}))
        self.assertIsNone(weather._normalize_alert(None))


import json as _json  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402


class TestResolveAndCache(unittest.TestCase):
    def test_key_from_env(self):
        os.environ["OWM_TEST_KEY"] = "abc123"
        try:
            cfg = {"weather": {"owm_api_key_env": "OWM_TEST_KEY"}}
            self.assertEqual(weather._resolve_key(cfg, "."), "abc123")
        finally:
            del os.environ["OWM_TEST_KEY"]

    def test_key_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "weather-secrets.json"), "w") as fh:
                _json.dump({"owm_api_key": "filekey"}, fh)
            cfg = {"weather": {"owm_api_key_env": "OWM_ABSENT",
                               "owm_api_key_file": "weather-secrets.json"}}
            self.assertEqual(weather._resolve_key(cfg, d), "filekey")

    def test_key_missing(self):
        cfg = {"weather": {"owm_api_key_env": "OWM_ABSENT",
                           "owm_api_key_file": "nope.json"}}
        self.assertIsNone(weather._resolve_key(cfg, "."))

    def test_latlon(self):
        self.assertEqual(weather._latlon({"lat": "47.5", "lon": "-122.0"}),
                         (47.5, -122.0))
        self.assertEqual(weather._latlon({}), (None, None))

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "weather-cache.json")
            weather._save_cache(p, {"fetched_at": 123, "data": {"ok": True}})
            got = weather._load_cache(p)
            self.assertEqual(got["fetched_at"], 123)
            self.assertTrue(got["data"]["ok"])
        self.assertIsNone(weather._load_cache("/no/such/file.json"))


class TestWeatherStatus(unittest.TestCase):
    def _cfg(self, d):
        return {
            "_config_dir": d,
            "oasis": {"http_timeout_s": 5},
            "weather": {"enabled": True, "provider": "owm", "units": "imperial",
                        "owm_api_key_env": "OWM_TEST_KEY2",
                        "cache_s": 900, "cache_path": "weather-cache.json",
                        "forecast_days": 5, "mini_days": 3,
                        "alerts": {"enabled": True, "provider": "nws",
                                   "url": "https://api.weather.gov/alerts/active"}},
        }

    def _install_fetch(self, cur, fc, nws):
        def fake(url, timeout, headers=None):
            if "/weather" in url:
                return cur
            if "/forecast" in url:
                return fc
            if "alerts" in url:
                return nws
            return None
        weather._fetch_json = fake

    def setUp(self):
        self._real_fetch = weather._fetch_json

    def tearDown(self):
        weather._fetch_json = self._real_fetch

    def test_happy_path_and_cache_write(self):
        os.environ["OWM_TEST_KEY2"] = "k"
        try:
            with tempfile.TemporaryDirectory() as d:
                self._install_fetch(_CUR, TestAggregateForecast()._fc(),
                                    {"features": []})
                cfg = self._cfg(d)
                data = weather.weather_status(cfg, {"lat": "47.5", "lon": "-122.0"})
                self.assertTrue(data["ok"])
                self.assertIsNone(data["stale_s"])
                self.assertEqual(data["location"], "Redmond")
                self.assertEqual(data["now"]["temp"], 72)
                self.assertIsNotNone(data["now"]["hi"])   # filled from forecast
                self.assertTrue(os.path.exists(os.path.join(d, "weather-cache.json")))
        finally:
            del os.environ["OWM_TEST_KEY2"]

    def test_network_fail_serves_stale_cache(self):
        os.environ["OWM_TEST_KEY2"] = "k"
        try:
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "weather-cache.json")
                weather._save_cache(p, {"fetched_at": time.time() - 4000,
                                        "data": {"ok": True, "stale_s": None,
                                                 "location": "Old", "now": {"temp": 60},
                                                 "days": [], "alert": None}})
                weather._fetch_json = lambda *a, **k: None  # all fetches fail
                data = weather.weather_status(self._cfg(d),
                                              {"lat": "47.5", "lon": "-122.0"})
                self.assertTrue(data["ok"])
                self.assertGreater(data["stale_s"], 0)
                self.assertEqual(data["location"], "Old")
        finally:
            del os.environ["OWM_TEST_KEY2"]

    def test_missing_key(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            cfg["weather"]["owm_api_key_env"] = "OWM_DEFINITELY_ABSENT"
            data = weather.weather_status(cfg, {"lat": "47.5", "lon": "-122.0"})
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "no API key")

    def test_disabled_returns_off(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            cfg["weather"]["enabled"] = False
            called = {"n": 0}
            real = weather._fetch_json
            weather._fetch_json = lambda *a, **k: called.__setitem__("n", called["n"] + 1)
            try:
                data = weather.weather_status(cfg, {"lat": "47.5", "lon": "-122.0"})
            finally:
                weather._fetch_json = real
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "weather off")
            self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
