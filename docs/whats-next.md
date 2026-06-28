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

## Digital Modes (FT8 / WSJT-X)

Target platform: **Raspberry Pi 4 / 4 GB (or CM4 stack)** — the Zero 2 W is too
constrained for the `jt9` decoder plus a GUI. WSJT-X is `apt`-available, so it
fits the existing install-script + manifest pattern (`install-wsjtx.py`, an
apt-type entry), pulling Hamlib (`rigctld`) for CAT/PTT.

- **Offline time discipline (the hard part).** FT8 needs the clock within ~±1 s
  with no internet NTP. Solution: **GPS** (an I2C GPS HAT on the GPIO header for
  the CM4 stack, or USB) feeding `gpsd` + `chrony`, with the **WittyPi RTC**
  holding time across reboots. A dashboard **clock source / offset** indicator
  will tell the operator when FT8 timing is trustworthy (GPS-locked vs. coasting
  on RTC drift, which can exceed FT8 tolerance after a day with no sky view).

- **Two rig paths.** (a) **IC-705 over USB** — one cable carries USB-audio CODEC
  + CAT + the radio's own GPS NMEA; its separate sound card lets FT8 coexist with
  APRS-on-DRA. (b) **DRA-Pi + FT-857D** — DRA audio + PTT, FT-857D CAT via FTDI;
  this shares the DRA sound card with GrayWolf, so FT8 and APRS are mutually
  exclusive on that radio.

- **Browser-native UI.** WSJT-X is a Qt desktop app, so surface it in the
  dashboard via a **noVNC tile** (Xvfb + VNC) to keep the "any browser on the
  LAN" model, or launch it on the local kiosk display. Add a `system/setup.html`
  check for `wsjtx` / `rigctld` / `chrony` + GPS lock.

- **Audio device help.** Reuse `/api/audio` so the setup page points the operator
  at the correct `hw:N,0` card for WSJT-X (same hint already used for GrayWolf).

---

## Dashboard Service Controls

- **Start / stop / restart OASIS services from the dashboard.** Add the *write*
  side to the service cards that already show up/down status, so an operator can
  free CPU/RAM (stop Kiwix when idle) and resolve hardware contention (the
  RTL-SDR can feed only one of GrayWolf-APRS *or* OpenWebRX at a time) without a
  shell. A `POST /api/service {unit, action}` endpoint runs `systemctl
  start|stop|restart` against a **fixed allowlist** of OASIS units (graywolf,
  graywolf-api, kiwix, ttyd, pat, aprs-sdr-feed, openwebrx) — never a free-form
  unit, and **never the OASIS web service itself** (self-DoS). Authorization is
  OS-side, **no passwords in the browser**: an opt-in `enable-service-controls.py`
  installs a narrow polkit/sudoers rule scoped to just those units + actions.
  UI is start/stop/restart on each card, **confirm-on-stop**, re-poll status
  after. v2: make it RTL-SDR-aware (starting OpenWebRX offers to stop the SDR
  feed, and vice versa).

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

## Install, Updates & Recovery

- **Document the two install paths in SETUP.md (docs task — no new scripts).**
  Both paths already work on one codebase; the gap is that SETUP.md doesn't spell
  out which to use when, which causes confusion. Write it down: (1) *online* —
  the machine has internet, so skip the bundle entirely: `git clone` +
  `setup-server.py` (PyPI fallback) + the `install-*.py` scripts (apt/GitHub)
  pull directly — this is how an always-connected station runs day to day.
  (2) *offline* — a bare/dark machine: build the bundle where there *is* internet
  (`create-oasis-offline.py`), carry it on USB, install with
  `setup-oasis.py` (zero network), and refresh the USB in place with
  `create-oasis-offline.py --update --dir /mnt/usb`. Same scripts either way —
  the only difference is whether packages come from the internet or the USB.

- **Alert-only update checks + image rollback.** A cron job runs only when the
  station has internet and *checks* for new packages/commits — it never
  auto-applies. When something's available it raises a dashboard notice
  ("updates available — review the update page") and the operator decides. Before
  any update is applied, a last-known-good image is written to USB using a
  live-rootfs-safe tool (`rpi-clone` / `image-backup`, **not** a raw `dd` of a
  mounted root), with a one-button rollback. Fragile components (librtlsdr,
  kernel, audio stack, gunicorn) stay `apt-mark hold`-pinned so even a manual
  update is deliberate about tested versions.

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


