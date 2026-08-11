# Changelog

All notable changes to OASIS are recorded here, newest first.

**Versioning rules.** The suite version lives in `version.json` (single source of
truth — the dashboard, `/api/server-info`, and `doctor.py` all read it).

**Per-commit SemVer (adopted 2026-08-02): every commit bumps `version.json`.**
- regular commit (`fix`/`chore`/`docs`/`style`/`refactor`/`perf`/`test`/…) → **patch +1** (`2.8.1 → 2.8.2`)
- feature / major modification (`feat`) → **minor +1, patch → 0** (`2.8.2 → 2.9.0`)
- breaking change → **major +1, minor/patch → 0**

Use `scripts/bump-version.py <patch|minor|major>` (or `--type <feat|fix|…>`) to
compute it. Tagged commits use the `v<version>` form — **no dot** after `v`
(e.g. `v2.8.1`), matching `v2.8.0`. Not every commit needs a tag or a CHANGELOG
entry, but notable releases still bump this file alongside `version.json`.

## v3.25.2 — 2026-08-10

Documentation review ahead of going public. The docs had drifted 238 commits
behind the code.

### Changed
- **README, `docs/SETUP.md`, `docs/api.md` and the handbook reviewed against the
  shipping code.** Undocumented features written up, stale claims corrected, and
  dead paths, anchors, unit names and endpoints fixed throughout.
- **Handbook:** new pages for Station Voice, Service Operations and Diagnostics;
  screenshots re-shot from a station running this release.
- **`CHANGELOG.md`** closes the gap between v2.8.1 and v3.25.0.

### Added
- [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
  [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and GitHub issue and PR templates.
- [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) — every bundled library,
  font, daemon and dataset with its licence and any redistribution obligation,
  including the Jenny voice attribution requirement. Items that could not be
  verified are recorded as unverified rather than guessed.

### Fixed
- Two documentation errors that could mislead an operator: the handbook
  understated the sudoers grant behind service controls, and the documented
  `doctor.py` CI recipe parsed a key that no longer exists.

### Removed
- An unreferenced ADS-B icon sheet with no traceable licence, and an orphaned
  handbook screenshot.

---

## v3.25.0 — 2026-08-10

**The first public release.** Everything below the 3.x heading has been in daily
use on the maintainer's stations; this is the version at which the project was
opened up.

### Added
- **The satellite card says what your station can actually do.** Downlinks
  collapsed from a row of buttons into one dropdown that arms a
  `frequency|mode` pair, showing only what this station can tune — and nothing
  is armable until an SDR is assigned to it in the hardware console.
- **A pass says *where* it peaks**, not just how high: peak azimuth alongside
  peak elevation, and slant range to a bird overhead.
- **Birds are named the way operators say them** — `AO-7`, not `OSCAR 7` — with
  the orbit class riding on the designator line.
- **The pass-elevation floor is the operator's**, set in
  `configuration/station.json`, not hardcoded at 10°.
- **The Zulu clock strikes the hour**, in three states from one control: off,
  chime, or chime-then-spoken-time. Quiet hours 22:00–07:00 local.
- **The kiosk avatar** — a face on the 1920×1200 layout that wakes and glows
  when the station speaks, with the mouth gated on audio onset rather than on
  the request.

### Fixed
- **One autostart per station.** Pi OS Trixie's labwc honours *both* the XDG
  autostart entry and its own autostart line, so stations installed before this
  fix run **two stacked Chromium kiosks** — invisible, double the load, and the
  reason muting a pass alert on the visible window left the hidden one chiming.
  **Re-run `scripts/enable-autostart-pi.py` after upgrading.**
- A re-computed pass is the same pass, so it stops being announced twice.
- The bell now stops the noise you can actually hear, not just the next one.
- `/listen` resolves by frequency **and** mode, so CW on a frequency shared with
  an FM downlink no longer hands back a WAV of silence.
- The Winlink probe no longer leaks 2 MiB of `/dev/shm` per poll.
- The maps health pill counts GrayWolf's tile store rather than the suite's own
  `maps/` tree, which is empty on every real station.

---

## The 3.x series — 2026-08-02 → 2026-08-10

238 commits between `v2.8.1` and `v3.25.0`, consolidated here by theme rather
than reconstructed version by version. The individual bumps are in the git
history; what follows is what actually changed for an operator.

### The API contract (3.0.0 — breaking)

The reason 3.0.0 was a major bump. Every route was migrated to a single
response contract, with an AST conformance gate and a debt ratchet that may only
shrink. The rule that drove it: **`ok` means the request succeeded, not that the
news is good** — a health endpoint reporting a stopped service returns
`{"ok": true, …}` with the stopped state in the body. Before this, callers
could not distinguish "the query failed" from "the answer is no".

`scripts/api-probe.py` was written as a functional harness for the migration and
immediately found 17 real defects. Contract and reference now live in
[docs/api-contract.md](docs/api-contract.md) and [docs/api.md](docs/api.md).

### Satellites

The largest single body of work in the series.

- An offline pass predictor over a roster aggregated from **SatNOGS**
  (downlink frequencies and modes) and **CelesTrak** (TLEs), computed on the Pi
  with **Skyfield/SGP4** and baked into the offline bundle at build time.
- **Live SDR audio** — arm a downlink, then Listen or Record through the pass
  (FM/APRS · CW · SSB), including recording a pass from the DRAWS radio port.
  Recordings retain under a 2 GB budget with a 1 GB free-space floor; the newest
  is never pruned.
- **Pass alerts** — Morse "V" at T-10/T-5, spoken with the station's voice,
  sounded by the touch kiosk as well as the browser. Bells live in the shared
  roster, so arming one on a laptop wakes the shack; muting stays per-screen.
  Monitoring a bird now arms its bell.
- Filters by capability, band, and orbit class; additive-RGB capability
  colouring; coverage pills; a live sky and footprint view.

### Speech

A station-wide voice, built and then rebuilt when the first approach proved
wrong.

- The server synthesises to a **cached WAV via a Piper subprocess** and the
  browser plays it through the chime's already-unlocked AudioContext.
  Announcements serialise instead of mushing together.
- The **Jenny (Dioco)** neural voice, with espeak-ng as the fallback ladder when
  Piper isn't installed or the platform can't run it.
- `/api/speech/status` and `/api/speech/say`.
- The speech-dispatcher route was **abandoned** — it owned the sound card, which
  is unacceptable on a box whose whole job is radio audio.

### The touch kiosk

- Satellite, traffic, and hazard cards; a shared service registry so the kiosk
  and the dashboard can no longer disagree about what is running; a Wi-Fi pill;
  per-clock colour cycling; SDR flow meters.
- The avatar card and the hour bell (see 3.25.0 above).

### Hardware and device assignment

- The **Service Operations console** (the "mixer board") — a device×service
  matrix that routes an SDR or sound card between APRS, ADS-B, satellites,
  and Winlink, with a per-device lock and a one-click STOP ALL that
  deliberately leaves Web SSH running so it cannot lock you out.
- A **resource guardian** — enabled by default — that watches temperature, CPU, and memory and
  arms a 30-second cancellable STOP ALL on a threshold trip.
- Assigned devices start their services again after a reboot.
- **DRAWS HAT support** — two-port radio audio and GPS/PPS, with a
  self-compiled `draws.dtbo` so the HAT and `librtlsdr ≥ 2.0` can coexist on
  Trixie. The governing rule: **override an OS device-tree overlay on evidence
  that the hardware enumerated, never on a kernel version string.**
- The Argon ONE fan feature, which masks `argononed` because upstream watches
  GPIO4 — the same pin an L76X GPS HAT uses for 1PPS, which produced phantom
  shutdowns.

### APRS, maps, and traffic

- **Operator warnings and hazards** — a 15-type EmComm catalog (EOC, shelter,
  fire, flood, hazmat, road closed, water point, …), each with its correct APRS
  symbol, placeable on the map, broadcast as **APRS objects** over IS, RF, or
  both, and cleanly killed with a tombstone when the incident clears.
- A GrayWolf-tiled dark basemap; rail, ferry, airport and boundary layers;
  highway-exit labels. POI labels were **removed** — the glyph atlas ships only
  codepoints 0–1023 and a missing glyph blanked whole tiles.
- OASIS stopped shipping and downloading its own tiles. **GrayWolf is the map
  source**, and the planet-extract downloader was removed.
- Shared traffic-list logic with RF/IS/ADS-B source pills across both dashboards.

### System, setup, and packaging

- The dashboard header became a **card row** — clocks, GPS, CPU with per-core
  bars, and top processes — replacing the old stat bar.
- A **browser-based installer**: the Setup page installs and removes features,
  not just checks health, backed by a root worker service.
- A **Diagnostics page** with 22 registered checks rolled into capability
  verdicts and a single "fix this first" pick.
- Bundle profiles, `--verify` against `bundle-manifest.json`, and `--update` in
  place — including onto a USB stick.

### Known issues carried into the public release

- Winlink RF remains **experimental**; the Telnet gateway works.
- OpenWebRX is not in the offline bundle (it needs a third-party apt repo) and
  has no UI for assigning a dongle.
- No 32-bit ARM wheels, so Speech and `psutil` are unavailable on 32-bit Pi OS.
- RTL-SDR APRS requires Pi OS Trixie for `librtlsdr ≥ 2.0`.

---

## v.2.8.1 — 2026-08-02

Small maintenance release on 2.8.0: a hardware-robustness fix, longer ADS-B
history, header readability tweaks, and a per-clock colour picker on both the
dashboard and the kiosk. No breaking changes — in-place upgrade is a code pull
(plus an `adsb-api` restart to pick up the new retention window).

### Added
- **Per-clock colour cycle (index + kiosk).** Click/tap the LOCAL or UTC clock to
  cycle its colour — blue → white → amber → green → chartreuse → orange. Per-clock
  and mutually exclusive (the two clocks can't share a colour), saved to
  `localStorage`, theme-aware. Shared six-colour set + storage keys across
  `index.html` and `oasis-dashboard/dashboard.html`; chartreuse/orange are toned
  down in the light theme for contrast.

### Changed
- **ADS-B detailed-track retention 48h → 120h (5 days).** `ADSB_RETAIN_HOURS`
  default raised so `/history` reaches back five days (still env-overridable;
  ~100 MB of observations at the new window).
- **Dashboard header readability.** Processes and GPS cards moved off the smallest
  type tier onto the primary reading size; GPS label→value gap made explicit and
  the GPS grid row/column gaps trimmed 30%.

### Fixed
- **RGB Cooling HAT survives a missing OLED.** The daemon no longer crash-loops
  when the SSD1306 panel is absent (or wedged) — the OLED is now optional, so the
  thermally-critical fan + RGB control keep running. The installer distinguishes
  the required fan/RGB MCU (0x0d) from the optional OLED (0x3c). Added a unit test
  for the degrade path.

### Removed
- **Orphaned `draws-audio` / `draws-gps` feature stubs.** Empty leftovers from the
  abandoned DRAWS go-box (incompatible with the Trixie-based stack); never wired
  into the setup registry, removed to stop confusing users.

## v.2.8.0 — 2026-07-22

Feature + fix release on the 2.7.5 baseline: the services refactoring, the
repository-analysis fixes, ADS-B history / airline work, shared front-end
modules, the first JS test harness, server-side backup of ICS forms + the net
log, dashboard-polling and mDNS improvements, and a round of APRS map filter
fixes. No breaking changes — every URL, method, and response shape is unchanged
(verified by a 52-route snapshot and the full unit suite), so an in-place upgrade
needs only a code pull and a re-run of the idempotent setup. Minor, not major,
bump for that reason.

### Added
- **APRS Alerts "show only Fires" (hazard isolate).** Each hazard row in the
  map's APRS Alerts card is now click-to-isolate: clicking a row (e.g. Fire)
  solos that hazard category on the map — a one-tap emergency view — and clicking
  the soloed row restores all. Keyboard-accessible; the active row highlights.
  Previously the rows were inert (the click handlers looked for markup the render
  no longer emitted).
- **APRS source filter in the map station list.** The Src column filter gains an
  **APRS** option (RF + IS, ADS-B excluded) alongside All / RF / IS / ADS-B, so
  the two APRS sources can be shown together without aircraft.
- **Server-side backup of ICS forms + the net log.** The ICS-213/214/309 forms
  and the net-check-in log lived only in browser localStorage — a cleared cache
  or a swapped tablet lost them mid-incident. A **Save to server** / **Restore**
  action now persists JSON snapshots under `static/<kind>/saved/` via a shared
  `POST /api/forms/save` + `GET /api/forms/list` (whitelisted kinds); the existing
  ICS-205 save/list delegate to the same store. Same flat-file model as
  station.json/warnings.json — no DB. Shared client helper `common/js/form-backup.js`.
- **Dashboard reachable by name (`<hostname>.local`).** The AP-fallback setup
  installs avahi and the install/final messages now name the actual host
  (`http://<hostname>.local:8083`) instead of a hard-coded "oasis.local" that only
  resolved if the box happened to be named `oasis`. The setup does NOT rename the
  machine — renaming the host is invasive (and, e.g., invalidates Chromium's
  hostname-keyed kiosk profile lock).
- **Live install log in the setup page.** Privileged feature installs run in a
  root worker whose output only reached the systemd journal; it's now streamed
  into the setup log window (via a per-job log file the web wait-loop tails), so
  apt/dpkg errors are visible without SSH/journalctl.
- **GPS UART/GPIO HAT selectable in setup.** Added a `gps-L76X (UART / GPIO HAT)`
  feature next to `gps (USB / autodetect)` — previously only the USB feature was
  offered, so a UART GPS HAT had no correct choice and autodetect grabbed the
  wrong device. The two are mutually exclusive (both drive gpsd).
- **Post-run reboot prompt.** After a setup run, the page shows a "Reboot
  required to finish" note (and highlights the Reboot button) when any feature
  came back `installed_needs_reboot`.
- **ADS-B aircraft list keeps 24 h of history.** Out-of-range aircraft used to
  vanish from the station lists the moment they left the live poll. The recorder
  now exposes `GET /recent?hours=` (one row per aircraft = its latest
  observation in the window, from the history DB), proxied as
  `/api/adsb/recent`. The map's bottom drawer and the dashboard station table
  merge this with live state (live wins) and age the rows exactly like APRS
  stations. Map markers and the map's right-side "N aircraft" panel stay
  live-only. The heavier DB scan is throttled to ≤ once/60 s, off the 2 s live
  cadence, to stay Pi-cheap.
- **Shared ADS-B front-end module** `common/js/adsb.js` (altitude-to-color scale,
  airline-operator decode from the flight callsign, and the Age-filter fetch
  window) plus a seeded ICAO→telephony operator table `common/js/adsb-operators.js`
  (regenerated by `scripts/build-adsb-operators.py` from OpenFlights `airlines.dat`).
  Backs the aircraft altitude coloring, the airline card tag, and the Age-filter
  history window below.
- **Airline tag in the map aircraft card:** decodes the operator from the flight
  callsign and shows its ATC telephony name for civil aircraft
  (e.g. `ASA424` → `[ALASKA]`); military aircraft keep their `[MIL]`/`[mil?]`
  badge and get no airline tag.
- **Shared front-end helper modules** `common/js/{units,geo,format}.js`
  (unit/temp/altitude/speed/uptime formatting, Maidenhead grid + distance/
  bearing, age/last-heard) now back both the main dashboard (`index.html`)
  and the 7" kiosk (`small-screen/index7.html`), ending the helper drift
  between them — a fix in one used to silently not apply to the other. Plain
  `<script src="/common/js/...">` tags; no build step, no npm.
- **First JS test harness.** `tests/js/` runs under `node --test` (built into
  Node 24, nothing to install), wired into `/preflight` (step 8) and a new
  `.github/workflows/js-tests.yml` CI gate — delivers analysis-report §3/P2.

### Changed
- **Minor-version bump: 2.7.5 → 2.8.0**, marking the completed services
  refactoring (service logic under `services/<name>/`, maps under
  `services/map/`) as the new baseline. Behaviour-preserving, so a minor rather
  than major bump.
- **Dashboard polling de-bursted.** `index.html` fired a synchronized
  herd every 30 s — `pingAll` bursting all 17 service health checks at once plus
  the hardware/stats/audio/wifi loops on the same boundary — and ~100 requests per
  start/stop toggle. Now a round-robin `pingNext` (one check per ~1.8 s, same ~30 s
  per-badge refresh), `fetchHardware` decoupled onto its own tick, the post-toggle
  burst replaced with a one-at-a-time settle poll, and the 30 s loops phase-offset.
  Same freshness and request count, no bursts (mirrors the `index7` approach).
- **Hardware-conflict-resolution v2 completed for all four services.** Winlink RF
  joined ADS-B / APRS-feed / OpenWebRX on the unified model: the start-time gate
  (`_HW_GATE_MIGRATED`) is gone, `/api/service` never refuses a start, `winlink`
  is a first-class service in `common/hardware.py` with a real radio-port apply
  hook, and start-click contention (Winlink RF vs. GrayWolf) is resolved in
  `index.html`'s `resolveHardwareConflict`.
- **`server/app.py` split into Flask blueprints** (analysis item 6): service
  routes live with their service (`services/{adsb,aprs,winlink,fcc_database,
  map}/routes.py`), server-core domains under `server/routes/{setup,hardware,
  wifi,service_control,health,system,files}.py`, shared runtime config in
  `server/appconfig.py`. `app.py` is now ~300 lines (app creation, hooks,
  blueprint registration, launcher). Every URL, method, and response shape is
  unchanged — verified by a 52-route response snapshot taken before and after
  the split, the full 385-test suite, and a live gunicorn boot. The WSGI entry
  (`app:app`, `--chdir server`) is unchanged; no new dependencies.
- **GPS and i2c-enable now signal that they need a reboot** (exit code 10), so
  the setup page prompts for it — GPS no longer silently shows "not configured"
  after install, and enabling i2c flags the reboot `/dev/i2c-1` needs.
- **Aircraft altitude values are altitude-colored** on the map ADS-B panel,
  map station list, and dashboard aircraft rows (using the same color scale;
  the callsign keeps its purple accent). The map's aircraft card altitude value
  is colored as well.
- **The Age filter defaults to 24 hours** (dashboard and map station list; was
  "All"), and selecting **ALL** now pulls the full aircraft history (previously
  aircraft were hard-capped at 24 h regardless of filter). APRS stations and
  aircraft now behave identically: Age="24h" for recent activity, Age="All" for
  the full database scan.
- **Aircraft map labels are always upper-case**, matching APRS callsign display
  (the popup card still shows the flight ID as received).
- **The map's ADS-B panel row labels are upper-case and altitude-coloured**
  (same scale as the map icons); the "N aircraft" header keeps its purple accent.
- **7" kiosk temperature now renders with a space before the unit**
  (`72°F` → `72 °F`), matching the main dashboard — the shared `fmtTemp` is
  the canonical spaced form.

### Fixed
- **Satellites: `/api/satellites/passes` no longer wedges at 500 on a large
  roster.** It computed a 24 h propagation for every roster sat and wrote the
  cache only after the whole loop, so a big roster (150+ sats) overran gunicorn's
  30 s worker timeout — the worker was killed before the cache was written, so
  every retry recomputed cold and the endpoint stayed stuck at 500 (roster greyed,
  nothing selectable). Now time-budgeted and incremental: selected sats first,
  progress persisted and resumed across polls, always 200.
- **Satellites: selection now persists.** `build-roster.py` runs under the root
  installer worker and left `configuration/satellites.json` root-owned, so the
  non-root server's `/api/satellites/select` write 500'd and selections were lost
  (dashboards showed nothing monitored). build-roster now chowns the roster to the
  operator; the select endpoint reports a clear error instead of a blank 500.
- **Privileged feature installs no longer fail with a queue permission error.**
  `enable-oasis-installer.py` created `configuration/installer-queue/` root-owned,
  so the non-root server couldn't drop job files (`EACCES`) — satellites, kiosk,
  and headless features failed to install. The queue is now created (and re-owned)
  as the operator.
- **Satellites voice install degrades gracefully.** A transient Raspberry Pi OS
  `speech-dispatcher` vs. `+rpt1` `speech-dispatcher-audio-plugins` apt conflict
  no longer fails the whole Satellites feature — voice is optional (pass alerts
  still chime), so it warns and continues. `install-predict.py` also now verifies
  the real `skyfield.api` import (catching a missing compiled numpy) instead of a
  bare `import skyfield`, and self-heals from PyPI when online.
- **Auto-path lines now respect the station-list column filter.** Isolating a
  type that has no path (e.g. Aircraft) left RF/IS path lines drawn for hidden
  stations; `_refreshAutoPaths` now filters its candidates through the same
  visibility predicate as the markers, and `applyFilter` rebuilds the overlay on
  every filter change (a manually-shown path also clears if its station is
  filtered out).
- **APRS heard RF/IS chips no longer hide ADS-B.** Toggling the RF or IS chip
  forced aircraft off too (`showADSB = pathRF && pathIS`); the chips now gate
  only the APRS RF/IS stations, leaving ADS-B to the Src dropdown.
- `services/winlink/common/winlink.py`: `run()` without an explicit
  `repo_root` referenced the undefined `_SCRIPTS_DIR` (NameError leftover from
  the pre-refactor script). Now derives the repo root from its own location.
- `common/lookup.py`: `build_name_index()` no longer loads the entire
  ZIP-code CSV it never used.
- Removed the duplicated `server/tools/` copies of `net-log.html` and
  `grid-calc.html` — `tools/` is the canonical location every link uses.
- Assorted dead code found by the new linter: unused imports, unused
  assignments, a shadowed `import re`, placeholder-less f-strings.

### Security
- **Winlink connect/disconnect hardened against CSRF.** Both endpoints mutate
  state (connect keys the transmitter on the RF path) but accepted plain GET.
  They now require POST plus the same `X-OASIS-Request` header gate as
  `/api/service` and `/api/wifi/*`.
- **Removed the unnecessary `Access-Control-Allow-Origin: *`** from the APRS
  history/stats daemon (port 8085). It's reached only by the main server's
  same-origin proxy (`services/aprs/routes.py` → `127.0.0.1:8085`), never by a
  browser cross-origin, so the wildcard only let any origin read the data for no
  benefit. (The report's original pmtiles target was already fixed in map routes.)

### CI / tooling
- **One-command quiet test runner** `scripts/run-tests.sh` — uses the `.venv`
  Python (system Python has no Flask) and unittest's `-b/--buffer`, so noisy
  per-test output is hidden on success and the `OK`/`FAILED` verdict is the last
  line. Documented in the README.
- **`common/js/maidenhead.js` now covered by the JS harness** — the last pure-logic
  front-end module without tests. Added a CommonJS export + `tests/js/maidenhead.test.js`
  (encoding, parsing/validation, round-trip). JS suite: 24 tests.
- **The full unit test suite now runs in CI** (`offline-install.yml`,
  ubuntu + macos matrix) via the `.venv` that `setup-server.py` builds. It
  previously never ran on push — only byte-compile and an import smoke test.
- **ruff lint gate** added (`ruff.toml` + step in `offline-manifest.yml`):
  bug-class rules only (pyflakes, E4/E7/E9); repo idioms (path-bootstrap
  imports, compact one-liners) are deliberately excluded. Dev/CI-only tool —
  the offline runtime wheel set is unchanged.
- Workflow trigger paths broadened so the gates fire on changes to `common/`,
  `server/`, `services/`, `displays/`, and `tests/`.

## v.2.7.5 and earlier

Pre-changelog releases (services refactoring, ADS-B aircraft support, setup
orchestrator, hardware conflict resolution v2, FCC lookup, offline maps, …) —
see `git log` and the tags `v.2.7.5` / `2.5.0`.
