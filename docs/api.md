# OASIS API Reference

Complete reference for every HTTP API in the OASIS system: the Flask
application server, its two standalone backend daemons (**graywolf-api** and
**adsb-api**), and the external services OASIS talks to or fronts (GrayWolf,
Pat/Winlink, Kiwix, OpenWebRX, WebSSH, gpsd).

> Offline-first: every endpoint here is served on the local device / LAN. There
> are **no** outbound API calls at runtime. The one operator-triggered exception
> is `POST /api/satellites/refresh`, which rebuilds the satellite list from
> SatNOGS + CelesTrak when the operator clicks the age pill and the Pi happens to
> have internet.

---

## 1. Architecture

```
                          Browser (dashboard, map, setup, …)
                                     │  HTTP (same-origin)
                                     ▼
                    ┌──────────────────────────────────┐
                    │   OASIS Flask app   :8083 (OASIS_PORT) │
                    │   server/app.py + blueprints        │
                    └───────┬───────────────┬────────────┘
        same-origin proxy   │               │  same-origin proxy
                            ▼               ▼
              graywolf-api :8085      adsb-api :8086
              (APRS station DB)       (dump1090 poller + history)
                    │                        │
                    ▼                        ▼
        /var/lib/graywolf/          /var/lib/adsb/
        graywolf-history.db         adsb-history.db
```

- **Flask front door** (`:8083`, override with `OASIS_PORT`) is the only port a
  browser talks to. Cross-origin calls are avoided by proxying the backend
  daemons through same-origin `/api/*` routes.
- **graywolf-api** (`:8085`) reads GrayWolf's APRS history DB and serves the
  station list / tracks. Fronted by `/api/aprs/*`.
- **adsb-api** (`:8086`) polls `dump1090-fa`'s `aircraft.json`, records history,
  evaluates alerts. Fronted by `/api/adsb/*`.
- **External apps** run their own web UIs on their own ports (GrayWolf `:8080`,
  Pat/Winlink `:8082`, Kiwix `:8081`, OpenWebRX `:8073`, WebSSH `:7681`). OASIS
  proxies Pat (`/api/winlink/*`) and links out to the rest.

### Conventions

| Aspect | Convention |
|---|---|
| Format | JSON in, JSON out (`Content-Type: application/json`). |
| Success envelope | Most endpoints return `{"ok": true, …}`. Some raw proxies pass the upstream body through verbatim. |
| Error envelope | `{"ok": false, "error": "<message>"}` with an appropriate HTTP status. |
| `supported: false` | Returned (HTTP 200) when a feature needs a platform capability the host lacks (e.g. systemd/ALSA on a Mac dev box) — a graceful degrade, not an error. |
| Timestamps | ISO-8601 or GrayWolf's `YYYY-MM-DD HH:MM:SS.fff±HH:MM`. ADS-B uses epoch seconds (`now`, `ts`). |
| Service discovery | HTML pages fetch `/server-ports.json` on load; **no port numbers are hardcoded** in front-end assets. |

### Authorization & CSRF

There is **no authentication layer** — OASIS trusts the local device / LAN
(the prime-directive threat model is an offline field deployment, not the open
internet). Two mechanisms protect state-changing calls:

1. **OS-side privilege.** Anything that runs `systemctl`/`apt`/network config
   goes through a **scoped `sudoers` NOPASSWD** grant installed by
   `scripts/enable-service-controls.py` (and siblings). No credential ever
   touches the HTTP layer; an un-granted host simply gets a failure with a hint
   to run the enabler.
2. **CSRF header.** Sensitive mutating endpoints require the custom request
   header **`X-OASIS-Request: 1`**, which a cross-origin page cannot set without
   a preflight these endpoints never grant. Missing/incorrect → `403 forbidden`.

**Every mutating `/api/*` endpoint requires it.** The guard lives in
`common/web_guard.py` as `@require_oasis_request` (and
`@require_oasis_request_for("DELETE")` for rules that serve an open `GET`
alongside a guarded mutation). `tests/test_csrf_guard.py` sweeps the source and
fails the build if a mutating route ships without one, so the list below cannot
silently drift again:

`POST /api/service` ·
`POST /api/wifi/connect` · `POST /api/wifi/forget` ·
`POST /api/aprs/frequency` ·
`POST /api/hardware/devices` · `/assign` · `/release` · `/burn-serial` ·
`/route` · `/lock` · `/stop-all` · `/service-stop` · `/guardian/cancel` · `/guardian/config` ·
`POST /api/setup/plan` · `/run` · `/cancel` · `/reboot` ·
`POST /api/winlink/connect` · `POST /api/winlink/disconnect` ·
`DELETE /api/winlink/mailbox/<box>/<mid>` ·
`POST /api/aprs/warnings` · `PATCH`/`DELETE /api/aprs/warnings/<wid>` ·
`POST /api/satellites/select` · `/refresh` · `/listen` · `/listen/stop` ·
`POST /api/forms/save` · `POST /api/save-ics205` · `POST /api/save-chirp`.

> The last three groups were **unguarded until 2.39.4**. Two of them were
> genuinely reachable cross-origin: the satellite routes parse with
> `get_json(force=True)`, so a `text/plain` POST is a *simple* request needing no
> preflight. A route that force-parses has none of the accidental protection a
> JSON content type provides — it needs the header most.

---

## 2. Port / service map

| Port | Service | Kind | Fronted by OASIS as |
|---|---|---|---|
| **8083** | OASIS Flask app (`OASIS_PORT`) | OASIS | — (the front door) |
| **8085** | graywolf-api | OASIS daemon | `/api/aprs/*` |
| **8086** | adsb-api | OASIS daemon | `/api/adsb/*` |
| 8080 | GrayWolf | External app | link-out + its DB feeds `:8085` |
| 8082 | Pat (Winlink) | External app | `/api/winlink/*` |
| 8081 | Kiwix | External app | link-out (`/api/health/zim` reports presence) |
| 8073 | OpenWebRX | External app | link-out |
| 7681 | WebSSH (ttyd) | External app | link-out |
| 2947 | gpsd | System daemon | read by `/api/system` |

Canonical machine-readable map: **`GET /server-ports.json`**.

---

## 3. Core / system APIs (Flask, `:8083`)

### Root & discovery

| Method | Path | Description |
|---|---|---|
| GET | `/` | Dashboard (`index.html`). |
| GET | `/station.json` | The operator's station identity (callsign, grid, lat/lon) from `configuration/station.json`. |
| GET | `/api/config` | Runtime config: listening `port` + feature flags. Reflects the actual bound port. |
| GET | `/server-ports.json` | Alias of `/api/config` — canonical service-discovery doc (`{port, ports:{flask, graywolf, kiwix, aprs_api, webssh, winlink, openwebrx}}`). |
| GET | `/api/server-info` | Which WSGI server is running (gunicorn vs Flask dev) + OASIS/package versions. |
| GET | `/api/installed-services` | Which features `setup-oasis.py` recorded installed (`{ok, features:[…]}`), or `{locked:true}` in portable mode. Drives which dashboard cards show. |

### System monitor

| Method | Path | Description |
|---|---|---|
| GET | `/api/system` | CPU %, RAM, disk, SoC temp, load, `uptime_s`, `boot_time` (ISO-8601 UTC) + GPS fix (gpsd), Wi-Fi SSID/clients, Pi throttling (`vcgencmd`), chrony clock offset. On the contract: every key always present; a **null** block means that subsystem is absent on this machine, a **dict** always carries its full key set. `503` + `code:"SYSTEM_METRICS_UNAVAILABLE"` if `psutil` is absent. |
| GET | `/api/audio` | ALSA sound cards with capture(RX)/playback(TX) capability — for choosing a GrayWolf/Winlink audio device. `supported:false` off-Linux. |

### Diagnostics

| Method | Path | Description |
|---|---|---|
| GET | `/api/diagnostics` | Runs the full diagnostics sweep (`common/diagnostics.run_all`) — station identity, hardware, feed freshness, power/thermal, data-age, service reachability — and returns the aggregated result. |

### Health probes (`/api/health/*`)

Cheap, targeted checks used by dashboard cards.

**All of these are on the contract.** `ok` means the PROBE RAN — it is never
`false` because the thing being probed is down. A stopped service, an absent
binary, an unconfigured Pat, an unreachable port: all are `ok:true` with the
finding in a typed field (`running`, `present`, `exists`, `reachable`). Only a
bad request is `ok:false`, and then with HTTP 4xx and a `code`. Key sets do not
change with the answer.

| Method | Path | Params | Description |
|---|---|---|---|
| GET | `/api/health/probe` | `service`, `port` | Server-side reachability check of a known local service (returns real HTTP status vs opaque no-cors). `{reachable, status, detail}`. |
| GET | `/api/health/service` | `name` | systemd status for a known unit (`graywolf`, `graywolf-api`, `pat`, `pat-direwolf`, `kiwix`, `webssh`, `aprs-sdr-feed`, `openwebrx`, `dump1090-fa`, `adsb-api`, `gpsd`, `oasis`). `{running, active, enabled, installed, supported}`; `supported:false` off-Linux, same keys. |
| GET | `/api/health/binary` | `name` | Whether a named system binary (e.g. `rtl_test`) is on `PATH`/standard dirs. `{present, path}`. |
| GET | `/api/health/feed-flow` | — | Whether UDP datagrams are actually flowing on the RTL-SDR feed port (passive `tcpdump` sample; needs scoped sudo). `{supported, probed, flowing, pps, reason, detail}`. `flowing` is **null** when nothing was measured — "we never listened" is not "the feed is dead". Reasons: `not-linux`, `tcpdump-missing`, `no-privilege`, `probe-error`. |
| GET | `/api/health/zim` | — | Offline Wikipedia/ZIM presence + count for the Kiwix card. |
| GET | `/api/health/maps` | — | Offline `.pmtiles` map count for the Offline Maps card. `{count, dir, cached}`. Recurses `maps/` server-side (depth 3) and caches the result, re-counting only when a directory in the tree changes mtime — so a map you just copied in shows up on the next call. Replaced a client-side walk that issued one `/api/browse` request **per directory, per dashboard, per pass**. |
| GET | `/api/health/rtc` | — | Hardware-RTC status from sysfs (presence, driver — e.g. Witty Pi DS3231). `{present, name, hctosys, drift_s}`. |
| GET | `/api/health/file` | `key` | Existence of a known OASIS config artifact (allowlisted keys only, no arbitrary paths). `{exists, callsign_set, password_set}` — booleans only, never the values; **null** means unreadable/not applicable. |

### Service control

| Method | Path | Auth | Body | Description |
|---|---|---|---|---|
| POST | `/api/service` | **CSRF** | `{unit, action}` | Start / stop / restart an allowlisted systemd unit via the scoped sudoers grant. `action ∈ {start, stop, restart}`. Boot-state-tracked units (`aprs-sdr-feed`, `dump1090-fa`) enable-on-start / disable-on-stop. **No hardware start-gate** — device contention is resolved client-side at start-click time. `supported:false` off-Linux. |

Allowlisted controllable units: `graywolf`, `graywolf-api`, `pat`,
`pat-direwolf`, `kiwix`, `webssh`, `aprs-sdr-feed`, `openwebrx`, `dump1090-fa`,
`adsb-api` (the web server `oasis` and `gpsd` are status-only).

### Hardware allocation (`/api/hardware/*`)

Device inventory + assignment engine (`common/hardware.py`). Assignment is
advisory bookkeeping; a service claims a device exclusively only when it starts.

| Method | Path | Auth | Body/Params | Description |
|---|---|---|---|---|
| GET | `/api/hardware/devices` | — | — | Live allocation: `{devices:[…], errors:[…], services:{svc:{device_id, running, assignee}}}`. Powers the setup Dongles card + the dashboard's read-only device labels. |
| POST | `/api/hardware/devices` | **CSRF** | `{kind, …}` | Declare a detected-but-undeclared device into `hardware.json`. |
| GET | `/api/hardware/detect` | — | — | Enumerate attached hardware (RTL-SDRs, ALSA cards, serial) as declare candidates. |
| POST | `/api/hardware/assign` | **CSRF** | `{service, device_id}` | Assign a device to a service. digirig/dra-pi are exclusive (refused with `holder` if already held); rtl-sdr is shared. |
| POST | `/api/hardware/release` | **CSRF** | `{service}` | Unassign a service's device; stops its unit(s) first if running. |
| POST | `/api/hardware/burn-serial` | **CSRF** | `{…}` | Burn a unique serial onto the sole connected, unclaimed RTL-SDR (`rtl_eeprom`), exclusive-access guarded. |

Assignable services: `adsb`, `openwebrx`, `aprs` (all rtl-sdr), `winlink`
(digirig / dra-pi). GrayWolf is intentionally absent (self-configured in its
own UI).

#### Service Operations console (`/api/hardware/console`, `/route`, …)

The device→service assignment matrix (the "mixer board") behind the dashboard's
HW/SRV console and the kiosk's SRV OPS card. Reroute is one decisive, silent
action; a per-device lock is the only guardrail. Both front-ends share the same
logic via `common/js/hw-console.js`.

| Method | Path | Auth | Body | Description |
|---|---|---|---|---|
| GET | `/api/hardware/console` | — | — | Live matrix state: `{services:[{id, name, kinds:[…]}], devices:[{id, label, kind, serial, assigned, running, locked}], warnings:[…]}`. Devices is empty where no hardware is present (hardware-less dev box). |
| POST | `/api/hardware/route` | **CSRF** | `{service, device_id}` | Exclusive reroute — assign `service` to `device_id` and start it, displacing whatever held the dongle. `409 {reason}` when the source or target is locked (the "unlock to move it" signal); `400` for an unknown/ineligible device. |
| POST | `/api/hardware/service-stop` | **CSRF** | `{service}` | Stop a service's unit(s) without changing its assignment — the matrix toggle-off (the dongle stays assigned, just idle). `409 source-locked` if the device is locked. |
| POST | `/api/hardware/lock` | **CSRF** | `{device_id, locked}` | Lock/unlock a device to its current assignment — protects it from any reroute (operator or auto-assign) until unlocked. |
| POST | `/api/hardware/stop-all` | **CSRF** | — | Emergency **STOP ALL** — plain-stop every controllable service **except Web SSH** (`_EMERGENCY_STOP` deliberately excludes it, so the operator always retains a way in). Returns `{stopped:[…]}`. |

#### Resource guardian (`/api/hardware/guardian*`)

An opt-in, server-side safety valve (`server/routes/hardware.py` runner thread,
`common/guardian.py` for the pure decision logic): when a monitored metric (SoC
temperature, CPU %, memory %) breaches its threshold — 80 °C / 95 % / 92 % by
default, tunable down to a safe floor (45 °C / 50 % / 60 %) via `/guardian/config`
— it arms a 30-second cancellable countdown and then runs the same
Web-SSH-excluding STOP ALL as above, even with no browser open. The dashboard
and kiosk poll it (~2 s) and show a countdown banner with **Cancel**.

| Method | Path | Auth | Body | Description |
|---|---|---|---|---|
| GET | `/api/hardware/guardian` | — | — | `{enabled, thresholds, mode: idle\|armed\|tripped, reason, seconds_left, stats}`. Cheap/cached — safe to poll for the banner. |
| POST | `/api/hardware/guardian/cancel` | **CSRF** | — | Operator override — cancel the countdown or clear a tripped state. |
| POST | `/api/hardware/guardian/config` | **CSRF** | `{enabled?, thresholds?}` | Enable/disable the guardian and tune thresholds (clamped to safe minimums). |

### Wi-Fi (`/api/wifi/*`, Linux + NetworkManager)

| Method | Path | Auth | Body | Description |
|---|---|---|---|---|
| GET | `/api/wifi/status` | — | — | Mode (`ap`/`client`/`none`), SSID, whether AP-fallback controls are wired. `{supported, mode, ssid, ap_ip, reason}` — one key set on every host. |
| GET | `/api/wifi/scan` | — | — | Networks in range. `{supported, scanned, networks, count, reason, detail}`. **`scanned` is the flag to branch on, not `ok`** — a host with no sudo grant returns `scanned:false, networks:[]`, which is not the same fact as "scanned and found nothing". |
| POST | `/api/wifi/connect` | **CSRF** | `{ssid, password}` | Join a WPA2 network (password 8–63 chars). An ACTION, so a refused join is `ok:false` — with **502** `WIFI_CONNECT_FAILED`, never HTTP 200. `503` `WIFI_NO_PRIVILEGE` / `WIFI_CONTROLS_UNAVAILABLE`; `400` `SSID_REQUIRED` / `INVALID_PASSWORD`. |
| POST | `/api/wifi/forget` | **CSRF** | — | Delete the current network's saved profile (radio falls back to the OASIS AP). Same status/code scheme; failure is `502` `WIFI_FORGET_FAILED`. |

> **Probes vs actions.** `status` and `scan` are probes: "could not scan" is an
> answer, so they are always `ok:true` with `supported`/`scanned` carrying it.
> `connect` and `forget` are actions: they either happened or they did not, so
> `ok:false` is right — what was wrong was serving it with HTTP 200, which made a
> refused join look like a successful call to anything checking the status first.

### Setup orchestrator (`/api/setup/*`)

Drives the web installer: plan → run → poll job → stream log.

| Method | Path | Auth | Params/Body | Description |
|---|---|---|---|---|
| GET | `/api/setup/permissions` | — | — | Which privileged grants are in place. |
| GET | `/api/setup/hardware-detect` | — | — | Hardware scan for the setup UI's device pickers. |
| POST | `/api/setup/plan` | **CSRF** | `{selectedFeatures, station, wifi, …}` | Validate + build an install plan; returns `{planId, …}` (or preflight blockers). |
| POST | `/api/setup/run` | **CSRF** | `{planId}` | Start the plan; returns `{jobId, status, startedAt}`. |
| GET | `/api/setup/jobs/<job_id>` | — | — | Job state snapshot. |
| GET | `/api/setup/jobs/<job_id>/log` | `cursor` | — | Incremental log tail (`{cursor, nextCursor, lines, eof}`). |
| POST | `/api/setup/cancel` | **CSRF** | `{jobId}` | Request cancellation of a running job. |
| POST | `/api/setup/reboot` | **CSRF** | — | Reboot the Pi (for features that need it). |

### Files / forms

| Method | Path | Auth | Params/Body | Description |
|---|---|---|---|---|
| GET | `/api/browse` | — | `path` | List a directory under the suite root (sandboxed; traversal rejected). |
| GET | `/api/list-chirp` | — | — | CHIRP CSVs in `static/chirp/`, newest first. |
| GET | `/api/list-ics205` | — | — | Saved ICS-205 plans in `static/ics-205/saved/`. |
| POST | `/api/save-chirp` | **CSRF** | `{filename, content}` | Save a CHIRP CSV into `static/chirp/`. |
| POST | `/api/save-ics205` | **CSRF** | `{filename, …}` | Save an ICS-205 plan JSON. Alias for `/api/forms/save` with `kind=ics-205`. |
| GET | `/api/forms/list` | — | `kind` | Saved snapshots for one form kind, newest first. Files themselves are fetched as static assets from `/static/<kind>/saved/<name>`. |
| POST | `/api/forms/save` | **CSRF** | `{kind, filename, content}` | Save a client form/net-log snapshot under `static/<kind>/saved/`, so a cleared browser cache or a swapped tablet doesn't lose it. `kind ∈ {ics-205, ics-213, ics-214, ics-309, net-log}` (whitelisted; traversal rejected). |

### APRS frequency

| Method | Path | Auth | Body | Description |
|---|---|---|---|---|
| GET | `/api/aprs/frequency` | — | — | Current APRS RX frequency + region presets. |
| POST | `/api/aprs/frequency` | **CSRF** | `{freq}` | Persist to `station.json` + hand off to the privileged applier (retunes the SDR feed). |

---

## 4. APRS APIs

The Flask `/api/aprs/*` routes are **same-origin proxies** to **graywolf-api**
(`:8085`), which reads GrayWolf's history DB. The map warnings routes are OASIS-owned (local JSON, shared across devices).

### Flask proxy layer (`:8083`)

| Method | Path | Params | Description |
|---|---|---|---|
| GET | `/api/aprs/stations` | — | Proxy of graywolf-api's station list (see §5). |
| GET | `/api/aprs/track` | `callsign`, `minutes` | Proxy of a station's position history. |
| GET | `/api/aprs/system` | — | Proxy of graywolf-api's cached system stats (CPU/RAM/temp). |
| GET | `/api/aprs/health` | — | graywolf-api reachability (`{ok, graywolf_reachable}`). |

### Map warnings — "Insert Alerts" (OASIS-owned, shared)

Operator-placed map markers (flood/fire/etc.), persisted to a shared JSON so
every device sees them.

| Method | Path | Auth | Body | Description |
|---|---|---|---|---|
| GET | `/api/aprs/warnings` | — | — | `{ok, warnings:[…], broadcast_available}`. |
| POST | `/api/aprs/warnings` | **CSRF** | `{lon, lat, type, note?}` | Add a warning (capped count → `409`). |
| PATCH | `/api/aprs/warnings/<wid>` | **CSRF** | `{…}` | Update a warning. |
| DELETE | `/api/aprs/warnings/<wid>` | **CSRF** | — | Remove a warning. |

Warning categories come from `maps/traffic/warnings.json`; their marker glyphs
come from the entry's `id` via `common/js/incident-icons.js`, not from the JSON.

---

## 5. GrayWolf & graywolf-api (`:8085`)

**GrayWolf** is an external APRS application (its own web UI on `:8080`) that
decodes APRS traffic (RF via `aprs-sdr-feed` → `rtl_fm` → UDP, and/or APRS-IS)
and writes observations to **`/var/lib/graywolf/graywolf-history.db`** (SQLite).
OASIS does not control GrayWolf's decoding — it only reads that DB.

**graywolf-api** (`services/aprs/common/aprs.py`, or the installer variant
`services/graywolf/enable-graywolf-api.py`) is the small OASIS daemon that reads
that DB and serves JSON on **`127.0.0.1:8085`**. It is the source behind every
`/api/aprs/*` proxy. It performs **no time-window filtering** — it returns every
station in the DB with its latest position.

| Method | Path (on `:8085`) | Params | Response |
|---|---|---|---|
| GET | `/api/aprs/stations` | — | `{ok, count, stations:[…]}` — see shape below. |
| GET | `/api/aprs/track` | `callsign` (req), `minutes` | `{ok, callsign, minutes, track:[{lat, lon, timestamp, speed_mph, course, alt_m}]}`. `minutes<=0` → full history. |
| GET | `/api/system` | — | CPU/RAM/temp (cached 5 s sampler). |
| GET | `/health` | — | `{ok, db, db_exists}`. |

**Station object** (`stations[]`):

```json
{
  "callsign": "K7ISQ-9",
  "is_object": false,
  "sym_table": "/",           // symbol table char ('/' primary, '\\' alt, or overlay)
  "sym_code": ">",            // symbol code char (e.g. ':' = wildfire/Fire object)
  "lat": 47.61, "lon": -122.33,
  "alt_m": 12.0,
  "speed_mph": 0,
  "course": null,             // degrees, or null when not moving/known
  "comment": "…",
  "last_heard": "2026-07-20 18:04:29.123-07:00",
  "via": "WIDE1-1",
  "path": ["WIDE1-1*", "WIDE2-1"]
}
```

> **Data store:** `/var/lib/graywolf/graywolf-history.db` — tables `stations`
> (keyed by `key`) and `positions` (`station_key` → `stations.key`, `id`
> autoincrement). Migrating between boxes = merge these two tables; positions
> link by the string `station_key`, so no numeric-id remapping is needed.

---

## 6. ADS-B APIs

Flask `/api/adsb/*` are same-origin proxies to **adsb-api** (`:8086`), the OASIS
recorder daemon (`services/adsb/common/adsb.py`) that polls `dump1090-fa`'s
`aircraft.json`, records to `/var/lib/adsb/adsb-history.db`, and evaluates
alerts against the station location.

### Flask proxy layer (`:8083`)

| Method | Path | Params | Description |
|---|---|---|---|
| GET | `/api/adsb/health` | — | Poller liveness (age of last JSON read). |
| GET | `/api/adsb/aircraft` | — | Current live snapshot. |
| GET | `/api/adsb/history` | `since`, `icao` | Recorded observations. |
| GET | `/api/adsb/recent` | `hours` (default 24) | Latest obs per aircraft over the window (feeds the list). |
| GET | `/api/adsb/alerts` | — | Recent alert events (ring buffer). |

### adsb-api daemon (`127.0.0.1:8086`)

| Method | Path | Params | Response |
|---|---|---|---|
| GET | `/health` | — | Poller liveness + `samples_per_sec` (the signal-health signal). |
| GET | `/aircraft` | — | `{now, aircraft:[{hex, flight, lat, lon, alt_baro, gs, track, squawk, category, seen, …}]}`. |
| GET | `/history` | `since` (epoch s), `icao` | `{observations:[…]}` (`503` if DB absent). |
| GET | `/recent` | `hours` (default 24) | `{now, aircraft:[…]}` — latest obs per aircraft since `now − hours`. |
| GET | `/alerts` | — | `{alerts:[{ts, icao, kind, detail, distance_km?}]}` — deduped ring buffer (max 200). |

**Alert kinds** (`services/adsb/common/alerts.py`): `squawk` (emergency
7500 / 7600 / 7700) and `proximity` (within `ADSB_ALERT_RADIUS_KM`, default 50).
No altitude alert.

**Env overrides:** `ADSB_DB_PATH` (`/var/lib/adsb/adsb-history.db`),
`ADSB_JSON_PATH` (`/run/dump1090-fa/aircraft.json`), `ADSB_API_PORT` (`8086`),
`ADSB_ALERT_RADIUS_KM` (`50`), `ADSB_POLL_SECS` (`1.0`).

> **Note:** `dump1090-fa` and `adsb-api` are separate units. Editing recorder
> code needs `systemctl restart adsb-api` (the Flask restart alone won't pick it
> up).

---

## 7. Satellites APIs (`/api/satellites/*`)

Pass prediction + tracks (Skyfield/sgp4), roster, and RTL-SDR pass recording.
Hardware-free routes are always available; listen routes need a dongle.

| Method | Path | Auth | Params/Body | Description |
|---|---|---|---|---|
| GET | `/api/satellites` | — | — | Roster with per-sat TLE lines + `{satellites, tle_age_days, station}`. |
| GET | `/api/satellites/passes` | — | `window` (h, default 48), `sat?` | Predicted passes `{passes:{norad:[{rise, peak, set, max_elev, …}]}}`, plus `min_elev` — the culmination floor these passes were computed under. Only passes culminating at or above it exist at all, so "no pass in 24 h" and "no pass above 25° in 24 h" are different statements and the response says which. Disk-cached (6 h TTL, keyed by TLE mtime **and** `min_elev`). |
| GET | `/api/satellites/track` | — | `sat`, `from`, `to` (ISO) | Ground track + `{track, l1, l2}`. |
| POST | `/api/satellites/select` | **CSRF** | `{norad, selected}` **or** `{selections:{norad:bool}}` | Set which satellites are monitored. The roster is the single source of truth — the kiosk reads its `selected` flag. **Use the bulk shape for more than one bird:** each request is a whole-roster read-modify-write, so fanning a set out into one request per satellite raced itself and lost most of them (20 picks landed 1–2). The single-toggle shape is retained for stale cached pages. |
| POST | `/api/satellites/refresh` | **CSRF** | — | **Online-only** rebuild of the satellite list from SatNOGS (freqs/modes) + CelesTrak (TLEs). Offline → `{ok:false, offline:true}` (HTTP 200, never fails). Returns `{ok, tle_age_days, count, labels, changes}`. |
| GET | `/api/satellites/listen/status` | — | — | Recorder state + dongle preconditions. |
| POST | `/api/satellites/listen` | **CSRF** | `{norad, freq_mhz?}` | Start recording a pass to WAV (pins `rtl_fm` to the assigned dongle by serial). Errors: `400` deps/downlink, `409` busy/already recording, `507` not enough disk space. |
| POST | `/api/satellites/listen/stop` | **CSRF** | — | Stop recording. |
| GET | `/api/satellites/listen/stream` | — | `norad`, `freq_mhz?` | Live pass audio as a chunked MP3 for a browser `<audio>` element. `GET` so a plain `<audio src=…>` works; holds the dongle for the connection and tears the pipeline down on disconnect. Mutually exclusive with recording. |
| GET | `/api/satellites/listen/recordings` | — | — | List recorded WAVs (`{recordings:[{name, bytes, mtime}]}`). |
| GET | `/api/satellites/listen/recording/<filename>` | — | — | Download a WAV. |

**Recording disk budget.** `configuration/sat-recordings/` is swept oldest-first
before each capture and after each stop — a pass costs ~5.8 MB/min at
48 kHz/16-bit mono. `SAT_RECORD_MAX_BYTES` (default 2 GB) caps the directory,
`SAT_RECORD_MIN_FREE_BYTES` (default 1 GB) is the free space required to start
(below it, `POST /listen` returns `507`), and `SAT_RECORD_MAX_AGE_S` (default
`0`, off) is an optional age sweep. The newest recording is never pruned. See
`docs/SETUP.md` for the rationale.

---

## 8. Speech APIs (`/api/speech/*`)

Station-wide text-to-speech, synthesised server-side with Piper and cached by
content hash under `features/speech/cache/`. Satellite pass alerts are the
first caller; server-side callers (guardian, Winlink) use `common/speech.py`
directly rather than round-tripping through HTTP. **`/say` is the one endpoint
in this document that does not return JSON** — it returns the audio itself.

| Method | Path | Params | Description |
|---|---|---|---|
| GET | `/api/speech/status` | — | What this station can say, and with what: `{ok, available, voice, model, sample_rate_hz, player, cache_entries, cache_bytes, cache_budget_bytes}`. Always `ok:true` — `available:false` (no Piper voice installed) is a normal, successful answer, not a failure. It's the signal a caller uses to decide whether to offer speech at all, the same posture as `supported:false` elsewhere in this document. |
| GET | `/api/speech/say` | `text` | Synthesise `text` and return it as **`audio/wav`** (not JSON), with `conditional=True` so a repeated phrase costs one `304`. Errors ARE JSON: `400 {ok:false, error, code}` for rejected text (`EMPTY_TEXT`, `TEXT_TOO_LONG` past 300 chars, `INVALID_TEXT` for control characters), `503 {ok:false, error, code:"SPEECH_UNAVAILABLE"}` when no engine/voice is installed — the engine's own stderr is never forwarded to the browser. |

No `POST` exists yet — nothing needs to trigger speech remotely, and adding one
would pull in the CSRF guard for no benefit.

---

## 9. Winlink APIs (`/api/winlink/*`)

Same-origin proxies to **Pat** (`:8082`). Two transports: RF (Direwolf, unit
`pat-direwolf`) and telnet (the Winlink Mail card).

| Method | Path | Auth | Params/Body | Description |
|---|---|---|---|---|
| GET | `/api/winlink/status` | — | — | Pat connection status. |
| GET | `/api/winlink/aliases` | — | — | Configured connect aliases (transports). |
| GET | `/api/winlink/mailbox/<box>` | — | — | List a mailbox (`in`/`out`/`sent`/`archive`). |
| GET | `/api/winlink/mailbox/<box>/<mid>` | — | — | Read a message. |
| DELETE | `/api/winlink/mailbox/<box>/<mid>` | — | — | Delete a message. |
| GET | `/api/winlink/mailbox/<box>/<mid>/<attachment>` | — | `download?` | Stream an attachment (passes Pat's Content-Type through). |
| POST | `/api/winlink/mailbox/out` | — | `{…}` | Queue a composed message into Pat's outbox. |
| GET | `/api/winlink/rmslist` | — | `mode` | Slim RMS gateway list for one transport. |
| GET | `/api/winlink/log` | — | — | Pat session log. |
| POST | `/api/winlink/connect` | **CSRF** | `url` | Start a Pat connect session against an alias/transport URL. |
| POST | `/api/winlink/disconnect` | **CSRF** | — | Abort the in-progress connect session. |

---

## 10. FCC callsign database (`/api/lookup*`)

Binary-search over the offline FCC amateur license index (no DB engine).

| Method | Path | Params | Description |
|---|---|---|---|
| GET | `/api/lookup` | `callsign` | Exact callsign, or prefix if it ends in `*`. `{ok, found, result}`. |
| GET | `/api/lookup/prefix` | `callsign` | Prefix search (≥2 chars) → `{ok, prefix, count, results}`. |
| GET | `/api/lookup/name` | `last`, `first?` | Name search (last ≥2 chars). |
| GET | `/api/lookup/grid` | `grid` | Grid-square search (≥2 chars, e.g. `CN87`). |
| GET | `/lookup` | — | The FCC lookup HTML page. |
| GET | `/health` | — | Health + whether the index is present. |

---

## 11. Map & tiles (`/server/map/*`, `/maps/*`)

| Method | Path | Params | Description |
|---|---|---|---|
| GET | `/maps/traffic/map.html` | `focus` | Live map UI — APRS + ADS-B. `?focus=CALLSIGN` opens on a station and loads its track. |
| GET | `/maps/traffic/assets/<filename>` | — | Shared map assets (APRS symbol sprite sheets, etc.), served by the blanket static mount. |
| GET | `/maps/mapengine/<filename>` | — | The map engine — MapLibre GL, PMTiles, the basemap style, and the glyph atlas. |
| GET | `/maps/<filename>` | HTTP Range | Static map files incl. `.pmtiles` (range reads over PMTiles — no full load into RAM). |
| GET | `/api/fs/browse` | `path` | Browse allowlisted roots for `.pmtiles` archives. |
| GET | `/api/fs/pmtiles` | `path` | Stream a `.pmtiles` archive from an allowlisted absolute path with HTTP Range. |

Map tile roots include `/var/lib/graywolf/tiles` and the suite `maps/` dir.

---

## 12. UI page-serving routes

Static HTML served per service (not JSON APIs, listed for completeness):

| Path | Serves |
|---|---|
| `/server/satellites/` , `/server/satellites/<file>` | Satellites page + assets. |
| `/server/winlink/<file>` | Winlink UI. |
| `/lookup` | FCC lookup page. |
| `/server/map/<file>` | Map UI. |

---

## 13. External services (not OASIS APIs)

Run their own web servers; OASIS links out and/or health-checks them.

| Service | Port | Notes |
|---|---|---|
| GrayWolf | 8080 | APRS decoder + its own admin UI; feeds `graywolf-history.db`. |
| Pat (Winlink) | 8082 | Winlink client; proxied via `/api/winlink/*`. |
| Kiwix | 8081 | Offline Wikipedia/ZIM; presence via `/api/health/zim`. |
| OpenWebRX | 8073 | Browser SDR receiver; SDR picked in its own Admin → SDR profiles. |
| WebSSH (ttyd) | 7681 | Browser terminal. |
| gpsd | 2947 | GPS daemon; read by `/api/system`. |

---

## 14. Data stores

| Store | Path | Owner | Used by |
|---|---|---|---|
| APRS history | `/var/lib/graywolf/graywolf-history.db` | GrayWolf (writes), graywolf-api (reads) | `/api/aprs/*` |
| ADS-B history | `/var/lib/adsb/adsb-history.db` | adsb-api | `/api/adsb/*` |
| FCC index | vendored `EN.dat` + index | — | `/api/lookup*` |
| Map tiles | `/var/lib/graywolf/tiles`, `maps/*.pmtiles` | — | `/server/map`, `/api/fs/pmtiles` |
| Station identity | `configuration/station.json` | setup / `/api/aprs/frequency` | most services |
| Hardware inventory | `configuration/hardware.json` | `/api/hardware/*` | service control, apply-hardware |
| Installed features | `configuration/installed-services.json` | setup-oasis / `/api/setup/*` | `/api/installed-services` |
| Map warnings | warnings JSON (suite) | `/api/aprs/warnings` | map "Insert Alerts" |
| TLE cache | `configuration/tle-cache/` | `build-roster.py` / `/api/satellites/refresh` | satellites |
| Speech voice model | `features/speech/voices/` | `features/speech/install.py` | `/api/speech/*` |
| Speech synth cache | `features/speech/cache/` | `common/speech.py` | `/api/speech/say` |

---

*Generated from the route definitions in `server/routes/*.py`,
`services/*/routes.py`, `services/aprs/common/aprs.py` (graywolf-api), and
`services/adsb/common/adsb.py` (adsb-api). If you add or change a route, update
this file.*
