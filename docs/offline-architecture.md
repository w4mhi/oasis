# OASIS offline-install architecture

How OASIS decides **what** external packages to install and **where to get them
from** — and why that decision is now driven by a single manifest instead of
hardcoded, single-suite lists.

---

## The bug that forced this

OASIS vendored Debian packages for **one** suite: `DEBIAN_SUITE = "bookworm"`.
A station was flashed with Raspberry Pi OS **Trixie** (Debian 13) and OASIS
installed. On a fresh flash, `rtl-sdr` is absent, so the offline-first installer
installed the **bundled bookworm** `librtlsdr` — version **0.6.0**. Bookworm's
`rtl-sdr` is **pre-2.0 and has no RTL-SDR Blog V4 support** (V4 support landed
upstream in `librtlsdr`/`rtl-sdr` **≥ 2.0**, which Trixie ships as `2.0.2`).

Symptoms: the V4 dongle reported `PLL not locked`, dropped samples, and decoded
zero APRS — while a cloned Trixie image with apt's `2.0.2` worked perfectly. The
"never downgrade" guard didn't help, because nothing was installed yet (absent →
just install the stale bundled one).

**Root cause:** a single-suite bundle + offline-first preference installed a
stale, suite-mismatched, *functionally broken* package onto a newer OS.

---

## The model: one manifest, two consumers

`scripts/offline-manifest.json` is the **single source of truth** for every
external package / driver / library. It is consumed by two programs that run on
**different machines** and must not be merged:

```
                         scripts/offline-manifest.json
                       (typed · suite-aware · min_version)
                          /                          \
        BUILDER  (any host)                       INSTALLER  (the Pi)
  create-oasis-offline.py                    setup-oasis-offline.py
  • reads the manifest                       • reads the same manifest
  • fetches per suite × arch                 • detects internet + host suite
  • vendors into the bundle                  • resolves source (below)
  • writes resolved versions back            • enforces min_version gates
    (lockfile)                               • delegates to common/<feature>.py
                                               for install / test / services
```

- The **builder** has no Pi, no systemd, no dongle. It cross-fetches `.debs`
  (via `Packages.gz`), GitHub release assets, PyPI wheels, and tarballs. It
  cannot create services or test hardware — that's the installer's job.
- The **installer** runs on the target, where apt/dpkg, systemd and the dongle
  exist. It decides where each package comes from, then runs the imperative,
  per-feature steps (DVB blacklist, systemd units, hardware tests). Those steps
  stay as **code** in `scripts/common/<feature>.py` — the manifest declares
  *packages*, not procedures.

`scripts/common/manifest.py` is the shared reader both import.

---

## Manifest shape

Each feature has a source `type` and type-specific fields:

| `type` | Source | Example features |
|---|---|---|
| `apt` | Debian packages, suite-aware | `rtl-sdr`, `rtl-sdr-feed`, `rtl-sdr-diag` |
| `github-release` | release assets per arch | `graywolf`, `webssh` (ttyd) |
| `pypi` | wheels, per platform/python | `server` (Flask/gunicorn/psutil) |
| `url` | a versioned tarball/binary | `kiwix` |
| `data` | large one-time download, not bundled | `fcc`, `wikipedia` |

`apt` features are **suite-aware** and carry **capability gates**:

```jsonc
"rtl-sdr": {
  "type": "apt",
  "min_version": {
    "librtlsdr": { "min": "2.0", "reason": "RTL-SDR Blog V4 needs >= 2.0" }
  },
  "packages": {
    "common":   ["rtl-sdr", "libusb-1.0-0"],
    "by_suite": { "bookworm": ["librtlsdr0"], "trixie": ["librtlsdr2"] }
  },
  "suites": ["bookworm", "trixie"],
  "arches": ["arm64", "armhf", "amd64"],
  "resolved": {}        // builder writes resolved versions here (lockfile)
}
```

Note the runtime lib is **renamed across suites** (`librtlsdr0` → `librtlsdr2`),
which the `by_suite` map captures.

---

## Source resolution (the installer)

For each chosen feature, on the Pi:

1. **Detect internet + host suite up front.** Show status once — 🟡 *offline:
   installing from the bundle (built for `<suite>`)*, or 🟢 *online: newest
   packages will come from the internet*.
2. **Resolve each package** with `manifest.resolve_source(installed, apt_candidate,
   bundled)` — **newest version wins**, ties prefer apt (it matches the host
   suite). This is what stops a stale bundle from clobbering a newer apt version:
   - online → compare *installed* vs *apt candidate* vs *bundled*, take newest;
   - offline → use the bundle's **suite-matched** set; warn on suite mismatch.
3. **Enforce capability gates** with `manifest.check_min_version(...)`. If the
   best obtainable version is below the gate (e.g. bookworm can only offer
   `librtlsdr` 0.9.0, < 2.0), **warn explicitly** and point the user at apt
   backports or a newer OS — don't silently install something that won't work.
4. **Install, then run the feature's imperative steps** from `common/<feature>.py`.

---

## Manifest as lockfile

The builder writes the **resolved** version per suite × arch back into each
feature's `resolved` map when it vendors a package. That lets the installer
compare versions **offline** (no need to crack open every `.deb`), and gives a
reproducible record of exactly what a given bundle contains.

---

## Why not just bump `DEBIAN_SUITE` to trixie?

That moves the break, it doesn't fix it: a trixie bundle on a bookworm host would
try to install newer-libc packages onto an older OS (harder failure). A single
suite is fundamentally fragile. The durable fix is **suite-aware bundling**
(vendor bookworm *and* trixie) plus **newest-source-wins** online and
**capability gates** that refuse to pretend an obsolete package is fine.
