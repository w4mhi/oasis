# Changelog

All notable changes to OASIS are recorded here, newest first.

**Versioning rules.** The suite version lives in `version.json` (single source of
truth — the dashboard, `/api/server-info`, and `doctor.py` all read it). Every
release bumps `version.json` and this file **together**, and the release commit
on `main` is tagged `v.<version>` (e.g. `v.3.0.0`) — that tag format is the
standard from 3.0.0 on.

## Unreleased

### Added
- **ADS-B aircraft list keeps 24 h of history.** Out-of-range aircraft used to
  vanish from the station lists the moment they left the live poll. The recorder
  now exposes `GET /recent?hours=` (one row per aircraft = its latest
  observation in the window, from the history DB), proxied as
  `/api/adsb/recent`. The map's bottom drawer and the dashboard station table
  merge this with live state (live wins) and age the rows exactly like APRS
  stations. Map markers and the map's right-side "N aircraft" panel stay
  live-only. The heavier DB scan is throttled to ≤ once/60 s, off the 2 s live
  cadence, to stay Pi-cheap.
- **Shared front-end helper modules** `static/js/{units,geo,format}.js`
  (unit/temp/altitude/speed/uptime formatting, Maidenhead grid + distance/
  bearing, age/last-heard) now back both the main dashboard (`index.html`)
  and the 7" kiosk (`small-screen/index7.html`), ending the helper drift
  between them — a fix in one used to silently not apply to the other. Plain
  `<script src="/static/js/...">` tags; no build step, no npm.
- **First JS test harness.** `tests/js/` runs under `node --test` (built into
  Node 24, nothing to install), wired into `/preflight` (step 8) and a new
  `.github/workflows/js-tests.yml` CI gate — delivers analysis-report §3/P2.

### Changed
- **Aircraft map labels are always upper-case**, matching APRS callsign display
  (the popup card still shows the flight ID as received).
- **The map's ADS-B panel row labels are upper-case and altitude-coloured**
  (same scale as the map icons); the "N aircraft" header keeps its purple accent.
- **7" kiosk temperature now renders with a space before the unit**
  (`72°F` → `72 °F`), matching the main dashboard — the shared `fmtTemp` is
  the canonical spaced form.

## v.3.0.0 — 2026-07-16

Quality-gate release: the services refactoring plus the first round of fixes
from the repository analysis (`ANALYSIS-REPORT.md` items 1–5).

### Security
- **Winlink connect/disconnect hardened against CSRF.** Both endpoints mutate
  state (connect keys the transmitter on the RF path) but accepted plain GET.
  They now require POST plus the same `X-OASIS-Request` header gate as
  `/api/service` and `/api/wifi/*`.

### Fixed
- `services/winlink/common/winlink.py`: `run()` without an explicit
  `repo_root` referenced the undefined `_SCRIPTS_DIR` (NameError leftover from
  the pre-refactor script). Now derives the repo root from its own location.
- `common/lookup.py`: `build_name_index()` no longer loads the entire
  ZIP-code CSV it never used.
- Removed the duplicated `server/tools/` copies of `net-log.html` and
  `grid-calc.html` — `tools/` is the canonical location every link uses.
- Assorted dead code found by the new linter: unused imports, unused
  assignments, a shadowed `import re`, placeholder-less f-strings.

### CI / tooling
- **The full unit test suite (385 tests) now runs in CI** (`offline-install.yml`,
  ubuntu + macos matrix) via the `.venv` that `setup-server.py` builds. It
  previously never ran on push — only byte-compile and an import smoke test.
- **ruff lint gate** added (`ruff.toml` + step in `offline-manifest.yml`):
  bug-class rules only (pyflakes, E4/E7/E9); repo idioms (path-bootstrap
  imports, compact one-liners) are deliberately excluded. Dev/CI-only tool —
  the offline runtime wheel set is unchanged.
- Workflow trigger paths broadened so the gates fire on changes to `common/`,
  `server/`, `services/`, `displays/`, and `tests/`.

### Changed
- Major-version bump: 2.7.5 → 3.0.0, marking the completed services
  refactoring (service logic under `services/<name>/`, maps under
  `services/map/`) as the new baseline.
- **`server/app.py` split into Flask blueprints** (analysis item 6): service
  routes live with their service (`services/{adsb,aprs,winlink,fcc_database,
  map}/routes.py`), server-core domains under `server/routes/{setup,hardware,
  wifi,service_control,health,system,files}.py`, shared runtime config in
  `server/appconfig.py`. `app.py` is now ~300 lines (app creation, hooks,
  blueprint registration, launcher). Every URL, method, and response shape is
  unchanged — verified by a 52-route response snapshot taken before and after
  the split, the full 385-test suite, and a live gunicorn boot. The WSGI entry
  (`app:app`, `--chdir server`) is unchanged; no new dependencies.

## v.2.7.5 and earlier

Pre-changelog releases (services refactoring, ADS-B aircraft support, setup
orchestrator, hardware conflict resolution v2, FCC lookup, offline maps, …) —
see `git log` and the tags `v.2.7.5` / `2.5.0`.
