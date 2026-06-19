# What's Next — OASIS Development Roadmap

Planned improvements and new features. Items are grouped by area and roughly
ordered by priority within each group. This is a living document — completed
items are removed, not crossed out.

---

## Dashboard & Service Health

- **APRS station age filter on the dashboard.** The APRS stations table in
  `system/dashboard.html` will include a time filter (last 30 min / 1 h / 6 h)
  matching what is available on the APRS map page.

---

## APRS Map

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

## Net Logger

- **Lat/lon in CSV export.** Exported net logs will include the latitude and
  longitude for each check-in so that re-imported logs display accurate map
  positions rather than falling back to grid square approximations.

- **Duplicate callsign warning.** Checking in an already-logged callsign will
  show a non-blocking warning. Duplicates will still be allowed (they are
  legitimate in some net formats) but will be flagged.

---

## Tools & Calculators

- **Grid / Bearing — manual lat/lon input.** The grid calculator will accept
  GPS coordinates (decimal degrees) as an alternative to Maidenhead grid
  squares, improving accuracy for operators who know their precise location.

- **Grid / Bearing — great-circle path visualization.** The calculator will
  display short-path and long-path bearings on a simple SVG globe sketch so
  operators can confirm antenna orientation at a glance.

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

- **GrayWolf API — persistent SQLite connection.** The database connection will
  use WAL mode and be kept open across requests, reducing per-request latency
  on SD card storage.

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
  Python bundled by `scripts/create-oasis-offline.py` (AMD64); Linux and macOS bootstrap
  from the vendored wheels using system `python3` on first run. Only the
  Windows embedded runtime is AMD64-specific.
