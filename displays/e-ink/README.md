# OASIS e-ink monitor

A shack-side status monitor for the **Waveshare 2.7" e-Paper HAT** (264×176 B/W,
4 buttons) on a Raspberry Pi. It's a **sidecar** to [OASIS](../README.md): it
reads everything it displays from the running OASIS server over HTTP
(`127.0.0.1:8083`) and touches no OASIS internals. The HTTP API is the only
contract between them, so this project stays cleanly separate.

Unlike OASIS itself (offline-first), this monitor is a *shack* tool and may use
internet for two screens — weather and propagation — which degrade gracefully
to "last known / stale" when offline.

## Planned screens (4 buttons → 4 screens)

| Key | Screen | Source |
|-----|--------|--------|
| KEY1 | Stations — last-heard detail (long-press → list) | OASIS `/api/aprs/*` |
| KEY2 | Winlink — passive inbox, new-message marks | OASIS `/api/winlink/*` |
| KEY3 | Propagation — SFI/K-index, band conditions | internet |
| KEY4 | Weather — current + NWS alerts + 5-day forecast | OWM + NWS (internet) |

A common header carries the title, a services strip (**LIVE · API · FEED · GPS ·
NET**), system stats (**CPU · RAM · TEMP · IP**), and a **LOC/UTC** clock.

## Status

**Task 1 — boot splash + display abstraction + config scaffold + installer.** Done.

**Task 2 — HTTP client + common header.** Done. Splash holds for
`splash_seconds`, then the default screen renders behind the common header
(title + LOC · UTC clock · service dots LIVE·API·FEED·NET·GPS · system
CPU·RAM·TEMP·IP), refreshing every `refresh.tick_s`. Service dots use two cues:
filled dot + bold = up, hollow circle + normal = down. GPS shows its fix (3D /
2D / no fix / off) from `/api/system`.

**Task 3 — buttons + station card + list.** Done. **Tap** KEY1–4 selects a
screen's base view; **hold** selects its list/secondary view (event-driven loop,
presses respond immediately). Screen 1 base view is the cm4stack-style last-heard
station card: APRS symbol (fetched from `/map-assets/…` and thresholded to 1-bit)
· callsign · age · path (RF/IS tag) · position + Maidenhead grid · speed/course ·
altitude · comment. Screen 1 hold view is the heard-station list (callsign · age
· distance+bearing from home, newest first, `+N more` footer). Screens 2–3
(winlink, propagation) are next.

**Tasks 7–10 — weather screen (screen 4).** Done. Base view: current conditions
(icon, temp/feels, humidity, wind) + today hi/lo + mini 3-day forecast
strip. Hold view (long-press KEY4): full 5-day forecast table with daily icons,
hi/lo, wind direction, and PoP%. An NWS alert banner (severity-coloured, with
`until` time) appears at the top of both views when an active alert is present.
Stale/cached data shows a `+Nm/h/d` age tag. Fetches only when screen 4 is
active; no network calls on other screens.

graywolf `/api/aprs/stations` notes: `last_heard` is
`YYYY-MM-DD HH:MM:SS.fffffffff±HH:MM` (space separator, nanoseconds — parsed
specially); who-heard is the `path` list, while `via` is the transport (`rf` vs
internet).

## Running

Preview the current frame anywhere (no Pi, no driver needed) — writes `preview.png`:

```sh
pip install Pillow
python3 oasis-e-ink.py --simulate
```

On the Raspberry Pi with the Waveshare driver installed:

```sh
python3 oasis-e-ink.py            # init panel + show splash once
python3 oasis-e-ink.py --service  # splash, then hold (what systemd runs)
python3 oasis-e-ink.py --clear    # clear the panel
```

## Installing on the Pi

`install-e-ink.py` is idempotent and safe to re-run (mirrors OASIS's other
`enable-*`/`install-*` scripts): enables SPI, installs Python deps, checks for
the Waveshare driver, and installs + enables the `oasis-e-ink.service` unit.

```sh
python3 install-e-ink.py             # full install
python3 install-e-ink.py --dry-run   # preview SPI/config changes
python3 install-e-ink.py --deps-only # SPI + deps, skip the service
```

Exit codes: `0` done · `10` reboot required (SPI just enabled) · `1` error.

### Panel driver (Pi)

The installer handles SPI + `spidev`/GPIO/Pillow, and **fetches Waveshare's
`waveshare_epd` driver from GitHub** when it isn't already present — a shallow +
sparse clone of just the driver (~7 MB, not the full multi-hundred-MB repo),
copied into `displays/e-ink/waveshare_epd/`. Because the service runs
`python3 oasis-e-ink.py`, this folder is on `sys.path`, so it resolves.

The driver isn't committed (Waveshare's license) — it's fetched or vendored. For
a fully offline install, pass `--no-fetch` and drop it in by hand first:

```sh
git clone https://github.com/waveshareteam/e-Paper
cp -r e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd ./
python3 install-e-ink.py --no-fetch
```

Buttons are on GPIO 5 / 6 / 13 / 19.

**Driver model gotcha:** the board silkscreen version does **not** map to the
driver revision. Our panel is labelled "V2.1" but is actually a **V1** — it
only draws with the `epd2in7` driver, not `epd2in7_V2` (which inits without
error but leaves the glass blank). `config.json` → `display.model` is set to
`epd2in7` for this reason. If a panel is blank but the driver imports fine, try
the other revision before suspecting hardware.

## Station identity

Callsign/grid/lat/lon come from the shared [`../station.json`](../station.json)
(path set by `station.source` in `config.json`), so the panel tracks the same
identity as the rest of OASIS. The inline `station` block is the fallback when
that file is absent.

## Weather (screen 4)

**Base view** (tap KEY4): current conditions icon, temperature, feels-like,
humidity, wind speed + cardinal direction, today hi/lo, and a
mini 3-day strip (icon + hi/lo + PoP%).

**Hold view** (long-press KEY4): full 5-day forecast table — date, drawn icon,
hi/lo, wind direction, and precipitation probability per day.

**NWS alert banner**: when an active NWS alert is present for the station's
coordinates, a banner with the event name, severity, and `until` time appears at
the top of both views.

**Data sources**: OWM free 2.5 endpoints (`/weather`, `/forecast`) for current
conditions + forecast; NWS `api.weather.gov/alerts/active` (no key, US-only) for
the alert. Location comes from `station.json` (lat/lon).

**API key setup**: set the `OWM_API_KEY` environment variable, or create a
`weather-secrets.json` file alongside `config.json`:

```json
{"owm_api_key": "your-key-here"}
```

The key is never stored in `config.json`. OWM's free "Current weather + 5 day
forecast" tier is sufficient.

**Offline / stale behavior**: results are cached to `weather-cache.json`
(configurable via `weather.cache_path`). When offline, the last cached reading
renders with a stale-age tag (e.g. `+22m`). `weather.cache_s` controls the
freshness window (default 900 s). Fetching is skipped entirely when another
screen is active, so OWM/NWS calls never occur on screens 1–3.

## Configuration

Everything lives in [`config.json`](config.json): OASIS base URL, display model
and orientation, refresh cadences, clock, service probes, weather/propagation
sources, and the Winlink mailbox. A future web config page will edit this file.
