# What's Next — OASIS Development Roadmap

Planned improvements and new features. Items are grouped by area and roughly
ordered by priority within each group. This is a living document — completed
items are removed, not crossed out.

---

## Tools & Calculators

- **Grid / Bearing — manual lat/lon input.** The grid calculator will accept
  GPS coordinates (decimal degrees) as an alternative to Maidenhead grid
  squares, improving accuracy for operators who know their precise location.

- **Grid / Bearing — great-circle path visualization.** The calculator will
  display short-path and long-path bearings on a simple SVG globe sketch so
  operators can confirm antenna orientation at a glance.

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

- **Radio cards for DMR handhelds.** DMR-capable handhelds (Anytone, Ailunce
  HD1) are increasingly common in EmComm deployments. Radio cards covering
  zone/channel programming and DMR-specific menu navigation will be added.

---

## Offline Maps (PMTiles)

- **Map info in the switcher.** Read each archive's PMTiles header (`getHeader()`
  already used for centering) to show region name, zoom range, bounds, and file
  size in the dropdown and the **Load maps** browser — so operators can tell
  regions apart before loading and spot a too-large file on a Pi SD card.

- **Remember the last-loaded map.** Persist the last selected/loaded map (and any
  USB path) in `localStorage`, mirroring the temperature-unit pattern, so
  reopening `maps/map.html` restores the operator's working region.

- **Schema guard.** OASIS styling assumes the OpenMapTiles (OMT) v3.x schema. The
  loader will inspect `vector_layers` on load and surface a clear warning if an
  archive uses a different schema (blank map today, no explanation), instead of
  silently rendering nothing.

- **"Locate me" (offline).** Use the browser Geolocation API (no network needed
  on devices with GPS) to drop and center on the operator's position.

- **Unify APRS stations onto the main map.** The offline map and the APRS map
  share the same MapLibre + PMTiles base; an optional APRS-station layer on
  `maps/map.html` (reusing `/api/aprs/stations`) would give one map instead of two.

---

## Security & Field Networks

- **Document and surface `OASIS_MAP_ROOTS`.** The filesystem map browser
  (`/api/fs/*`) reads `.pmtiles` from an allowlist of roots. The active allowlist
  will be shown on the setup page and documented as the knob for "where can Load
  maps browse," making the (bounded) read surface explicit to operators.

- **Optional shared-network access token.** On a shared field/Wi-Fi network, an
  optional lightweight token gate (env-configured, off by default to preserve the
  zero-friction LAN experience) for the dashboard and `/api/fs/*`, for deployments
  where the network isn't trusted.


