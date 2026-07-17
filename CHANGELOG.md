# Changelog

All notable changes to OASIS are recorded here, newest first.

**Versioning rules.** The suite version lives in `version.json` (single source of
truth — the dashboard, `/api/server-info`, and `doctor.py` all read it). Every
release bumps `version.json` and this file **together**, and the release commit
on `main` is tagged `v.<version>` (e.g. `v.3.0.0`) — that tag format is the
standard from 3.0.0 on.

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

## v.2.7.5 and earlier

Pre-changelog releases (services refactoring, ADS-B aircraft support, setup
orchestrator, hardware conflict resolution v2, FCC lookup, offline maps, …) —
see `git log` and the tags `v.2.7.5` / `2.5.0`.
