# Contributing to OASIS

Thanks for looking. OASIS is a personal project by **W4MHI**, and it is opinionated
in ways that are easy to trip over if you treat it like a normal web app. This
document is mostly about those constraints — read the first section before you
write code, and you'll save yourself a rejected pull request.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## The prime directive: offline-first

**OASIS must work with no internet, forever, on a Raspberry Pi 3 with 2 GB of
RAM.** Not "degrade gracefully when offline" — *assume there is no network and
never has been*. This is the single rule that governs every other decision here,
and it is the most common reason a contribution can't be merged.

Concretely, at runtime OASIS fetches **nothing**:

| Not allowed | Why | What to do instead |
|---|---|---|
| CDN `<script>` / `<link>` | There is no internet | Vendor the file into the repo and reference it locally |
| Google Fonts, remote webfonts | Same | Vendor the font, and check the licence permits it |
| A call to any external API | Same | Compute it locally, or ship the data |
| `npm install`, webpack, Vite, a bundler | The Pi has no Node, and there is no build step | Plain `.js` files loaded with `<script>` |
| React / Vue / Svelte / TypeScript | Needs a build step | Vanilla JS, ES2020-ish, no transpile |
| PostgreSQL / MySQL / MongoDB | No database engine on the target | JSON files, SQLite where genuinely needed |
| Loading a whole dataset into RAM | 2 GB, shared with the radio stack | Stream it — see the PMTiles HTTP-range reads and the binary-search FCC index |
| A Docker requirement | The Pi runs bare | systemd units, installed by the setup scripts |

Anything that must come from the internet is fetched **at bundle-build time** on
a machine that has a network, by `scripts/create-oasis-offline.py`, and declared
in `scripts/offline-manifest.json`. The Pi only ever consumes that bundle.

If you're not sure whether an idea violates this, open an issue and ask before
building it.

---

## Also non-negotiable

**No emoji in anything the Pi renders.** Raspberry Pi OS Lite ships no emoji
font, so every codepoint at or above U+1F000 renders as a tofu box on the actual
product. This applies to the dashboard, the kiosk, the handbook, and any
server-generated string. Use inline SVG (see `common/js/incident-icons.js`) or
BMP glyphs like `✓ ⚠ ⌂ ●`. Markdown files such as this one and the README are
fine — they're read on GitHub, not on the panel.

**No curly quotes inside HTML tags.** A smart quote in an attribute value
silently breaks the attribute, and `node --check` does not catch it. The
front-end gate does — see below.

**Metric stays metric where physics says so.** Satellite readouts are always
km; don't route them through the units toggle.

**Don't commit large or encumbered files.** Map tiles (`*.pmtiles`,
`*.mbtiles`), the FCC database, Wikipedia ZIMs, RepeaterBook CSV exports
(copyright — not redistributable), radio-manual PDFs, and Python wheels are all
gitignored on purpose. See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

---

## Getting set up

You do **not** need a Raspberry Pi to work on most of OASIS. A Mac, Windows, or
Linux box runs the server, the dashboard, the forms, the calculators, the
reference library, and the whole test suite. You need a Pi only for the radio
and hardware paths (SDR, GPS, HATs, systemd).

```bash
git clone https://github.com/W4MHI/oasis
cd oasis
python3 scripts/setup-server.py     # builds .venv and installs Flask + gunicorn
python3 start-oasis.py              # detached launcher
# or: scripts/start-server.sh       # runs in the foreground, Ctrl-C to stop
```

Then open **http://localhost:8083**. Override the port with `OASIS_PORT`.

Features that need hardware simply report themselves unavailable — a missing
service greys out its card instead of breaking the page. That's by design, and
new features should behave the same way.

---

## Before you open a pull request

Run all of it. CI runs the same gates, and a red build is the most likely reason
a PR sits.

```bash
# 1 — Python unit suite (uses .venv; system Python has no Flask)
scripts/run-tests.sh
scripts/run-tests.sh -k satellites     # or just the ones you touched

# 2 — Front-end: pure-function tests + the syntax/hygiene gate over every
#     tracked .html/.js (this is what catches curly quotes in tags)
node --test 'tests/js/**/*.test.js'

# 3 — Manifest still parses, and its self-tests pass
python3 -c "import json; json.load(open('scripts/offline-manifest.json'))"
python3 tests/test_offline_manifest.py

# 4 — Everything byte-compiles
python3 -m py_compile $(git ls-files 'scripts/*.py' 'common/*.py' 'tests/*.py' \
  'setup-oasis.py' 'features/*/*.py' 'services/*/*.py' 'services/*/common/*.py' \
  'server/*.py' 'server/routes/*.py' 'maps/*.py' 'oasis-dashboard/*.py')

# 5 — Lint
ruff check .
```

Three GitHub Actions workflows gate the repo:

| Workflow | What it proves |
|---|---|
| `offline-install.yml` (**server-setup**) | `setup-server.py` produces a working environment on Linux · macOS · Windows × Python 3.11 · 3.13, then the unit suite passes on the POSIX runners |
| `offline-manifest.yml` | Manifest JSON is valid, manifest self-tests pass, everything byte-compiles, ruff is clean, thin CLIs still answer `--help` |
| `js-tests.yml` | JS unit tests + the front-end syntax/hygiene gate over every tracked HTML/JS/CSS asset |

CI installs only the core dependencies, so **a test for an optional feature must
`skipUnless` its dependency is present**, and a host-specific test must be
platform-guarded. A test that only passes on a Pi will fail the build.

---

## Testing expectations

The suite is `unittest` (**not** pytest), discovered from `tests/`. There are
~115 Python test modules and ~20 JS ones; please add to them.

- **Bug fix** — add a test that fails before your change and passes after.
- **New API route** — cover the success shape, the failure shape, and the
  `ok` semantics. In this codebase **`ok` means "the request succeeded"**, not
  "the news is good": a health endpoint reporting a stopped service is
  `{"ok": true, ...}` with the stopped state in the body. See
  [docs/api-contract.md](docs/api-contract.md).
- **Front-end logic** — pull the pure function into `common/js/` and unit-test
  it there rather than testing through the DOM.
- **Installers** — assert on the generated systemd unit / config text. Don't
  shell out to real hardware.

---

## Registering a new feature — read this or it won't install

A feature must be registered in **three** places or it will be silently
uninstallable, with no error message anywhere:

1. the feature/service registry (`common/setup_registry.py`),
2. the terminal menu (`setup-oasis.py`),
3. the checkboxes in the **web** Setup page (`server/system/setup.html`).

Missing #3 is the classic failure: the feature works from the terminal and
simply doesn't exist in the browser. If you add a feature, grep all three.

Installers must be **version-aware and idempotent** — safe to re-run, and never
downgrading something already installed. Platform quirks belong in
`scripts/offline-manifest.json`, not in branching logic scattered through the
installer.

If your feature touches `/boot/firmware/config.txt`, it may only edit lines
inside its own `BEGIN/END` block — use the `config_subs` mechanism or stay
purely additive. Blowing away someone's `config.txt` is how you brick a field
station.

---

## Commits, versioning, and branches

- **Branch off `main`.** `main` is the last known-good state; every change gets
  its own branch.
- **Conventional-commit subjects**: `feat(satellites): …`, `fix(kiosk): …`,
  `docs(setup): …`, plus `chore` / `refactor` / `style` / `perf` / `test`.
  Write the subject as a statement about behaviour, not a description of the
  diff.
- **Every commit bumps the version.** `version.json` is the single source of
  truth (the dashboard, `/api/server-info`, and `doctor.py` all read it). Use:
  ```bash
  python3 scripts/bump-version.py --type feat    # minor+1
  python3 scripts/bump-version.py patch          # patch+1
  ```
  `feat` → minor, everything else → patch, breaking → major. Tags are
  `v<version>`, e.g. `v3.25.0`.
- **Notable changes get a [CHANGELOG.md](CHANGELOG.md) entry.** Not every commit
  needs one; every release does.

---

## What makes a good pull request

- **One thing.** A focused diff gets reviewed; a 40-file refactor sits.
- **Say what hardware you tested on.** "Verified on a Pi 4 with an RTL-SDR
  Blog V4 on Trixie" is worth more than any amount of description. If you
  couldn't test the hardware path, say so plainly — that's fine, it just tells
  the maintainer what still needs a bench check.
- **Match the surrounding code.** Comment density, naming, and idiom included.
  The codebase predates the linter and ruff is deliberately configured to the
  bug-class rules only, so please don't reformat untouched lines.
- **Screenshots for UI changes**, in both light and dark theme.

Expect review to be direct and technical. Pushback on an approach isn't
personal; it usually means the change collides with the offline-first rule or
the Pi 3 memory budget in a way that isn't obvious from the diff.

---

## Good first contributions

- **Hardware reports.** Tell us what worked or didn't on your radio, SDR
  dongle, HAT, or Pi model. This is genuinely the most useful thing an operator
  can contribute, and it needs no code.
- **Radio cards** — a cheat-sheet for a radio not yet in `static/radio-cards/`.
- **Documentation** — [docs/SETUP.md](docs/SETUP.md) has a per-subsystem
  field-debug pattern (command → healthy output → broken output). Filling a gap
  there, from a real box, is high value.
- **Handbook pages** — `static/oasis-handbook/` is plain HTML with a shared
  sidebar. No build step, no framework.
- **Non-US band plans and prefixes.** OASIS is US-centric today; that's a gap,
  not a decision.

## Where to ask

Open an issue. For anything security-related, follow
[SECURITY.md](SECURITY.md) instead and report privately.

73 — and thanks for helping.
