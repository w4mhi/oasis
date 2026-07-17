# OASIS Repository Analysis Report

**Date:** 2026-07-16 · **Version analyzed:** 2.7.5 (branch `refactoring-services`)

**Snapshot:** ~279 Python files (8.2k lines in `common/`, 3.5k in `server/app.py`, 4k in `services/`), ~44k lines of HTML (vanilla JS inline, no build step), 381 unit tests (all passing under `.venv`, 11 skipped), 2 CI workflows, MIT licensed.

Scoring: **P1** = fix soon, high payoff · **P2** = plan into normal work · **P3** = nice-to-have.
Each item lists **Importance** (impact if ignored) and **Effort**.

---

## 1 · Flaws

### P1 — CI never runs the unit test suite
**Importance: High · Effort: Low**

You have 381 passing tests, but `offline-manifest.yml` only byte-compiles files and runs the single manifest self-test, and `offline-install.yml` only verifies `setup-server.py` + one import. A regression in `hardware.py`, `setup_engine.py`, the APRS/ADS-B proxies, or any of the 40+ test modules merges green. This is the cheapest, highest-value fix in the repo: add a `python -m unittest discover -s tests` step (after `setup-server.py` installs Flask) to the existing matrix. Note the suite currently fails under system Python (`ModuleNotFoundError: flask`) — CI would also document the supported way to run it.

### P1 — `server/app.py` is a 3,521-line monolith with 73 routes
**Importance: High · Effort: Medium**

The `refactoring-services` branch moved service *logic* into `services/<name>/common/`, but the route layer never followed: setup, FCC lookup, filesystem browse, hardware, Wi-Fi, service control, APRS, ADS-B, Winlink, system stats, and audio all live in one file. Consequences: every feature change touches the same file (merge/regression hotspot), route-level tests import the whole world, and the file mixes trust levels (public map assets next to `sudo reboot`). Flask blueprints per service — mirroring the `services/` layout you already created — would finish the refactor this branch started. Zero new dependencies, works fine on the Pi Zero.

### P1 — Front-end duplication with no shared JS layer
**Importance: High · Effort: Medium**

`index.html` (3,308 lines, ~2,000 of inline JS) and `small-screen/index7.html` (940 lines) reimplement the same helpers (`fmtTemp`, `fmtAlt`, unit formatting, polling loops); `services/map/map.html` is another 3,516-line single file. "No build step" does **not** require inline scripts — plain `<script src="/static/js/units.js">` files shared between the three dashboards would cut duplication, make the code diffable, and finally give you something a JS test harness could load (see §3). Today a bug fixed in one dashboard silently survives in the other.

### P2 — Duplicate tracked files: `tools/` vs `server/tools/`
**Importance: Medium · Effort: Low**

`net-log.html` and `grid-calc.html` are byte-identical and **both tracked in git** at two paths. They *will* drift the first time someone edits only one. Keep one canonical location and serve the other URL via a route alias (like you already do for `/map-assets/`), or delete the stale pair.

### P2 — No authentication on a server that can reboot the host
**Importance: Medium (deliberate trade-off, but undocumented) · Effort: Low–Medium**

Anyone who can reach port 8083 can stop services, join the Pi to a Wi-Fi network, burn RTL-SDR EEPROM serials, and reboot the host. The mitigations present are good — sudoers rules scoped per command, the `X-OASIS-Request` CSRF header on mutating endpoints, portable-mode gating — but they assume every device on the LAN/AP is friendly. For field EmComm with an open AP fallback that's a real exposure. Two cheap steps: (a) write the threat model down in `docs/concept.md` so it's a decision, not an accident; (b) offer an optional operator PIN for the destructive subset (`/api/service`, `/api/wifi/*`, `/api/setup/reboot`, `/api/hardware/burn-serial`) — you already built exactly this pattern for WebSSH's `--basic-auth`.

### P2 — State-changing endpoints on GET without the CSRF header
**Importance: Medium · Effort: Low**

`/api/winlink/connect` and `/api/winlink/disconnect` mutate state (start/abort a Pat RF session) but are `GET` and don't require `X-OASIS-Request`, unlike `/api/service` and `/api/wifi/*`. Any page a browser on the LAN loads could `<img src=…/api/winlink/connect?url=…>`. Make them POST (or at minimum apply the same header check).

### P2 — Inconsistent release hygiene
**Importance: Medium · Effort: Low**

Tags are inconsistent (`2.5.0` vs `v.2.7.5` — note the odd `v.` prefix), there is no CHANGELOG, and `version.json` is the only version record. For a project users deploy offline from USB sticks, "what changed since the bundle I burned in March" is a question you can't currently answer. Adopt one tag format, add a short `CHANGELOG.md`, and bump both together (a preflight check can enforce it).

### P3 — API error handler leaks raw exception text
**Importance: Low · Effort: Low**

`_api_json_error_handler` returns `str(exc)` for every unhandled `/api/*` exception. On a LAN-only box this is mostly a debugging *feature*, but it can leak filesystem paths and internals to any client. Consider logging the full text and returning a truncated/generic message when the exception isn't an `HTTPException`.

### P3 — `Access-Control-Allow-Origin: *` on `/api/fs/pmtiles`
**Importance: Low · Effort: Low**

The map tile streamer allows any origin. Combined with path-listing under `/api/fs/browse` (correctly root-restricted), a hostile page on any origin can read your map archives. Low stakes (tiles aren't secrets), but the wildcard is unnecessary — same-origin covers the actual UI.

---

## 2 · Improvements

### P1 — Add a linter and formatter gate
**Importance: High · Effort: Low**

There is no `pyproject.toml`, no ruff/flake8 config, nothing. At 279 Python files maintained by one person, `ruff` (single binary, no runtime dep, runs fine in CI) catches the unused-import/shadowed-variable/f-string bug class for free. Wire it into `/preflight` and the manifest workflow next to the existing `py_compile` step.

### P2 — Consolidate dashboard polling
**Importance: Medium · Effort: Medium**

`index.html` runs 7+ concurrent `setInterval` fetch loops (hardware+ping, stats, audio, APRS, feed-flow, ADS-B, Wi-Fi — most at 15–30 s). On a Pi Zero 2 W each request is a gunicorn worker wakeup, and with several client browsers open the multiplier adds up. A single `/api/dashboard` aggregate endpoint (or one loop fanning out server-side) would cut per-client request count ~5× and simplify the front-end error handling that each loop currently duplicates.

### P2 — Split the map page's JS into modules
**Importance: Medium · Effort: Medium**

`map.html` now serves both APRS and ADS-B and has grown past 3,500 lines: station table, coverage/convergence analysis, drawing tools, filesystem browser, warnings editor — all in one inline script. Same remedy as the dashboard: plain `.js` files under `services/map/map-assets/` (the directory already exists and is already served). Escaping discipline is good (`_escHtml` is applied to RF-sourced fields — verified in the station table renderer); modularizing protects that property as the file keeps growing.

### P2 — Make the test suite runnable and documented in one command
**Importance: Medium · Effort: Low**

`python3 -m unittest discover -s tests` fails on a fresh clone (no Flask in system Python) and several tests print banners/progress noise into the runner output, which buries the `OK`/`FAILED` line. Document `.venv/bin/python -m unittest discover -s tests` (or add a `scripts/run-tests.sh`), and silence test stdout (capture prints or route them through `logging` gated on verbosity).

### P3 — Finish the hardware-gate migration
**Importance: Medium (known debt) · Effort: Medium**

`_HW_GATE_MIGRATED = {"adsb", "aprs", "openwebrx"}` — Winlink RF is still on the old model, so the conflict-resolution v2 work is incomplete. Finishing it removes the legacy `HW.can_start` path and the branch in `/api/service`.

### P3 — README length and duplication with SETUP.md
**Importance: Low · Effort: Low**

The README is excellent but very long, and feature blurbs partially duplicate `docs/SETUP.md` sections (two places to update per feature). Consider trimming README feature entries to one line + link, keeping SETUP.md canonical.

---

## 3 · Additions

### P1 — Server-side backup of client data (forms, net logs)
**Importance: High · Effort: Medium**

ICS forms auto-save and the net-check-in log live in **browser localStorage**. In a real deployment, a cleared browser cache, a swapped tablet, or a dead client device loses operational records mid-incident — the exact scenario OASIS exists for. Add a small opt-in "save to server" endpoint (JSON files under `configuration/`, mirroring the `station.json`/warnings pattern you already have) plus a restore/download list. No database needed; it fits the flat-file philosophy.

### P2 — JS test coverage for the pure logic
**Importance: Medium · Effort: Medium**

There is no JS test harness at all, yet the front-end contains real algorithms: maidenhead conversion (`static/maidenhead.js`), unit conversions, APRS symbol categorization, distance/bearing math. Once helpers are extracted to `.js` files (§1/P1 above), a `node --test` runner over the pure functions costs nothing at runtime (dev-only, no npm packages required) and can run in CI.

### P2 — Surface `doctor.py` in the web UI
**Importance: Medium · Effort: Medium**

`scripts/doctor.py` (650 lines of diagnostics) is CLI-only, but the audience most likely to need it — an operator whose Pi is misbehaving in the field — is exactly the one without a keyboard attached. A read-only "Diagnostics" page running the same checks and rendering pass/fail would reuse the existing code and the setup page's job/log plumbing.

### P2 — mDNS advertisement (`oasis.local`)
**Importance: Medium · Effort: Low**

Clients currently need to know the Pi's IP (`http://<host-ip>:8083`), which changes when Wi-Fi/AP mode flips — the exact moment users are most confused. Publishing `oasis.local` via Avahi (already present on Pi OS; just ship a `.service` file in the setup script) makes the dashboard reachable by name across AP-fallback transitions.

### P3 — Offline update bundle ("delta" USB update)
**Importance: Medium · Effort: High**

`create-oasis-offline.py` builds full bundles; there's no story for *updating* a deployed Pi from a USB stick short of re-copying everything. A versioned update flow (compare `version.json`, sync changed files, re-run idempotent setup) would fit the offline-first model. High effort — only worth it once there are external deployments to support.

### P3 — CHANGELOG-driven release notes on the dashboard
**Importance: Low · Effort: Low**

Once a CHANGELOG exists (§1), showing "what's new in 2.7.5" on the setup page is nearly free and helps operators who update rarely.

---

## 4 · Suggested order of attack

| # | Item | Priority | Effort |
|---|------|----------|--------|
| 1 | Run the 381-test suite in CI | P1 | Low |
| 2 | Add ruff to preflight + CI | P1 | Low |
| 3 | De-duplicate `tools/` vs `server/tools/` | P2 | Low |
| 4 | POST + CSRF header on Winlink connect/disconnect | P2 | Low |
| 5 | Tag format + CHANGELOG | P2 | Low |
| 6 | Split `app.py` into blueprints per service | P1 | Medium |
| 7 | Extract shared JS helpers; then map.html modules | P1 | Medium |
| 8 | Server-side backup for forms/net logs | P1 | Medium |
| 9 | Document threat model; optional PIN on destructive APIs | P2 | Low–Med |
| 10 | Aggregate dashboard polling endpoint | P2 | Medium |
| 11 | mDNS `oasis.local` | P2 | Low |
| 12 | Doctor page in UI · JS tests · Winlink HW-gate migration | P2–P3 | Medium |

Items 1–5 are a week of small PRs that permanently raise the safety floor; 6–8 are the structural payoff of the refactoring branch you're already on.

---

## 5 · What's already strong (don't break it)

- **Offline-first discipline is real**: vendored wheels, flat-file FCC index with binary search, PMTiles over HTTP range reads, no CDN references — the architecture genuinely honors the constraint.
- **Test culture**: 381 unit tests across hardware parsing, service layouts, proxies, and setup engine is far above typical for a solo project — they just need to run in CI.
- **Security craftsmanship where it exists**: narrow sudoers `Cmnd_Alias` rules, the pinned `oasis-netctl` helper, CSRF custom-header gating, portable-mode surface blocking, and consistent `_escHtml` on RF-sourced strings all show deliberate design.
- **Comments explain *why*** — the codebase documents platform gotchas (pip marker evaluation, embedded-Python `sys.path`, nmcli terse escaping) exactly where they bite.
