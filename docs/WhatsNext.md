# What's Next — OASIS Development Roadmap

Planned improvements and new features. Items are grouped by area and roughly
ordered by priority within each group. This is a living document — completed
items are removed, not crossed out.

---

## Dashboard & Service Health

- **True service health checks.** GrayWolf and Kiwix will expose lightweight
  `/health` endpoints that OASIS pings directly, replacing the current
  `no-cors` fetch that cannot distinguish a healthy service from a broken one.

- **Non-blocking CPU sampling in the APRS API.** `graywolf_api.py` currently
  waits 1 second per request to read CPU usage. A background sampler will run
  every 5 seconds and cache the result so API calls return immediately.

- **APRS station age filter on the dashboard.** The APRS stations table in
  `system/dashboard.html` will include a time filter (last 30 min / 1 h / 6 h)
  matching what is available on the APRS map page.

---

## APRS Map

- **Region-configurable map.** The APRS map is currently hardcoded to
  Washington state. It will read from `maps/map-config.json` to select the
  active PMTiles region, making it usable in any deployment area without code
  changes.

- **Station time-range filter.** A filter control (last 30 min / 1 h / 6 h /
  all) will be added to the map so operators can hide stale stations and focus
  on recent activity.

- **Historical track playback.** Selecting a station will show its movement
  track over a configurable time window, drawn as a line on the map using
  position history from the GrayWolf database.

- **GrayWolf API — time-range query.** The `/api/aprs/stations` endpoint will
  accept an optional `since` parameter (minutes) to return only stations heard
  within that window, reducing payload size for large deployments.

---

## Maps

- **Map coverage expansion.** A guided workflow for downloading and registering
  PMTiles for any region will be added. Currently only Washington state
  (Puget Sound / Issaquah) PMTiles are included.

---

## FCC Callsign Lookup

- **FCC data refresh from the dashboard.** An admin action in
  `system/dashboard.html` will trigger a re-download and re-index of FCC data,
  completing the management loop without requiring a terminal session.

- **Grid accuracy disclosure.** The lookup result will show a note when the
  displayed Maidenhead grid square is derived from a ZIP code centroid, which
  may be several miles from the operator's actual location.

---

## ICS Forms

- **localStorage quota warning.** If browser storage is nearly full, a visible
  warning will prompt the operator to export their data before it is at risk.

---

## Net Logger

- **Lat/lon in CSV export.** Exported net logs will include the latitude and
  longitude for each check-in so that re-imported logs display accurate map
  positions rather than falling back to grid square approximations.

- **Duplicate callsign warning.** Checking in an already-logged callsign will
  show a non-blocking warning. Duplicates will still be allowed (they are
  legitimate in some net formats) but will be flagged.

- **FCC re-lookup in edit mode.** When a callsign is corrected while editing
  an existing log entry, the name and location fields will be refreshed from
  the FCC database automatically.

---

## Tools & Calculators

- **Grid / Bearing — manual lat/lon input.** The grid calculator will accept
  GPS coordinates (decimal degrees) as an alternative to Maidenhead grid
  squares, improving accuracy for operators who know their precise location.

- **Grid / Bearing — great-circle path visualization.** The calculator will
  display short-path and long-path bearings on a simple SVG globe sketch so
  operators can confirm antenna orientation at a glance.

- **Solar / Propagation — 60m and 6m band conditions.** The current band
  condition matrix covers four HF band pairs. 60m (emergency interoperability
  channel) and 6m (sporadic-E beacon) will be added.

- **Solar / Propagation — KP index interpretation.** The solar page will
  include a plain-language aurora and geomagnetic storm alert derived from the
  A/K indices, with guidance on which bands are expected to be degraded.

- **Solar / Propagation — condition history.** The last 3–5 parsed NOAA
  bulletins will be stored in localStorage for side-by-side comparison across
  reporting periods.

- **Gray Line — long/short path indicator.** The gray line page will mark both
  the short-path and long-path windows for any entered grid square, helping
  operators identify the best propagation window for DX contacts.

- **Power & Battery — temperature derating.** A temperature input will adjust
  battery capacity estimates for cold-weather deployments, where lead-acid and
  lithium cells lose significant capacity.

- **Power & Battery — lithium vs. lead-acid profiles.** Separate discharge
  curves for LiFePO4 and AGM/SLA will give more accurate runtime estimates
  than the current flat depth-of-discharge model.

- **Battery / Power Planning — ICS-205 integration.** The Power & Battery
  calculator will include an option to push the power budget summary into the
  Special Instructions field of an open ICS-205 form.

---

## Winlink

- **Position report workflow completion.** `winlink/position-report.html` and
  `winlink/to-position.html` will be reviewed and completed to cover the full
  Winlink position reporting workflow — paste, parse, display, export — as a
  coherent end-to-end tool.

- **Radio settings content expansion.** `winlink/radio-settings.html` will be
  expanded to cover all radio models present in `radio-cards/` and
  `repeater-guide/`, keeping settings synchronized across the suite.

---

## Radio Reference Content

- **Cheatsheets for additional radios.** Quick-reference cheatsheets will be
  added for radios widely used in EmComm that are not yet covered: Baofeng
  UV-5R family, Anytone AT-D878UV (DMR), and additional Yaesu mobiles.

- **Radio cards for DMR handhelds.** DMR-capable handhelds (Anytone, Ailunce
  HD1) are increasingly common in EmComm deployments. Radio cards covering
  zone/channel programming and DMR-specific menu navigation will be added.

- **Firmware version tracking.** Each radio card and cheatsheet will include
  the firmware version the content was written against, so operators know when
  a page may be out of date for their hardware.

---

## Server & Backend

- **GrayWolf API — configurable paths.** The hardcoded paths in
  `graywolf_api.py` (`/var/lib/graywolf/graywolf-history.db`) will be
  configurable via environment variables so the API works on non-standard Pi
  setups and development machines.

- **GrayWolf API — persistent SQLite connection.** The database connection will
  use WAL mode and be kept open across requests, reducing per-request latency
  on SD card storage.

- **File browser — search and filter.** `system/browser.html` will add a
  filename filter input so operators can quickly locate files in directories
  with many entries (e.g., `radio-manuals/`).

---

## Setup & Onboarding

- **Setup checklist page.** A one-page setup wizard (`system/setup.html`) will
  check each component (Flask running, FCC data indexed, PMTiles present,
  GrayWolf reachable, Kiwix reachable) and show step-by-step instructions for
  anything that is not yet configured. New users will be linked here
  automatically if required components are missing.

---

## Known Limitations

These are by design or accepted tradeoffs, documented here for transparency.

- **No authentication.** Any device on the local network can access OASIS,
  including the file browser. This is intentional for emergency mesh network
  deployments where speed matters more than access control.

- **No HTTPS.** The server runs plain HTTP. Appropriate for trusted local
  networks; not recommended for public or internet-facing deployments.

- **FCC grid squares are ZIP-centroid derived.** The FCC license database
  contains ZIP codes, not GPS coordinates. Grid squares are computed from ZIP
  centroids and may be several miles from the operator's actual location.

- **GrayWolf and Kiwix are external services.** OASIS links to them but does
  not manage their installation beyond the provided scripts, and they show as
  DOWN on the dashboard when not running. (The map tile server is **not**
  external — it is part of the OASIS Flask app and works wherever OASIS runs,
  including the USB bundle.)

- **USB bundle runs on Windows, Linux, and macOS.** Windows uses the embedded
  Python bundled by `scripts/create-offline-dist.py` (AMD64); Linux and macOS bootstrap
  from the vendored wheels using system `python3` on first run. Only the
  Windows embedded runtime is AMD64-specific.
