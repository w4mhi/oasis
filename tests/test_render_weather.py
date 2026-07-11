#!/usr/bin/env python3
"""Render smoke tests for screen 4. Needs Pillow; skips if it isn't installed."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_EINK = os.path.join(os.path.dirname(_HERE), "displays", "e-ink")
if _EINK not in sys.path:
    sys.path.insert(0, _EINK)

try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

if _HAS_PIL:
    import render  # noqa: E402


@unittest.skipUnless(_HAS_PIL, "Pillow not installed")
class TestIconAndAge(unittest.TestCase):
    CONDS = ["clear", "moon", "partly", "partly_night", "cloud",
             "rain", "drizzle", "snow", "fog", "storm"]

    def test_every_icon_draws(self):
        for cond in self.CONDS:
            for size in (22, 40):
                img = Image.new("1", (64, 64), render.WHITE)
                draw = ImageDraw.Draw(img)
                render._weather_icon(draw, 4, 4, size, cond, night=False)
                self.assertEqual(img.mode, "1")

    def test_unknown_cond_does_not_raise(self):
        img = Image.new("1", (64, 64), render.WHITE)
        render._weather_icon(ImageDraw.Draw(img), 0, 0, 24, "???")

    def test_short_age(self):
        self.assertEqual(render._short_age(90), "1m")
        self.assertEqual(render._short_age(3600), "1h")
        self.assertEqual(render._short_age(90000), "1d")


def _cfg():
    return {
        "display": {"width": 264, "height": 176},
        "fonts": {"regular": None, "bold": None},
        "services": {"order": []},
        "system": {"show": []},
        "weather": {"mini_days": 3, "forecast_days": 5},
    }


def _wx(alert=None, stale=None):
    days = [{"dow": "Fri", "hi": 80, "lo": 62, "cond": "clear",
             "wind_dir": "W", "pop": 10},
            {"dow": "Sat", "hi": 74, "lo": 59, "cond": "rain",
             "wind_dir": "NW", "pop": 60},
            {"dow": "Sun", "hi": 77, "lo": 60, "cond": "storm",
             "wind_dir": "SW", "pop": 40},
            {"dow": "Mon", "hi": 81, "lo": 63, "cond": "partly",
             "wind_dir": "S", "pop": 5},
            {"dow": "Tue", "hi": 79, "lo": 61, "cond": "cloud",
             "wind_dir": "N", "pop": 20}]
    return {"ok": True, "stale_s": stale, "location": "Redmond",
            "now": {"temp": 72, "feels": 74, "humidity": 55, "pressure": 1013,
                    "wind_mph": 8, "wind_dir": "WSW", "cond": "partly",
                    "cond_text": "Partly Cloudy", "night": False,
                    "sunrise": "06:12", "sunset": "20:41", "hi": 78, "lo": 61},
            "days": days, "alert": alert}


def _ctx(view="base", wx=None):
    return {"screen": 4, "view": view, "title": "WEATHER",
            "clock": ("14:32", "21:32"), "services": {}, "gps_fix": "off",
            "system": {}, "weather": wx if wx is not None else _wx()}


@unittest.skipUnless(_HAS_PIL, "Pillow not installed")
class TestScreen4Base(unittest.TestCase):
    def test_base_renders(self):
        img = render.compose(_cfg(), _ctx("base"))
        self.assertEqual(img.size, (264, 176))
        self.assertEqual(img.mode, "1")

    def test_base_with_alert_and_stale(self):
        wx = _wx(alert={"event": "Tornado Warning", "severity": "Extreme",
                        "until": "15:15", "headline": "h"}, stale=1320)
        img = render.compose(_cfg(), _ctx("base", wx))
        self.assertEqual(img.size, (264, 176))

    def test_base_no_key(self):
        wx = {"ok": False, "error": "no API key", "stale_s": None,
              "now": None, "days": [], "alert": None}
        img = render.compose(_cfg(), _ctx("base", wx))
        self.assertEqual(img.size, (264, 176))

    def test_alert_banner_returns_offset(self):
        img = Image.new("1", (264, 176), render.WHITE)
        d = ImageDraw.Draw(img)
        self.assertEqual(render._alert_banner(d, None, 264, 55), 55)  # no alert: no shift
        y2 = render._alert_banner(d, {"event": "X", "severity": "Severe",
                                      "until": "12:00"}, 264, 55)
        self.assertGreater(y2, 55)


@unittest.skipUnless(_HAS_PIL, "Pillow not installed")
class TestScreen4List(unittest.TestCase):
    def test_list_renders(self):
        img = render.compose(_cfg(), _ctx("list"))
        self.assertEqual(img.size, (264, 176))
        self.assertEqual(img.mode, "1")

    def test_list_with_alert(self):
        wx = _wx(alert={"event": "Winter Storm Warning", "severity": "Severe",
                        "until": "06:00", "headline": "h"})
        img = render.compose(_cfg(), _ctx("list", wx))
        self.assertEqual(img.size, (264, 176))

    def test_list_no_key(self):
        wx = {"ok": False, "error": "fetch failed", "stale_s": None,
              "now": None, "days": [], "alert": None}
        img = render.compose(_cfg(), _ctx("list", wx))
        self.assertEqual(img.size, (264, 176))

    def test_list_forecast_days_zero_no_crash(self):
        cfg = _cfg()
        cfg["weather"]["forecast_days"] = 0
        img = render.compose(cfg, _ctx("list"))
        self.assertEqual(img.size, (264, 176))


import importlib.util  # noqa: E402


def _load_app():
    path = os.path.join(_EINK, "oasis-e-ink.py")
    spec = importlib.util.spec_from_file_location("oasis_eink_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBuildCtxWeather(unittest.TestCase):
    def setUp(self):
        import weather as _w
        self._orig_fetch = _w._fetch_json
        _w._fetch_json = lambda *a, **k: None

    def tearDown(self):
        import weather as _w
        _w._fetch_json = self._orig_fetch

    def test_weather_only_on_screen_4(self):
        app = _load_app()
        cfg = app.load_config(os.path.join(_EINK, "config.json"))
        # Screen 1: no weather fetch.
        ctx1 = app.build_ctx(cfg, 1, "base")
        self.assertIsNone(ctx1.get("weather"))
        # Screen 4: weather key present (dict), even with no key/network -> ok False.
        ctx4 = app.build_ctx(cfg, 4, "base")
        self.assertIsInstance(ctx4.get("weather"), dict)
        self.assertIn("ok", ctx4["weather"])


if __name__ == "__main__":
    unittest.main()
