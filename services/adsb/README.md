# ADS-B (dump1090-fa + adsb-api)

Aircraft tracking via a cheap RTL-SDR dongle. Two systemd units, both **off by
default** — ADS-B is only started when the operator enables it from the
dashboard's ADS-B card, since it holds the RTL-SDR exclusively (mutually
exclusive with any other RTL-SDR-based feature, e.g. GrayWolf).

## Units

- **`dump1090-fa`** — the Mode S/ADS-B decoder (installed via `apt`). Owns the
  RTL-SDR and writes live aircraft positions to `aircraft.json`.
- **`adsb-api`** — OASIS's own recorder + history API
  (`services/adsb/common/adsb.py`, run via `services/adsb/install.py --serve`).
  Polls `aircraft.json`, records observations to a local SQLite history
  database, evaluates proximity/altitude alerts against the station's
  location, and serves it all on `:8086`:
  - `GET /health` — poller liveness (age of last JSON read)
  - `GET /aircraft` — current live snapshot
  - `GET /history?since=<ts>&icao=<hex>` — recorded observations
  - `GET /alerts` — recent alert events (in-memory ring buffer)

Both units are installed disabled; the dashboard enables/starts them on
demand and disables/stops them when ADS-B is turned off.

## Env overrides (for off-Pi testing)

| Variable | Default | Purpose |
|---|---|---|
| `ADSB_DB_PATH` | `/var/lib/adsb/adsb-history.db` | SQLite history database path |
| `ADSB_JSON_PATH` | `/run/dump1090-fa/aircraft.json` | dump1090-fa live aircraft JSON to poll |
| `ADSB_API_PORT` | `8086` | HTTP API port |
| `ADSB_ALERT_RADIUS_KM` | `50` | Proximity alert radius around the station |
| `ADSB_POLL_SECS` | `1.0` | Poll interval for `aircraft.json` |

Set these to point at a local JSON file and a writable DB path to run
`adsb.serve()` on a dev machine without dump1090-fa installed.

## Offline packages

Phase 1 installs `dump1090-fa` via `apt` only. Vendored offline `.deb`
install (via the offline-manifest bundle group + `services/adsb/packages/`)
is added in a later task.
