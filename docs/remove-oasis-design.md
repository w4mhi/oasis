# remove-oasis.py — factory reset / uninstall

**Status:** approved design · 2026-06-29
**Script:** `scripts/remove-oasis.py`

## Purpose

Give an operator a single "reset to factory default" command that undoes everything
the OASIS setup scripts install on a Raspberry Pi: stops/disables/removes services,
deletes OASIS-managed system files, and strips the OASIS-managed blocks from
`config.txt`. The goal is a clean Pi ready for a fresh install — without destroying
the large, expensive-to-re-download data sets.

## Decisions (locked)

- **All-or-nothing.** One full teardown. No per-subsystem flags.
- **No `apt remove`.** Packages (chromium, lxde, rtl-sdr, gpsd, tcpdump, …) are left
  in place — removing them risks breaking the desktop or unrelated software.
- **Never auto-delete downloaded data.** Maps/FCC DB/ZIMs/wheels are expensive to
  re-fetch (often impossible offline). The script **preserves them and prints a
  manual-deletion advisory** with paths and sizes. There is no destructive data flag.
- **Dry-run by default.** Print the full plan and change nothing unless `--apply`.
- **Boot target:** warn only — print the `systemctl set-default graphical.target`
  command; do not auto-flip it.

## CLI

```
python3 scripts/remove-oasis.py            # dry-run: print the teardown plan, change nothing
python3 scripts/remove-oasis.py --apply    # perform the teardown
python3 scripts/remove-oasis.py --check    # report what OASIS state is present; change nothing
```

- Linux/`systemctl` guard up front (`_fail` off-Pi), matching the other scripts.
- `--check` and bare invocation are both read-only; only `--apply` mutates state.
- Reuses `common.oasis_lib` helpers (`_hr`, `_step`, `_ok`, `_info`, `_warn`,
  `_fail`, `_run`) and the same banner/idempotent style as the install scripts.

## Architecture

Self-contained declarative teardown. A single module with small, focused functions
driven by data tables (services, files, config blocks, data paths). Each removal
function is **idempotent** and reports `_ok` (removed) / `_info` (already absent).

To avoid drift from the installers, import the config-block markers rather than
re-typing them:

- `BLOCK_BEGIN` / `BLOCK_END` from `enable-dra-pi`
- `BLOCK_BEGIN` / `BLOCK_END` from `enable-cm4stack`

(Imported via `importlib` since the filenames contain hyphens.)

### Source-of-truth tables

```python
SERVICES = [   # /etc/systemd/system/<name>.service
    "oasis", "oasis-panel",
    "graywolf", "graywolf-api", "pat", "kiwix", "webssh",
    "aprs-sdr-feed", "openwebrx", "rgb-cooling-hat",
]

FILES = [      # plain files removed with sudo rm -f
    "/etc/sudoers.d/oasis-service-controls",
    "/etc/modprobe.d/rtlsdr-blacklist.conf",
    "/usr/local/bin/oasis-browser-launch",
    "/usr/local/bin/ttyd",
    "/usr/local/bin/kiwix-serve",
]

DIRS = [       # directories removed with sudo rm -rf
    "/opt/rgb-cooling-hat",
]

# Large data — NEVER auto-deleted. Probed for existence, sized, listed in advisory.
DATA_PATHS = [
    "/var/lib/graywolf",                 # graywolf history DB + tiles (system)
    "<repo>/maps",                       # *.mbtiles / *.pmtiles + pmtiles binary
    "<repo>/fcc-offline-database",       # FCC license DB
    "<repo>/server/wheels",              # vendored Python wheels
    "<repo>/offline-packages",           # bundled .deb / tarballs
    "<repo>/**/*.zim",                   # Wikipedia / Kiwix ZIMs
]
```

`<repo>` resolves to the script's repo root (`Path(__file__).resolve().parents[1]`).

## Teardown steps (under `--apply`)

1. **Services** — for each in `SERVICES`: `systemctl stop`, `systemctl disable`,
   `rm -f /etc/systemd/system/<name>.service`, then a single `daemon-reload` at the
   end. All `check=False` (a missing/already-stopped unit is fine).
2. **Files** — `sudo rm -f` each path in `FILES`.
3. **Dirs** — `sudo rm -rf` each path in `DIRS`.
4. **config.txt** (`/boot/firmware/config.txt` or `/boot/config.txt`):
   - Remove the DRA-Pi managed block (between its markers).
   - Remove the CM4Stack managed block (between its markers).
   - Remove the standalone `dtoverlay=i2c-rtc,ds3231` line added by `enable-rtc`
     (no markers — matched line-exact).
   - **Leave** `dtparam=i2c_arm=on` (shared, harmless).
   - Back up `config.txt` once to `config.txt.oasis-remove.bak` before rewriting.
5. **hwclock-set** — if `/lib/udev/hwclock-set.oasis.bak` exists, restore it over
   `/lib/udev/hwclock-set` (undo the `--systz` patch).

## Warnings / manual follow-ups (always printed)

- **Downloaded data advisory** — list each existing `DATA_PATHS` entry with its
  `du -sh` size and the exact `rm -rf` command, under a clear "left in place —
  delete manually if you want a clean slate" heading.
- **Boot target** — if `systemctl get-default` is `multi-user.target`, print
  `sudo systemctl set-default graphical.target` as an optional manual step.
- **fake-hwclock** — `enable-rtc` removed it; reinstalling needs network:
  `sudo apt install fake-hwclock` when back online.
- **i2c group** — membership left as-is (harmless).
- **apt packages** — left installed by design.
- **Reboot** — required to drop the `config.txt` overlays.

## `--check` output

Read-only status report: for each service, `systemctl is-active`/`is-enabled` and
whether the unit file exists; presence of each `FILES`/`DIRS` entry; which
`config.txt` blocks/lines are present; whether the `hwclock-set` backup exists; and
the data advisory (sizes). Changes nothing.

## Dry-run output (bare invocation)

Same structure as `--apply` but every mutating action is printed as
`would remove …` / `would restore …` with no `_run` of mutating commands. Ends with
a one-line hint: `Re-run with --apply to perform the teardown.`

## Testing

- **Off-Pi guard:** running on non-Linux `_fail`s cleanly (matches sibling scripts).
- **Idempotency:** a second `--apply` on an already-clean Pi reports everything
  already absent and exits 0.
- **Dry-run safety:** bare run and `--check` perform zero mutating `_run` calls
  (verified by asserting no `sudo`/`systemctl <mutating>` invocations in dry-run).
- **config.txt block removal:** unit test feeding a synthetic `config.txt` with both
  managed blocks + the RTC line + an unrelated user line → only OASIS lines removed,
  user line and `i2c_arm` preserved, backup written.
- Lives under `scripts/tests/` alongside the existing tests; runs without a Pi.

## Out of scope

- Removing apt packages.
- Auto-deleting maps/FCC/ZIMs/wheels.
- Reverting `i2c` group membership or reinstalling `fake-hwclock`.
- Undoing anything the install scripts did not create.
