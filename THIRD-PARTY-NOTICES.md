# Third-Party Notices

OASIS itself is **MIT licensed** — see [LICENSE](LICENSE). This file inventories
everything *else*: the software, fonts, and data that OASIS vendors, bundles,
downloads, or reads, and the terms each one carries.

It exists because OASIS is offline-first. A normal web app links a CDN and the
licence question stays someone else's problem; OASIS **redistributes** — into
the git repo, into the USB bundle, onto the SD card — so the question is ours.

## How to read this

Each entry says **how it ships**, which determines what obligations attach:

| Marker | Meaning |
|---|---|
| **vendored** | Committed to this git repository. Redistributed by anyone who clones. |
| **bundled** | Not in git. Fetched by `scripts/create-oasis-offline.py` into the offline USB bundle. Redistributed by anyone who hands over the stick. |
| **apt** | Installed from the operating system's own repositories on the Pi. Not redistributed by us. |
| **fetched** | Downloaded by the operator at install time, or read from a directory another program owns. Not redistributed by us. |
| **not redistributed** | Deliberately excluded — gitignored, and never placed in the bundle. |

Licence fields are marked **UNVERIFIED** where the upstream terms could not be
confirmed from the artifact or from a statement in this repository. That is an
honest gap, not a claim that the component is unlicensed — see
[Open questions](#open-questions) at the end. **Nothing here is legal advice**;
if you redistribute OASIS commercially, verify the terms yourself.

---

## 1. Python dependencies

Wheels are fetched at bundle-build time into `server/wheels/` (gitignored, never
committed). Licences below were read from the wheel `METADATA` in a built bundle.

| Component | Used for | Upstream | Licence | Ships |
|---|---|---|---|---|
| Flask | The web framework the whole suite runs on | https://flask.palletsprojects.com | BSD-3-Clause | bundled |
| Werkzeug | WSGI layer under Flask | https://werkzeug.palletsprojects.com | BSD-3-Clause | bundled |
| Jinja2 | Templating (Flask transitive) | https://jinja.palletsprojects.com | BSD-3-Clause | bundled |
| MarkupSafe | Jinja2 transitive | https://github.com/pallets/markupsafe | BSD-3-Clause | bundled |
| itsdangerous | Signing (Flask transitive) | https://github.com/pallets/itsdangerous | BSD-3-Clause | bundled |
| click | CLI (Flask transitive) | https://click.palletsprojects.com | BSD-3-Clause | bundled |
| blinker | Signals (Flask transitive) | https://github.com/pallets-eco/blinker | MIT | bundled |
| colorama | ANSI colour on Windows | https://github.com/tartley/colorama | BSD-3-Clause | bundled |
| gunicorn | Production WSGI server on the Pi | https://gunicorn.org | MIT | bundled (not on Windows) |
| psutil | CPU, RAM, disk, and uptime for `/api/system` | https://github.com/giampaolo/psutil | BSD-3-Clause | bundled |
| Skyfield | Satellite pass prediction | https://rhodesmill.org/skyfield/ | MIT | bundled |
| sgp4 | SGP4 orbit propagator under Skyfield | https://github.com/brandon-rhodes/python-sgp4 | MIT | bundled |
| NumPy | Skyfield's compiled dependency | https://numpy.org | BSD-3-Clause | bundled |
| jplephem | Ephemeris reader (Skyfield transitive) | https://github.com/brandon-rhodes/python-jplephem | MIT | bundled |
| certifi | CA bundle (Skyfield transitive) | https://github.com/certifi/python-certifi | **MPL-2.0** | bundled |
| packaging | Build/version handling | https://github.com/pypa/packaging | Apache-2.0 OR BSD-2-Clause | bundled |
| piper-tts | Neural text-to-speech engine (opt-in) | https://github.com/OHF-voice/piper1-gpl | **GPL-3.0** | bundled, opt-in only |
| onnxruntime | Inference runtime under Piper | https://github.com/microsoft/onnxruntime | UNVERIFIED (MIT upstream) | bundled, opt-in only |
| rpi_ws281x | WS281x LEDs on the GeekPi case | https://github.com/jgarff/rpi_ws281x | UNVERIFIED | bundled, feature-local |
| CPython (embedded) | The portable Windows runtime | https://www.python.org | UNVERIFIED (PSF-2.0 upstream) | bundled (Windows profile) |

`certifi` is the only non-permissive PyPI dependency. MPL-2.0 is file-level
copyleft and imposes nothing on OASIS's own code.

## 2. Front-end libraries

All of these are **committed to this repository** — OASIS has no build step, so
browser libraries are vendored as plain files.

| Component | Used for | Upstream | Licence | Ships |
|---|---|---|---|---|
| MapLibre GL JS 4.7.1 | The map renderer (traffic, ADS-B, net log) | https://github.com/maplibre/maplibre-gl-js | BSD-3-Clause | vendored |
| PMTiles (pmtiles.js) | HTTP-range reads of `.pmtiles` basemaps | https://github.com/protomaps/PMTiles | UNVERIFIED (BSD-3-Clause upstream) | vendored |
| fflate | Decompression, bundled inside pmtiles.js | https://github.com/101arrowz/fflate | UNVERIFIED (MIT upstream) | vendored, transitive |
| pdf-lib 1.17.1 | Fills the FEMA ICS AcroForm PDFs in-browser | https://github.com/Hopding/pdf-lib | UNVERIFIED (MIT upstream) | vendored |
| tslib | Bundled inside pdf-lib | https://github.com/microsoft/tslib | Apache-2.0 (in-file banner) | vendored, transitive |
| satellite.js 5.0.0 | Browser-side SGP4 for the satellite view | https://github.com/shashwatak/satellite-js | MIT (© 2013 Shashwat Kandadai and UCSC) | vendored |
| Swagger UI | Renders GrayWolf's OpenAPI spec in its handbook | https://github.com/swagger-api/swagger-ui | Apache-2.0 (full text at `static/graywolf-handbook/vendor/swagger-ui/LICENSE`) | vendored |
| normalize.css 7.0.0 | Inlined in Swagger UI's stylesheet | https://github.com/necolas/normalize.css | MIT (in-file banner) | vendored, transitive |
| core-js | Inlined in the Swagger UI bundles | https://github.com/zloirock/core-js | UNVERIFIED (MIT upstream) | vendored, transitive |

Everything under `common/js/`, `css/`, `tools/`, `server/system/`, `index.html`,
and `oasis-dashboard/` is OASIS's own vanilla JavaScript. The dashboard itself
loads no third-party library at all.

## 3. Fonts

| Component | Used for | Upstream | Licence | Ships |
|---|---|---|---|---|
| Inconsolata | Monospace in both handbooks | https://github.com/googlefonts/Inconsolata | OFL-1.1 — full text vendored beside the font as `OFL.txt` | vendored |
| Roboto Mono | Monospace across the dashboard and pages | https://github.com/googlefonts/robotomono | UNVERIFIED (Apache-2.0 upstream) — **no licence file vendored** | vendored |
| Noto Emoji | Glyph coverage; Pi OS ships no emoji font | https://github.com/googlefonts/noto-emoji | UNVERIFIED (OFL-1.1 upstream) — **no licence file vendored** | vendored |
| Open Sans (SDF glyph PBFs) | Map label glyphs for MapLibre | https://github.com/googlefonts/opensans | UNVERIFIED — **no licence file vendored**. Open Sans changed licence at v3 (Apache-2.0 → OFL-1.1), so the answer depends on which build these came from | vendored |

Only codepoints 0–1023 of the Open Sans atlas are shipped, which is why map POI
labels were removed — glyphs outside that range blanked whole tiles.

## 4. System services and daemons

Installed from apt on the Pi; several are also vendored as `.deb` files into the
offline bundle so a from-scratch install needs no internet.

| Component | Used for | Upstream | Licence | Ships |
|---|---|---|---|---|
| libusb | USB access for RTL-SDR dongles | https://libusb.info | LGPL-2.1-or-later | bundled + apt |
| socat | Carries the RTL-SDR → GrayWolf audio feed | http://www.dest-unreach.org/socat/ | **GPL-2.0** | bundled + apt |
| tcpdump | Verifies the UDP audio feed | https://www.tcpdump.org | BSD-3-Clause | bundled + apt |
| libpcap | tcpdump dependency | https://www.tcpdump.org | BSD-3-Clause | bundled + apt |
| speech-dispatcher (+ audio plugins, espeak-ng backend) | Gives the browser's Web Speech API a voice — the fallback announcement path | https://github.com/brailcom/speechd | **GPL-2.0+ / LGPL-2.1+ / GFDL** | bundled + apt |
| espeak-ng | Fallback synthesiser; also Piper's phonemiser | https://github.com/espeak-ng/espeak-ng | **GPL-3.0** | bundled + apt |
| ttyd 1.7.7 | The browser terminal on :7681 | https://github.com/tsl0922/ttyd | UNVERIFIED (MIT upstream) — redistributed binary carries no licence text | bundled |
| xterm.js | Embedded in the ttyd binary | https://github.com/xtermjs/xterm.js | UNVERIFIED (MIT upstream) | bundled, transitive |
| kiwix-tools (`kiwix-serve`) | Serves offline Wikipedia on :8081 | https://github.com/kiwix/kiwix-tools | UNVERIFIED (GPL-3.0 upstream) — tarball carries no licence file | bundled |
| gpsd, chrony | GPS-disciplined time | https://gpsd.io · https://chrony-project.org | UNVERIFIED | apt |
| NetworkManager, avahi | Wi-Fi AP fallback and `oasis.local` mDNS | — | UNVERIFIED | apt (already present on Pi OS) |
| python3-pil, python3-smbus, i2c-tools, python3-serial, device-tree-compiler, alsa-utils, ffmpeg, libhamlib | Feature-local dependencies (OLED panels, I²C HATs, mixer control, recording, rig control) | — | UNVERIFIED | apt |

## 5. Radio and SDR software

| Component | Used for | Upstream | Licence | Ships |
|---|---|---|---|---|
| rtl-sdr / librtlsdr | RTL-SDR driver, `rtl_fm`, `rtl_test` | https://gitea.osmocom.org/sdr/rtl-sdr | **GPL-2.0+** | bundled + apt |
| Direwolf | AX.25 packet modem behind Winlink RF and APRS decode | https://github.com/wb2osz/direwolf | **GPL-2.0+** | bundled + apt |
| multimon-ng | Bench AFSK1200 decoder (diagnostics) | https://github.com/EliasOenal/multimon-ng | **GPL-2.0+** | bundled + apt |
| SoX | Audio scaling for the SDR feed; satellite recording | https://sox.sourceforge.net | UNVERIFIED (GPL-2.0/LGPL upstream) | apt |
| dump1090-fa | Mode S / ADS-B decoder | https://github.com/flightaware/dump1090 | **GPL-2.0+** | bundled + FlightAware apt repo |
| GrayWolf | The APRS TNC / iGate / digipeater engine (:8080) | https://github.com/chrissnell/graywolf | UNVERIFIED — redistributed `.deb` carries no copyright file | bundled |
| Pat | Winlink client and web UI (:8082) | https://github.com/la5nta/pat · https://getpat.io | UNVERIFIED (MIT upstream) — redistributed `.deb` carries no copyright file | bundled |
| OpenWebRX+ | Browser SDR receiver (:8073), off by default | https://luarvique.github.io/ppa · https://www.openwebrx.de/ | UNVERIFIED — upstream OpenWebRX is AGPL-3.0; the fork's terms need confirming | **fetched** — never vendored, needs internet |
| go-pmtiles | MBTiles → PMTiles conversion and region extraction | https://github.com/protomaps/go-pmtiles | UNVERIFIED (BSD-3-Clause upstream) | bundled |

### GPL in the offline bundle

Several bundled components are GPL while OASIS is MIT. This is **aggregation,
not derivation** — each runs as its own process or is invoked through a CLI, and
OASIS links none of them into its own address space. But the GPL's source
obligation attaches to *the bundle* for anyone who redistributes one. If you hand
someone a USB stick containing these `.deb` files, you owe them the corresponding
source, which is available from each project's upstream above. The written-offer
pattern in [`overlays/SOURCE.md`](overlays/SOURCE.md) is the model.

## 6. Firmware and device-tree overlays

| Component | Used for | Upstream | Licence | Ships |
|---|---|---|---|---|
| `draws.dtbo`, `udrc.dtbo` | DRAWS HAT bring-up — the stock overlays fail on recent kernels | https://github.com/raspberrypi/linux (built unmodified from the kernel DT sources) | **GPL-2.0, not MIT** | vendored |
| `m5stack-cm4.dtbo` | M5Stack CM4Stack LCD panel — Pi OS ships no such overlay | https://github.com/m5stack/m5stack-linux-dtoverlays | **UNRESOLVED** — redistributed as published, unmodified; OASIS asserts nothing about its terms | vendored |
| Stock Pi OS overlays (`i2c-rtc`, `pps-gpio`, `vc4-kms-dsi-7inch`, `audioinjector-wm8731-audio`, …) | RTC, GPS PPS, DSI panel, DRA-Pi codec | Raspberry Pi OS kernel tree | GPL-2.0 upstream | **not redistributed** — only `dtoverlay=` lines are written to `config.txt` |

[`overlays/SOURCE.md`](overlays/SOURCE.md) carries the provenance, the exact
rebuild recipe, and a written offer for the corresponding source of the GPL
overlays. Deleting those files makes the installer fall back to whatever the OS
provides.

## 7. Voice and speech

| Component | Used for | Upstream | Licence | Ships |
|---|---|---|---|---|
| Piper | The neural TTS engine | https://github.com/OHF-voice/piper1-gpl | **GPL-3.0** | bundled, opt-in — runs as a subprocess per request, never linked in-process |
| **Jenny (Dioco)** — `en_GB-jenny_dioco-medium` | The voice itself | Dataset: https://github.com/dioco-group/jenny-tts-dataset · model: https://huggingface.co/rhasspy/piper-voices | **Custom attribution licence — explicitly NOT CC-BY** | **not redistributed** — OASIS ships neither the model nor the config; the operator's own bundle build downloads them |
| `oasis-dashboard/jenny.mp4` | The kiosk avatar card's face | Generated with Midjourney by the OASIS maintainer from a generic prompt | **OASIS-original, MIT** — see note below | vendored |

> ### The avatar is synthetic
>
> `jenny.mp4` is an **AI-generated image sequence** (Midjourney, generic prompt),
> created for this project. It depicts **no real person**, is not derived from
> any individual's likeness, and carries no third-party rights. The look is a
> nod to the genie of the lamp — the *oasis* the project is named for — not to
> anyone living or dead.
>
> It shares a name with the Jenny *voice* only because they arrive together on
> the kiosk. They are unrelated works: the voice is Dioco's and carries the
> attribution obligation described above; the face is the maintainer's own and
> is MIT like the rest of OASIS.

> ### Jenny attribution
>
> The Jenny voice carries a custom licence, and it is easy to get wrong. It
> imposes **no** licence-URL requirement and **no** statement-of-changes
> requirement — but it **does** impose a naming one:
>
> **Any software or interface that generates audio from this voice must credit
> it as "Jenny", and where at all practical as "Jenny (Dioco)".**
>
> That obligation binds OASIS itself, not just the bundle, because OASIS *is* an
> interface that generates audio on operator action. It is discharged in three
> places: the installer writes `ATTRIBUTION.txt` beside the model so the credit
> travels onto any SD card; the Setup page names `Piper, voice "Jenny (Dioco)"`
> on the checkbox; and [docs/SETUP.md](docs/SETUP.md#speech-piper-voice) states
> it. The canonical text lives in `features/speech/install.py` — reuse it
> verbatim rather than paraphrasing.
>
> No attribution is required when distributing generated audio clips. Commercial
> use is permitted. Dioco claims no ownership of generated audio.

## 8. Data sources

| Component | Used for | Upstream | Licence | Ships |
|---|---|---|---|---|
| **OpenStreetMap** | The offline vector basemap | https://www.openstreetmap.org · https://build.protomaps.com | **ODbL** — share-alike | **not redistributed** (`*.pmtiles`/`*.mbtiles` gitignored); credited on every map |
| OpenMapTiles schema | The tile schema GrayWolf's basemaps use | https://openmaptiles.org | UNVERIFIED — own terms, distinct from ODbL | fetched (read from GrayWolf's tile store) |
| **FCC ULS amateur database** | Offline callsign / name / grid lookup | https://data.fcc.gov/download/pub/uls/complete/l_amat.zip | UNVERIFIED — US federal data; check FCC ULS terms | fetched at install; may be bundled. **Gitignored — the raw dump carries licensee names and addresses and must never be committed** |
| **GeoNames** US postal codes | ZIP → Maidenhead grid conversion | https://download.geonames.org/export/zip/ | **CC BY 4.0** — attribution required | fetched at install |
| **RepeaterBook** CSV exports | Local repeater directory | https://www.repeaterbook.com | **Not redistributable** | **not redistributed** — gitignored; every operator exports their own |
| **SatNOGS** transmitter database | Satellite downlink frequencies and modes | https://db.satnogs.org | **CC BY-SA** — share-alike | the derived roster is baked into the bundle |
| **CelesTrak** TLEs | Orbital elements for pass prediction | https://celestrak.org | UNVERIFIED — check CelesTrak's terms of use | baked into the bundle; goes stale within days |
| **APRS symbol icons** | Station symbols on every map and panel | https://github.com/hessu/aprs-symbols (Heikki Hannikainen, OH7LZB · aprs.fi) | **CC BY 2.0** — attribution required | **vendored** |
| **OpenFlights** airline data | ICAO → telephony callsign for ADS-B | https://github.com/jpatokal/openflights | **ODbL** — share-alike | generated table is vendored |
| **FEMA ICS forms** 205 / 213 / 214 / 309 | The official AcroForm PDF templates | FEMA — *ICS Forms Booklet* (FEMA 502-2) | UNVERIFIED — US Government works are generally uncopyrightable under 17 U.S.C. §105, but no source URL is recorded | vendored (twice: the PDF, and the same bytes base64-encoded into the template JS) |
| **Natural Earth** 110m coastline and countries | Land outlines on the satellite view | https://www.naturalearthdata.com | **Public domain** | vendored |
| **Wikipedia** ZIM snapshots | Offline encyclopaedia via Kiwix | https://download.kiwix.org/zim/wikipedia | UNVERIFIED — Wikipedia content is CC BY-SA / GFDL | **not redistributed** (`*.zim` gitignored); may be bundled by the operator |
| Radio manuals | Manufacturer PDFs in the file browser | operator-supplied | **Copyright the respective manufacturers** | **not redistributed** — gitignored |
| ITU prefix table | Callsign-series reference | ITU Article 19, presented per ARRL | UNVERIFIED | vendored |
| US band plan data | Offline band-plan reference | FCC Part 97 allocations; presentation tracks the ARRL band plan | UNVERIFIED | vendored |
| `maps/us-states.geojson` | State-name → bounding box for region extraction | UNVERIFIED — no provenance recorded | UNVERIFIED | vendored |
| `maps/traffic/assets/APRS-Symbols.pdf` | The printable APRS symbol chart offered from the hazard catalog | Believed to be the hessu/aprs-symbols chart — provenance not recorded | UNVERIFIED | vendored |
| HAMQSL solar data | Propagation numbers over Winlink/SailDocs | http://hamqsl.com | UNVERIFIED | fetched by the operator |
| u-blox AssistNow Offline | Faster GPS cold fixes, strictly opt-in | https://www.u-blox.com | UNVERIFIED — service terms; needs the operator's own token | fetched by the operator |
| dsame3 `defs.py` | SAME/EAS event-code, originator and FIPS county name tables | https://github.com/jamieden/dsame3 | ISC | vendored |

dsame3 is James Kitchens' 2023 fork of Joseph W. Metcalf's `cuppa-joe/dsame`.
Only `defs.py` is vendored, at `services/nwr/vendor/dsame3/`, with `license.txt`
verbatim beside it. ISC requires the copyright and permission notice to travel
with every copy, including a partial one.

### Attribution that must stay visible

Three of these carry live attribution obligations:

- **OpenStreetMap (ODbL)** — the on-map credit (`© OpenStreetMap · OpenMapTiles
  · GrayWolf`) is a licensing requirement. Do not remove it from the map chrome.
- **APRS symbols (CC BY 2.0)** — credited to Heikki Hannikainen, OH7LZB in the
  README and here.
- **GeoNames (CC BY 4.0)** and **SatNOGS (CC BY-SA)** — credited here and in the
  README.

## 9. Concept and origin credit

No code or data was copied from these; they are acknowledged because OASIS would
not exist in this shape without them.

- **Jason, KM4ACK** — the *ACK Off-Grid Ham Radio Server*. The original idea of a
  fully offline, browser-accessible ham toolkit on a Raspberry Pi is his.
  https://github.com/km4ack
- **smittix** — *intercept*, which inspired the ADS-B and 433 MHz feature
  concepts. https://github.com/smittix/intercept

---

## Open questions

Honesty over tidiness: these are the items whose terms are **not** settled. They
are listed so a downstream redistributor can make their own call, and so they can
be closed out over time.

1. **`overlays/m5stack-cm4.dtbo`** — a committed binary whose upstream terms this
   repository explicitly declines to assert. Confirm M5Stack's terms before
   relying on redistributing it.
2. **Redistributed binaries with no licence text attached** — GrayWolf, Pat,
   ttyd, and kiwix-tools are all shipped in the offline bundle without an
   upstream `LICENSE` or Debian `copyright` file travelling with them. Their
   upstream terms are believed permissive or GPL respectively, but should be
   recorded from source.
3. **Vendored libraries missing their notice** — BSD-3-Clause and Apache-2.0 both
   require reproducing the licence text. MapLibre links its licence rather than
   vendoring it; Swagger UI's bundles reference two `*.LICENSE.txt` files that
   are not present; pmtiles.js and pdf-lib carry no header for their own code.
4. **Fonts missing their notice** — Roboto Mono, Noto Emoji, and the Open Sans
   glyph atlas are committed with no licence file. Inconsolata does this
   correctly (`OFL.txt` beside the font); the others should mirror it.
5. **`maps/us-states.geojson`** and **`maps/traffic/assets/APRS-Symbols.pdf`** —
   provenance unrecorded. The symbol chart is very likely the hessu/aprs-symbols
   chart already credited above, but that is inference, not a record.
6. **FEMA ICS PDFs** — no source URL or public-domain basis is asserted in the
   repository, and three of the four template files do not name FEMA.
7. **Reference data** — the band plan, radio cards, repeater guide, and quick-ref
   tables cite no source. The underlying allocations and procedures are facts,
   but a specific chart's presentation may not be.
8. **apt dependencies outside the manifest** — `gpsd`, `chrony`, `python3-pil`,
   `i2c-tools`, `ffmpeg`, `libhamlib` and others are hard-coded in feature
   installers rather than declared in `scripts/offline-manifest.json`, so they
   are invisible to any generated inventory.

Corrections are welcome — open an issue. If you are the author of something
listed here and the attribution is wrong or missing, please say so and it will
be fixed promptly.
