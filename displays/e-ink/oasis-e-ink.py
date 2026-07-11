#!/usr/bin/env python3
"""
OASIS e-ink monitor — entry point.

A shack-side status monitor for the Waveshare 2.7" e-Paper HAT (264x176 B/W,
4 buttons). It is a *sidecar*: it reads everything it shows from the running
OASIS server over HTTP (127.0.0.1:8083) and owns no OASIS internals — the API
is the only contract between the two.

Flow: on start, show the boot splash for `splash_seconds` (init proof), then
render the default screen behind the common header and refresh on the tick.
Buttons / the other screens land in later tasks.

Run on the Pi (with the waveshare_epd driver present):
    python3 oasis-e-ink.py              # splash, then one screen render, exit
    python3 oasis-e-ink.py --service    # splash, then live loop (systemd runs this)

Run anywhere for a PNG preview (no hardware / driver needed):
    python3 oasis-e-ink.py --simulate            # boot splash
    python3 oasis-e-ink.py --simulate --screen 1 # a live screen with the header
"""

import os
import sys
import time
import json
import signal
import argparse
import datetime

VERSION = "0.1.0"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")

SCREEN_TITLES = {1: "STATIONS", 2: "WINLINK", 3: "PROPAGATION", 4: "WEATHER"}


def load_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["_config_dir"] = os.path.dirname(os.path.abspath(path))
    resolve_station(cfg, path)
    return cfg


def resolve_station(cfg, config_path):
    """Load the panel's identity (callsign/grid/lat/lon) from the shared OASIS
    station.json — the single source of truth, no inline fallback.

    The file path comes from station.source (default ../../station.json, i.e. the
    repo root — this panel lives two levels down at displays/e-ink/). If the file
    is absent, station fields stay unset (the panel just shows no callsign)
    rather than falling back to stale config values.
    """
    st = cfg.setdefault("station", {})
    src = st.get("source", "../../station.json")
    base = os.path.dirname(os.path.abspath(config_path))
    path = src if os.path.isabs(src) else os.path.normpath(os.path.join(base, src))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return cfg
    for key in ("callsign", "grid", "lat", "lon"):
        if data.get(key):
            st[key] = data[key]
    st["_source_path"] = path
    return cfg


def build_clock(cfg):
    """(local, utc) 'HH:MM' strings at minute resolution.

    Local uses clock.local_tz (an IANA zone like 'America/Los_Angeles'); when
    that's blank it falls back to the Pi's own system timezone.
    """
    utc = datetime.datetime.now(datetime.timezone.utc)
    fmt = "%H:%M" if cfg.get("clock", {}).get("hour24", True) else "%I:%M"
    tzname = cfg.get("clock", {}).get("local_tz")
    loc = None
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            loc = utc.astimezone(ZoneInfo(tzname))
        except Exception:
            loc = None
    if loc is None:
        loc = datetime.datetime.now()
    return loc.strftime(fmt), utc.strftime(fmt)


def build_ctx(cfg, screen, view="base"):
    """Gather everything the header + current screen/view need for one frame.

    All reads are defensive (see oasis_client) — OASIS being down yields down
    dots / em-dash values, never an exception.
    """
    import oasis_client as api

    # Generic probes for the plain up/down services; FEED and GPS are derived.
    services = api.services_status(cfg, ["LIVE", "API", "NET"])
    stations = api.stations_status(cfg)
    system = api.system_status(cfg)
    services["FEED"] = stations["ok"]           # mirrors the actual feed state
    services["GPS"] = system.get("gps_up", False)  # up only on a real 2D/3D fix

    # Pre-fetch the last-heard station's symbol for the detail card (base view
    # only — the list view is text, so no icon fetch needed). Keeps render
    # HTTP-free.
    icon = None
    last = stations.get("last")
    if view == "base" and last and (last.get("sym_code") or last.get("sym_table")):
        try:
            import aprs_symbols
            icon = aprs_symbols.symbol_1bit(
                str(last.get("sym_table") or "/"),
                str(last.get("sym_code") or "-"),
                cfg["oasis"]["base_url"], size=40,
                timeout=cfg.get("oasis", {}).get("http_timeout_s", 5),
            )
        except Exception:
            icon = None

    # Weather is fetched only when its screen is active — the other screens
    # never touch the internet, and OWM/NWS calls stay off the tick otherwise.
    weather_data = None
    if screen == 4:
        try:
            import weather as _weather
            weather_data = _weather.weather_status(cfg, cfg.get("station", {}))
        except Exception:
            weather_data = None

    return {
        "screen": screen,
        "view": view,
        "title": SCREEN_TITLES.get(screen, ""),
        "clock": build_clock(cfg),
        "services": services,
        "gps_fix": system.get("gps_fix", "off"),
        "system": system,
        "stations": stations,
        "station_icon": icon,
        "weather": weather_data,
    }


def run_service(display, cfg):
    """Boot splash, then the live, button-driven screen loop until stopped.

    The loop blocks on an Event that fires on either a button press (redraw the
    newly-selected screen at once) or the refresh tick (redraw with fresh data).
    Buttons run in gpiozero's thread and only touch `state`/`wake` — never the
    panel — so there's no cross-thread display access.
    """
    import threading
    from render import render_splash, compose
    from buttons import Buttons

    stop = {"flag": False}
    wake = threading.Event()

    def _handle(*_):
        stop["flag"] = True
        wake.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    # 1) Init proof: hold the splash briefly.
    display.show(render_splash(cfg, version=VERSION))
    splash_s = cfg.get("splash_seconds", 4)
    print(f"[oasis-e-ink] splash up ({splash_s}s), then screen loop")
    _sleep_until(stop, splash_s)

    # 2) Buttons: short press = screen base view, long press = list/secondary.
    state = {"screen": cfg.get("default_screen", 1), "view": "base"}

    def on_key(n, long):
        state["screen"] = n
        state["view"] = "list" if long else "base"
        wake.set()

    buttons = Buttons(cfg, on_key)
    if buttons.available:
        print(f"[oasis-e-ink] buttons ready ({buttons.count} keys; tap=screen, hold=list)")
    else:
        print(f"[oasis-e-ink] buttons disabled: {buttons.error}")

    # 3) Live loop. Clear `wake` BEFORE rendering so a press arriving during the
    #    (slow) e-ink refresh is retained and handled on the next pass.
    tick = cfg.get("refresh", {}).get("tick_s", 60)
    try:
        while not stop["flag"]:
            wake.clear()
            display.show(compose(cfg, build_ctx(cfg, state["screen"], state["view"])))
            wake.wait(timeout=tick)
    finally:
        buttons.close()
        display.sleep()
    print("[oasis-e-ink] stopped.")


def _sleep_until(stop, seconds):
    """Sleep in 1s steps so SIGTERM is honored promptly."""
    for _ in range(int(max(1, seconds))):
        if stop["flag"]:
            return
        time.sleep(1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="OASIS e-ink monitor")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="path to config.json (default: alongside this script)")
    ap.add_argument("--simulate", action="store_true",
                    help="force the PNG simulator even if a panel driver exists")
    ap.add_argument("--service", action="store_true",
                    help="run as a long-lived service (splash, then screen loop)")
    ap.add_argument("--screen", type=int, metavar="N",
                    help="render screen N once (with the header) instead of the splash")
    ap.add_argument("--list", action="store_true",
                    help="with --screen, render the list/secondary view")
    ap.add_argument("--clear", action="store_true",
                    help="clear the panel and exit (no splash)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)

    from display import make_display
    from render import render_splash, compose

    display = make_display(cfg, force_simulate=args.simulate)
    print(f"[oasis-e-ink] backend: {display.name} "
          f"({display.width}x{display.height})")

    display.init()

    if args.clear:
        display.clear()
        display.sleep()
        print("[oasis-e-ink] cleared.")
        return 0

    if args.service:
        run_service(display, cfg)
        return 0

    if args.screen:
        view = "list" if args.list else "base"
        display.show(compose(cfg, build_ctx(cfg, args.screen, view)))
        print(f"[oasis-e-ink] rendered screen {args.screen} ({view})")
    else:
        display.show(render_splash(cfg, version=VERSION))
        print("[oasis-e-ink] splash displayed.")
    display.sleep()

    if display.name == "PNG simulator":
        print(f"[oasis-e-ink] wrote {cfg['display'].get('preview_path')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
