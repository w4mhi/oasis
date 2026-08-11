<!--
Thanks for the pull request. Please keep it to one thing — a focused diff gets
reviewed, a 40-file refactor sits. Delete any section that doesn't apply.
-->

## What this changes

<!-- One or two sentences. What behaviour is different after this merges? -->

## Why

<!-- The operating problem this solves. Link an issue if there is one. -->

## How it was tested

<!--
The single most valuable thing in this box is what HARDWARE you ran it on.
"Verified on a Pi 4 with an RTL-SDR Blog V4 on Trixie" is worth more than any
amount of prose. If you could not test a hardware path, say so plainly — that
is fine, it just tells the maintainer what still needs a bench check.
-->

- Hardware / platform:
- What I exercised:
- What I could **not** test:

## Checks

- [ ] `scripts/run-tests.sh` passes
- [ ] `node --test 'tests/js/**/*.test.js'` passes
- [ ] `ruff check .` is clean
- [ ] Added or updated tests for the change
- [ ] Bumped `version.json` (`python3 scripts/bump-version.py --type <feat|fix|...>`)

## Offline-first

- [ ] No CDN, external API, cloud service, or runtime network call was added
- [ ] No npm / build step, no framework, no database engine
- [ ] Still fits a Raspberry Pi 3 with 2 GB RAM (nothing large loaded into memory)

## If this touches the UI

- [ ] No emoji at or above U+1F000 (the Pi has no emoji font — it renders as tofu)
- [ ] No curly quotes inside an HTML tag
- [ ] Checked in both light and dark theme
- [ ] Screenshots attached

## If this adds an installable feature

- [ ] Registered in **all three** surfaces: `common/setup_registry.py`,
      `setup-oasis.py`, and the checkboxes in `server/system/setup.html`
      (miss the third and it is silently uninstallable from the browser)
- [ ] The installer is idempotent and never downgrades
- [ ] Any `config.txt` edit stays inside its own BEGIN/END block
- [ ] Documented in `docs/SETUP.md`, with a field-debug block
