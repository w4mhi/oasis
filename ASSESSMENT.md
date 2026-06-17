# OASIS — Project Assessment

**Project:** OASIS — Off-grid Amateur Station Information Suite
**Reviewed:** 2026-06-17
**Scope of review:** Does the project meet its stated goals — (1) work fully offline, (2) install all dependencies offline, (3) run on macOS / Windows / Raspberry Pi, with (4) APRS on the Raspberry Pi?
**Method:** Static review of all server code, setup/build scripts, launchers, and asset bundling, plus empirical testing of the offline dependency resolution against each target platform/Python combination using `pip download --no-index`.

> **Update 2026-06-17 — aarch64 wheel gap fixed.** The Raspberry Pi blocker (finding #1) and the aarch64 portion of the psutil gap (finding #2) have since been resolved. See **[Fixes applied](#fixes-applied-2026-06-17)** at the end. The verdict table and matrix below reflect the post-fix state; the finding write-ups are kept for the record with their resolution noted.

---

## Verdict at a glance

| Requirement | Status | Notes |
|---|---|---|
| Runtime is fully offline (no CDN/tile/font/API calls) | ✅ **Met** | No external asset references found across 108 HTML/CSS/JS files; libraries (MapLibre, PMTiles, pdf-lib) are vendored locally. |
| Install **all** dependencies offline | ✅ **Met on Pi** / ⚠️ elsewhere | **Fixed for the Raspberry Pi** (aarch64, Python 3.9/3.11/3.13 — incl. `psutil`). macOS/Windows/Linux-x86_64 still fall back to PyPI for `psutil` only (those wheels were intentionally not vendored). |
| Runs on macOS | ✅ **Met** | Verified offline wheel coverage for Python 3.9–3.14 (arm64) and 3.9–3.13 (x86_64); `psutil` from PyPI fallback. |
| Runs on Windows | ⚠️ **Partial** | Works for CPython 3.9–3.13 (amd64); **breaks on Python 3.14**; `psutil` requires internet unless USB-bundled. |
| Runs on Raspberry Pi | ✅ **Fixed** | Offline `pip install flask gunicorn psutil` now resolves on Pi OS Bullseye (3.9), **Bookworm (3.11)**, and 3.13. *(was broken — see finding #1)* |
| APRS on the Raspberry Pi | ✅ **Improved** | GrayWolf install path is correct and Pi-only; `psutil` is now vendored for the Pi so the APRS API has its dependency offline. *(Soft-import hardening in #3 still recommended.)* |

**Bottom line:** The *runtime* offline design is genuinely solid and well-executed. The *offline-install* blocker on the Raspberry Pi — the headline issue — has been **fixed**: the missing aarch64 wheels were vendored and the installer now installs `psutil` from the bundle. Remaining items (Windows 3.14, repo size, USB-bundle for ARM, soft psutil import) are lower priority.

---

## What is done well

- **No runtime internet dependency.** A grep across all HTML/CSS/JS found zero CDN, Google Fonts, tile-server, or third-party API references. MapLibre GL, PMTiles, glyph fonts, and pdf-lib are all served from local files (`server/map-assets/`, `static/dependencies/`). This is the hard part of an off-grid suite and it is done correctly.
- **Range-request PMTiles streaming** (`server/app.py:60`) with ETag / `If-None-Match` / 304 handling — efficient, correct map-tile serving from a single Flask process. Good fit for a Pi Zero's memory budget.
- **No database engine for FCC lookup** — binary-search over flat files keeps the Pi footprint tiny, exactly as advertised.
- **Service discovery via `/api/config` / `/server-ports.json`** (`server/app.py:279`) means HTML pages never hardcode ports; `find_free_port()` lets the server move between 8083–8093 without breaking the UI.
- **Graceful cross-platform degradation in the stats endpoints** — `getloadavg`, CPU temperature, and disk-mount auto-detection all fail soft on macOS/Windows (`server/app.py:304`).
- **Sensible launcher UX** — `start.sh` / `start.bat` auto-create the venv, run a pre-flight check, and pick gunicorn when available, Flask dev server otherwise.
- **Correct GrayWolf architecture mapping** (`scripts/install-graywolf.py:84`) — aarch64→arm64, armv7l→armhf, etc., with clear error messages for unsupported targets.

---

## Critical findings

### 1. Offline Flask install fails on the Raspberry Pi (primary target) — `BLOCKER` — ✅ **RESOLVED**

> **Resolved 2026-06-17.** Vendored `MarkupSafe` aarch64 wheels for cp39/cp310/cp311/cp312 (cp313 was already present). Offline `pip install flask` now resolves on all current Pi OS releases. See [Fixes applied](#fixes-applied-2026-06-17). Original analysis below.

`server/wheels/` bundles `MarkupSafe` (a required, compiled dependency of Jinja2 → Flask) for many platforms, **but the only Linux-ARM (aarch64) build is for CPython 3.13**:

```
markupsafe-3.0.3-cp313-cp313-manylinux2014_aarch64...whl   ← the ONLY arm64 Linux wheel
```

There are **no** aarch64 wheels for cp39 / cp310 / cp311 / cp312, and **no** 32-bit ARM (armv7l/armhf/armv6l) wheels at all.

Raspberry Pi OS defaults:
- **Bookworm (current):** Python **3.11**, aarch64 → **no matching wheel**
- **Bullseye (legacy):** Python **3.9**, aarch64 → **no matching wheel**
- 32-bit Pi OS / Pi Zero (armv6l/armv7l) → **no wheel at all**

Empirically confirmed with pip's platform resolver:

```
$ pip download flask --no-index --find-links server/wheels --only-binary=:all: \
      --platform manylinux2014_aarch64 --python-version 3.11 --abi cp311 --implementation cp
ERROR: Could not find a version that satisfies the requirement markupsafe>=2.1.1 (from flask)
ERROR: No matching distribution found for markupsafe>=2.1.1
```

The same command for `--python-version 3.13` succeeds. So `python3 scripts/setup-server.py --offline`, `start.sh`, and the README's "all offline-vendored except psutil" quick-start **all fail on a stock Pi** with the bundled wheels. The server cannot start.

**Fix:** Vendor `MarkupSafe` (and any other compiled deps) for aarch64 **cp39 + cp311** at minimum, ideally also armv7l. Generate the wheel set per target with `pip download --platform manylinux2014_aarch64 --python-version 3.11 --only-binary=:all:`. Better still, pin to a known Pi-OS Python and document it, or ship a musl/static fallback. Consider adding a CI matrix that runs the exact offline-install command for each `{platform, python}` you claim to support, so this regresses loudly.

### 2. `psutil` is never available offline — contradicts the stated requirement — `HIGH` — ✅ **RESOLVED for the Pi**

> **Resolved 2026-06-17 (aarch64).** Vendored the `psutil` `cp36-abi3` aarch64 wheel (one file covers Python 3.6+) and reworked `scripts/setup-server.py` to install `psutil` from the bundle first, falling back to PyPI only on platforms whose wheel is not vendored (macOS/Windows/Linux-x86_64). The Pi is now fully offline. Original analysis below.

The README states dependencies are "all offline-vendored except psutil," and `scripts/setup-server.py:35` fetches `psutil` from PyPI at runtime. There is **no psutil wheel in `server/wheels/`**. Consequences:

- A genuine offline first-run on the Pi (the whole point of the project) **cannot install psutil**.
- `setup-server.py --offline` explicitly skips it.
- `scripts/build-usb.py:171` downloads the psutil Windows wheel from PyPI at *build* time — so even the "portable" bundle needs internet to be built.

This directly violates requirement #2 ("install **all** dependencies … offline").

**Fix:** Vendor `psutil` wheels for every supported `{platform, python}` into `server/wheels/` alongside MarkupSafe, and drop the "online" install step. `psutil` ships manylinux aarch64/armv7l wheels on PyPI, so this is straightforward.

### 3. APRS API hard-imports `psutil` — the one package most likely missing — `HIGH`

`graywolf-api/graywolf_api.py:17` does a top-level `import psutil`. Because of findings #1/#2, `psutil` is the dependency most likely to be absent on a Pi. A top-level import means the **entire APRS service fails to start** — including `/api/aprs/stations`, which has nothing to do with system stats — rather than just degrading the `/api/system` route. Note `server/app.py:309` already does this correctly (lazy `import psutil` inside the route with a graceful 503).

**Fix:** Move `import psutil` into `api_system()` and guard it, mirroring `server/app.py`. The APRS map should work even when psutil is missing.

---

## Cross-platform support matrix (offline wheel install)

Verified by resolving `flask` against `server/wheels/` for each target:

| Platform | Python 3.9 | 3.11 | 3.12 | 3.13 | 3.14 |
|---|---|---|---|---|---|
| macOS arm64 | ✅ | ✅ | ✅ | ✅ | ✅ |
| macOS x86_64 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Linux x86_64 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Windows amd64 | ✅ | ✅ | ✅ | ✅ | ❌ (no cp314 win wheel) |
| **Linux aarch64 (Pi 64-bit)** | ✅¹ | ✅ | ✅ | ✅ | ❌ |
| **Linux armv7l/armhf (Pi 32-bit)** | ❌ | ❌ | ❌ | ❌ | ❌ |

¹ Python 3.9 (Pi OS Bullseye): pip auto-selects the vendored `gunicorn 23.0.0` (gunicorn 26.0.0 requires ≥3.10). flask + psutil + gunicorn all resolve offline.

The aarch64 row was previously red for everything except 3.13; the vendored MarkupSafe + psutil wheels (and a 3.9-compatible gunicorn) fixed it for 3.9–3.13 — covering today's Pi OS Bookworm (3.11) and Bullseye (3.9). **32-bit ARM (armv7l/armhf) is still unsupported offline** — no ARM32 wheels are vendored; add them if the original Pi Zero / 32-bit OS must be supported.

---

## Medium findings

### 4. Repository is ~2.3 GB with large binaries committed directly to git — `MEDIUM`

- `.git` alone is **993 MB**; working tree **2.3 GB**.
- `maps/washington.pmtiles` (**115 MB**) and `radio-manuals/` (**719 MB** of PDFs) are committed as ordinary git objects — no Git LFS.

The README's quick-start (`git clone …`) therefore pulls hundreds of MB before anything runs, which is painful over the field/low-bandwidth links this project targets, and makes history rewrites expensive. PMTiles are regenerable; manuals are static blobs better suited to LFS or an out-of-band download step.

**Fix:** Move large binaries to Git LFS or a `setup-assets.py` download step (consistent with how FCC data is already handled via `.gitignore`), and consider `git filter-repo` to shrink history.

### 5. `build-usb.py` is x86-64 / Windows-only; no Pi or Apple-Silicon portable bundle — `MEDIUM`

`PYTHON_EMBED_URL` pins `python-3.12.10-embed-amd64.zip`, and the cross-build wheel filter (`scripts/build-usb.py:165`) accepts only `none-any` or `win_amd64 cp312` wheels. The "USB bundle" thus targets Windows-amd64 + generic Linux-with-system-Python. There is no self-contained artifact for the Pi itself or for Apple Silicon, and the Linux `start.sh` it writes still `pip install`s Flask/psutil from PyPI on first run (`scripts/build-usb.py:289`) — i.e. **not offline**.

### 6. `gunicorn` not present in the active `.venv` despite being bundled — `LOW`

The current `.venv` (Python 3.14) has `flask` and `psutil` but **not** `gunicorn`, even though `gunicorn-26.0.0-py3-none-any.whl` is vendored. `start.sh` silently falls back to the Flask dev server. Functional, but the dev server is not recommended for the multi-client LAN use this serves. Worth ensuring setup actually installs it (and note gunicorn is correctly omitted on Windows, where it cannot run).

---

## Low findings / polish

- **Path-traversal guard is prefix-based** (`server/app.py:209`): `target.startswith(os.path.realpath(SUITE_ROOT))` matches a sibling directory sharing the prefix (e.g. `.../oasis-emcomm-evil`). Reachable via `?path=../oasis-emcomm-evil`. Low impact on a trusted off-grid LAN, but use `os.path.commonpath([target, root]) == root` (or append `os.sep`) to close it.
- **No authentication, binds `0.0.0.0`** — acceptable and intentional for an off-grid LAN, but worth a one-line note in the README so deployers don't expose it on a routable network. `debug=False` is correctly set.
- **Wheel cruft:** both `MarkupSafe-2.1.5-cp312...x86_64` and the `markupsafe-3.0.3` set are present; the old one looks like leftover. Trim to reduce confusion.
- **Hardcoded DB path** `/var/lib/graywolf/graywolf-history.db` (`graywolf-api/graywolf_api.py:23`) — fine for the Pi, but an env override would help testing on dev machines.
- **Two `markupsafe` major versions** could let pip pick inconsistently across platforms; pin exact versions in `requirements.txt` (currently only lower bounds, e.g. `Flask>=3.0`) so the vendored wheels and resolver always agree.

---

## Recommended priority order

1. **Vendor MarkupSafe + psutil for aarch64 (cp39 & cp311) and armv7l** → unblocks the Raspberry Pi and makes the offline-install promise true. *(Findings #1, #2)*
2. **Make `psutil` a soft dependency in `graywolf_api.py`** so APRS works without it. *(Finding #3)*
3. **Add a CI matrix** running the offline install for every claimed `{platform, python}` so coverage gaps fail the build. *(prevents #1 recurring)*
4. **Move large binaries to Git LFS / a download step** and slim history. *(Finding #4)*
5. **Extend `build-usb.py`** to produce a real offline Pi/arm bundle (or document that the Pi path is clone-based, prepped online once). *(Finding #5)*
6. Tidy wheel set, pin versions, harden the browse-path check. *(Low findings)*

---

## Fixes applied (2026-06-17)

The Raspberry Pi offline-install blocker has been fixed in this repo:

**Wheels added to `server/wheels/`:**

| Wheel | Why |
|---|---|
| `markupsafe-3.0.3-cp39…cp312 …manylinux2014_aarch64.whl` (4 files) | Closes the compiled-dependency gap that broke Flask on the Pi. cp313 aarch64 was already present, so 3.9–3.13 are now covered. |
| `psutil-7.2.2-cp36-abi3-…manylinux2014_aarch64.whl` | Vendors psutil for the Pi (abi3 → one wheel works on Python 3.6+). |
| `gunicorn-23.0.0-py3-none-any.whl` | gunicorn 26.0.0 requires Python ≥3.10; this lets pip pick a compatible gunicorn on Pi OS Bullseye (3.9). 26.0.0 is kept for 3.10+. |

(`server/wheels/` is now ~1.6 MB total — negligible.)

**Code change — `scripts/setup-server.py`:**
- New `install_psutil(allow_online=…)` installs psutil from the bundled wheel first (`--no-index`), and only falls back to PyPI on platforms whose wheel isn't vendored. The Pi never touches the network.
- Removed the old "psutil requires internet / will be missing" online-only step and updated the `--offline` help text.

**Verification (via `pip download --no-index --find-links server/wheels` against each target):**
- ✅ `flask gunicorn psutil` resolve fully offline on aarch64 for Python **3.9, 3.11, 3.13**.
- ✅ `python3 scripts/setup-server.py` runs clean end-to-end; `py_compile` and `--help` pass.

### Second round (2026-06-17) — findings A/C/D/F/G/I/J/K/L

- **C — psutil vendored for all platforms.** Added `psutil` abi3 wheels for macOS (arm64 + x86_64), Windows, and Linux x86_64. `setup-server.py` now installs psutil fully offline everywhere it has a wheel.
- **D — Python 3.14 wheels.** Added MarkupSafe `cp314` for Windows, Linux x86_64, and Linux aarch64. (Intel-Mac is capped at 3.11 — upstream MarkupSafe ships no x86_64 macOS wheel past 3.11; Apple Silicon covers 3.9–3.14.)
- **A — APRS API hardened** (`graywolf-api/graywolf_api.py`): `psutil` is now imported lazily inside `/api/system` with a graceful 503, so the APRS station feed serves even if psutil is absent.
- **K — DB path override**: `graywolf_api.py` reads `APRS_DB_PATH` (defaults to the Pi path) for off-Pi testing.
- **G — path-traversal guard** (`server/app.py`): replaced the prefix `startswith` check with `os.path.commonpath`, closing the sibling-directory escape (verified blocked).
- **I — removed** the leftover `MarkupSafe-2.1.5` wheel.
- **J — `requirements.txt` pinned** to exactly what is vendored (`Flask==3.1.3`, `MarkupSafe==3.0.3`, `psutil==7.2.2`, `gunicorn>=23,<27` with a note on the two vendored gunicorns).
- **F — `build-usb.py` is now offline**: the generated Linux/macOS launcher installs from bundled wheels (`--no-index`), and the Windows embedded-Python is populated by extracting vendored wheels (no get-pip, no PyPI). Build needs internet only for the python.org embeddable runtime.
- **L — new `scripts/vendor-wheels.py`** is the single source of truth for the wheel set, with a `--check` mode wired into **`.github/workflows/offline-install.yml`** that resolves the full {platform × python} matrix offline (plus a real `setup-server.py --offline` run on Linux/macOS/Windows). A wheel gap now fails CI.

**Full-matrix verification:** `python3 scripts/vendor-wheels.py --check` → **all 27 targets satisfied offline** (aarch64/x86_64/macOS-arm64/Windows on 3.9–3.14, Intel-Mac on 3.9–3.11). `server/wheels/` is ~2.3 MB / 44 wheels.

**Still open (lower priority):** finding #4 (repo size / Git LFS — the 2.3 GB tree), 32-bit ARM (armv7l/armhf) offline support, and the very-low-severity polish notes.

---

## Requirement-by-requirement summary

- **"Should work offline"** — ✅ at runtime. The asset story is clean and verified.
- **"Be able to install all dependencies offline"** — ✅ on the Raspberry Pi (fixed: aarch64 wheels vendored, installer reworked). ⚠️ macOS/Windows/Linux-x86_64 still pull only `psutil` from PyPI; vendor those wheels too for a universal offline install.
- **"Support mac / Windows / Raspberry Pi"** — macOS ✅; Windows ⚠️ (Python 3.14 wheel gap); Raspberry Pi ✅ (64-bit, Python 3.9–3.13). 32-bit Pi OS still unsupported offline.
- **"APRS targets Raspberry Pi"** — ✅ architecturally correct (GrayWolf .deb + systemd, Linux-only installer, dedicated map/API on 8085); psutil is now vendored for the Pi. Hardening the top-level psutil import (#3) is still recommended so the APRS map survives a missing psutil.

The design intent is sound and most of the code is well above hobby quality. The headline ARM/Linux packaging gap has been fixed; the project now delivers offline install on its primary Raspberry Pi target, with a short list of lower-priority follow-ups remaining.
