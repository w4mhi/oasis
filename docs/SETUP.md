# OASIS — Setup & Configuration Guide

This document covers everything needed to deploy, configure, and maintain OASIS. For an overview of features see the [README](../README.md).

---

## Contents

- [Project structure](#project-structure)
- [Server setup](#server-setup)
- [FCC Callsign Lookup](#fcc-callsign-lookup)
- [Offline Maps](#offline-maps)
- [GrayWolf APRS](#graywolf-aprs)
- [Kiwix / Wikipedia](#kiwix--wikipedia)
- [ICS Forms](#ics-forms)
- [USB / Portable bundle](#usb--portable-bundle)
- [Keeping data fresh](#keeping-data-fresh)

---

## Project structure

```
oasis-emcomm/
├── index.html                  ← Dashboard (start here)
├── css/common.css              ← Shared design system
│
├── server/                     ← Flask web server (port 8083)
│   ├── app.py
│   ├── lookup.py               ← FCC binary-search engine
│   ├── maidenhead.py           ← ZIP → grid square
│   ├── map-assets/             ← MapLibre GL, PMTiles plugin, fonts
│   ├── templates/              ← Flask HTML templates
│   └── wheels/                 ← Vendored Flask + gunicorn wheels
│
├── fcc-offline-database/
│   └── data/                   ← EN.dat, HD.dat, EN.idx, zipcodes.csv
│
├── maps/                       ← PMTiles files + map.html
├── aprs/                       ← APRS map page
├── scripts/                    ← Setup and install scripts
│
├── static/
│   ├── ics-205/                ← ICS 205 Radio Communications Plan
│   ├── ics-213/                ← ICS 213 General Message
│   ├── ics-214/                ← ICS 214 Activity Log
│   ├── ics-309/                ← ICS 309 Communications Log
│   ├── band-plan/              ← U.S. amateur band plan
│   ├── cheatsheets/            ← Radio quick-reference cards
│   ├── graywolf-handbook/      ← GrayWolf offline handbook
│   └── radio-ref/              ← Q-codes, phonetics, pro-words, RST, ITU
│
├── radio-cards/                ← Per-radio operation cards
├── radio-manuals/              ← PDF manuals
├── repeater-guide/             ← Repeater programming with CHIRP
├── tools/                      ← Antenna calc, grid/bearing, net logger
├── system/                     ← Dashboard, file browser
├── winlink/                    ← Winlink tools
└── docs/                       ← This file and other documentation
```

---

## Server setup

`scripts/setup-server.py` creates `.venv` and installs dependencies.

```bash
python3 scripts/setup-server.py           # Flask + gunicorn offline, psutil from PyPI
python3 scripts/setup-server.py --offline # wheels only, no PyPI
```

Flask and gunicorn are installed from `server/wheels/` (no internet needed for those). psutil is fetched from PyPI (~2 MB) for CPU/RAM/disk stats on the dashboard.

**To run the server:**

```bash
# Development
source .venv/bin/activate
python3 server/app.py

# Production (recommended for always-on Pi)
source .venv/bin/activate
gunicorn --workers 1 --bind 0.0.0.0:8083 server.app:app
```

**systemd service (start on boot):**

Create `/etc/systemd/system/oasis.service`:

```ini
[Unit]
Description=OASIS suite server
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/oasis-emcomm
ExecStart=/home/pi/oasis-emcomm/.venv/bin/gunicorn --workers 1 --bind 0.0.0.0:8083 server.app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now oasis
```

**Server routes:**

| Route | Description |
|---|---|
| `GET /` | Dashboard |
| `GET /lookup` | FCC callsign lookup page |
| `GET /api/lookup?callsign=W7XYZ` | JSON callsign result |
| `GET /maps/<file>.pmtiles` | PMTiles with HTTP Range support |
| `GET /map-assets/<path>` | MapLibre GL JS/CSS, fonts |
| `GET /health` | JSON health check |
| `GET /server-ports.json` | Port map consumed by dashboard JS |

---

## FCC Callsign Lookup

Offline lookup of U.S. amateur licenses by callsign. No database engine — binary-search over FCC flat files. Lookups return callsign, name, city, state, and Maidenhead grid square (derived from ZIP centroid).

**Setup (internet-connected machine, one-time):**

```bash
# Download EN.dat + HD.dat (~160 MB) and build index:
python3 scripts/setup-fcc-database.py

# Also build the full 40 000-entry ZIP→grid table:
python3 scripts/setup-fcc-database.py --full-zip

# Re-index only (after manual data update):
python3 scripts/setup-fcc-database.py --index-only
```

Files written to `fcc-offline-database/data/`:

| File | Description |
|---|---|
| `EN.dat` | FCC entity records (~200 MB) — gitignored |
| `HD.dat` | FCC license headers (~120 MB) — gitignored |
| `EN.idx` | Binary-search index — gitignored |
| `zipcodes.csv` | ZIP → lat/lon — tracked in git (sample ships with repo) |

**How it works:** `HD.dat` identifies active licenses. `EN.dat` holds the entity records. A binary-search index over call signs points directly to byte offsets in `EN.dat` so lookups are sub-millisecond without loading the full file. Only active licenses are indexed — expired and cancelled records are excluded.

**Copy to Pi:**

```bash
scp -r fcc-offline-database/data pi@raspberrypi.local:/home/pi/oasis-emcomm/fcc-offline-database/
```

---

## Offline Maps

Vector tile maps built from OpenStreetMap data, rendered in the browser with MapLibre GL. No tiles are fetched from the internet at runtime — everything is served from the Pi.

```
Prep machine (internet, one-time)
  └─ BBBike GPKG (OSM extract)
       ├─ ogr2ogr → lines.geojson
       ├─ ogr2ogr → multipolygons.geojson
       ├─ ogr2ogr → points.geojson
       └─ tippecanoe → <area>.pmtiles
            └─ drop into maps/

Pi (no internet)
  └─ server/app.py :8083
       └─ GET /maps/*.pmtiles  (HTTP Range / 206)
            └─ maps/map.html  ← MapLibre GL renders in browser
```

### Prerequisites

On the prep machine (macOS):

```bash
brew install gdal tippecanoe
```

On Linux, `gdal-bin` is available via apt; tippecanoe must be built from source. Nothing extra needed on the Pi.

### Using the build script

`scripts/build-map.py` wraps the full pipeline into one command:

```bash
python3 scripts/build-map.py planet_-122.603_47.241_391b4027.gpkg \
  --bbox -122.17 47.47 -121.93 47.58 \
  --name issaquah \
  --center -122.0321 47.5301 \
  --zoom 13
```

This runs ogr2ogr (3 layers), tippecanoe, copies the PMTiles file to `maps/`, and updates `maps/map-config.json`. Intermediate GeoJSON files are written to a temp directory and cleaned up automatically.

```bash
# Cap zoom range to save space on a Pi SD card:
python3 scripts/build-map.py <file>.gpkg \
  --bbox WEST SOUTH EAST NORTH \
  --name <area> \
  --min-zoom 5 --max-zoom 15

# Run ogr2ogr only, skip tippecanoe:
python3 scripts/build-map.py <file>.gpkg --bbox ... --name <area> --geojson-only
```

If `--center` is omitted the script calculates it from the midpoint of the bbox.

### Step 1 — Download a GPKG extract

Go to [BBBike Extract Service](https://extract.bbbike.org/), draw your area, select **Geopackage (GPKG)**, submit, and download the file.

**Layers in a BBBike GPKG:**

| Layer | Type | Use | Action |
|---|---|---|---|
| `lines` | LineString | Roads, waterways | Convert — clip first, extends beyond bbox |
| `multipolygons` | MultiPolygon | Buildings, landuse, water, parks | Convert |
| `points` | Point | POIs, place names | Convert — clip first, extends beyond bbox |
| `multilinestrings` | MultiLineString | Bus routes, minor relations | Skip |
| `other_relations` | GeometryCollection | Mixed OSM relations | Skip — tippecanoe can't handle |

### Step 2 — Clip and convert to GeoJSON

Always clip with `-clipsrc`. The `lines` and `points` layers often extend far outside the download bbox.

```bash
GPKG="planet_<lat>_<lon>_<hash>.gpkg"
WEST=-122.75; SOUTH=47.15; EAST=-121.40; NORTH=47.85

ogr2ogr -f GeoJSON -t_srs EPSG:4326 -clipsrc $WEST $SOUTH $EAST $NORTH lines.geojson "$GPKG" lines
ogr2ogr -f GeoJSON -t_srs EPSG:4326 -clipsrc $WEST $SOUTH $EAST $NORTH multipolygons.geojson "$GPKG" multipolygons
ogr2ogr -f GeoJSON -t_srs EPSG:4326 -clipsrc $WEST $SOUTH $EAST $NORTH points.geojson "$GPKG" points
```

Each conversion takes 30 s – 2 min depending on machine speed.

### Step 3 — Build PMTiles

```bash
tippecanoe -o <area>.pmtiles -zg --drop-densest-as-needed --force \
  lines.geojson multipolygons.geojson points.geojson
```

**To reduce file size on a storage-constrained Pi** (skip low-zoom tiles, cap at street detail):

```bash
tippecanoe -o <area>.pmtiles -Z 5 -z 15 --drop-densest-as-needed --force \
  lines.geojson multipolygons.geojson points.geojson
```

### Step 4 — Register and deploy

```bash
cp <area>.pmtiles /path/to/oasis-emcomm/maps/
```

Add an entry to `maps/map-config.json`:

```json
{ "<area>.pmtiles": { "center": [<lon>, <lat>], "zoom": 12 } }
```

```bash
scp maps/<area>.pmtiles pi@raspberrypi.local:/home/pi/oasis-emcomm/maps/
scp maps/map-config.json pi@raspberrypi.local:/home/pi/oasis-emcomm/maps/
```

### Refreshing JS libraries

MapLibre GL and PMTiles are bundled in `server/map-assets/`. To update:

```bash
cd server/map-assets
curl -L -o maplibre-gl.js  https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js
curl -L -o maplibre-gl.css https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css
curl -L -o pmtiles.js      https://cdn.jsdelivr.net/npm/pmtiles@3.2.1/dist/pmtiles.js
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Map stuck on "Loading…" | JS libraries missing or wrong path | Check `server/map-assets/` has real files |
| No roads visible | Layer name mismatch | Check browser console diagnostics panel |
| PMTiles file > 1 GB | Lines/points not clipped | Re-run ogr2ogr with `-clipsrc` |
| tippecanoe crashes | `other_relations` layer included | Already excluded in commands above |
| No buildings at low zoom | `--drop-densest-as-needed` culled them | Zoom past z14 |
| Flask 416 error | Bad Range header | Verify `/maps/` route in `server/app.py` |
| Labels/fonts missing | Glyph PBF files absent | Check `server/map-assets/fonts/` |

---

## GrayWolf APRS

[GrayWolf](https://github.com/chrissnell/graywolf) is a browser-based APRS TNC, iGate, and digipeater that runs on port 8080.

```bash
python3 scripts/install-graywolf.py

# Pin a version:
python3 scripts/install-graywolf.py --version 0.13.16
```

The script detects architecture (aarch64, armv7l, armv6l, x86_64), downloads the matching `.deb` from GitHub releases, installs it with apt, and enables the systemd service. After install, open `http://<pi-ip>:8080` to configure GrayWolf.

### 1. Station Callsign

Go to **Settings → Station Callsign** and enter your callsign with SSID (e.g. `W4MHI-5`).

### 2. Channels

Go to **Settings → Channels → Add Channel**. Configure a modem-backed VHF APRS channel:

- **Modem Type:** AFSK
- **Bit Rate:** 1200 / **Mark:** 1200 Hz / **Space:** 2200 Hz
- **Input / Output Device:** select your sound card (configured in Audio Devices)
- **TX Delay:** 300 ms — key-up time before sending

![GrayWolf — Edit Channel (VHF APRS)](images/file9.png)

After saving, the channel card shows the backing status and modem parameters at a glance:

![GrayWolf — Channels list (VHF APRS configured)](images/file10.png)

### 3. PTT

Go to **Settings → PTT → Add PTT**. Use **Detect Devices** — GrayWolf will recommend the best match for your hardware. For a CM108-based cable (AIOC, Digirig, etc.), select the `hidraw` device:

- **Device:** `/dev/hidraw1` (CM108) or a GPIO pin for a direct Pi connection
- **GPIO Pin:** GPIO 3 (pin 13) when using CM108 GPIO mode

![GrayWolf — PTT Configuration](images/file23.png)

### 4. Audio Devices

Go to **Settings → Audio Devices → Add Device**. Add your sound card for both input (RX) and output (TX). Use **Detect Devices** to find the ALSA path.

- **Sample Rate:** 96000 Hz, **Channels:** Mono
- Adjust LEVEL and GAIN sliders after a test transmission

![GrayWolf — Audio Devices configured](images/file5.png)

### 5. GPS (optional)

Go to **Settings → GPS → Configure GPS** if you want position beacons to follow a GPS receiver rather than a fixed coordinate. Use **Detect Devices** to find the serial port:

![GrayWolf — GPS configuration (no GPS attached)](images/file13.png)

Without GPS, set a fixed position directly on the Beacon (step 6 below).

### 6. Beacons

Go to **Operations → Beacons → Add Beacon**:

- **Channel:** select the VHF APRS channel you created
- **Position source:** Use fixed coordinates (enter your lat/lon) or GPS
- **Path:** `WIDE1-1,WIDE2-1` for normal digipeating
- **Symbol:** choose from the APRS symbol palette
- **Interval:** 300 s (5 min) is a safe default for a fixed station

![GrayWolf — Edit Beacon](images/file6.png)

The symbol picker lets you select from primary and alternate APRS symbol tables with an optional overlay character:

![GrayWolf — APRS Symbol picker](images/file7.png)

**SmartBeaconing** (optional) — adjusts beacon rate based on speed for mobile use:

![GrayWolf — Beacons with SmartBeaconing settings](images/file8.png)

### 7. Digipeater (optional)

Go to **Operations → Digipeater**. Enable and add rules under **Digipeater Rules → Add Rule**:

- **Callsign:** use station callsign or a dedicated digi callsign
- **Dedupe window:** 30 s (APRS convention — drops duplicate packets heard within this window)
- Add a **Wide-area digi** rule (Repeat action) for standard `WIDEn-N` path digipeating

![GrayWolf — Digipeater settings and rules](images/file12.png)

### 8. iGate (optional)

Go to **Operations → iGate**. iGating requires internet access on the Pi.

**Connection tab** — enable iGate and set the APRS-IS server:

![GrayWolf — iGate Connection tab](images/file15.png)

**RF → APRS-IS tab** — enter your callsign, APRS passcode, and a server-side filter (e.g. `r/47.6/-122.0/100` for a 100 km radius around Seattle):

![GrayWolf — iGate RF → APRS-IS tab](images/file16.png)

**APRS-IS Feed & TX Rules tab** — control which IS packets are re-transmitted on RF. Add the most specific rules you can; broad wildcards can flood the local frequency:

![GrayWolf — iGate APRS-IS Feed & TX Rules](images/file14.png)

### 9. KISS Interface (optional)

Go to **Interfaces → KISS** to expose a KISS TNC endpoint for third-party software (Winlink, Xastir, etc.):

![GrayWolf — KISS Interfaces (empty)](images/file17.png)

### 10. AGW Interface (optional)

Go to **Interfaces → AGW** to enable the AGWPE-compatible interface for software that requires AGW protocol on port 8000:

![GrayWolf — AGW Interface](images/file4.png)

### 11. Actions (optional)

Actions execute shell commands or call webhooks when authorized APRS messages arrive addressed to your station. Go to **Actions → New Action**:

![GrayWolf — Actions page with WEATHER action](images/file3.png)

Configure the command path, arguments, and an optional OTP credential for sender authentication:

![GrayWolf — Edit Action: WEATHER](images/file0.png)

Use the **Test** button to fire the action directly from the UI without sending an APRS message:

![GrayWolf — Test action dialog](images/file2.png)

OTP credentials use TOTP/SHA1 (6-digit, 30 s period). Add a new credential under **OTP Credentials → New Credential**:

![GrayWolf — New OTP Credential](images/file1.png)

### Live operation

Once configured, the **Dashboard** shows channel status, audio level, packet RX/TX counts, iGated count, uptime, and a live packet feed:

![GrayWolf — Dashboard (live station overview)](images/file11.png)

The **Live Map** plots stations heard over RF and via iGate with layer toggles and a time-range filter:

![GrayWolf — Live Map](images/file18.png)

**Logs** provides a searchable, filterable view of every packet with type badges (position, weather, mic-e) and RF/iGate annotations. Export to CSV for offline analysis:

![GrayWolf — Packet Logs](images/file19.png)

**Messages** supports direct APRS messaging and tactical group chats. Send to any callsign or create a tactical group and invite stations by callsign:

![GrayWolf — Messages (direct APRS messaging)](images/file20.png)

![GrayWolf — Tactical chat with Invite dialog](images/file21.png)

![GrayWolf — Tactical chat in use](images/file22.png)

---

## Kiwix / Wikipedia

Kiwix serves offline snapshots of Wikipedia and other content through a local web server on port 8081.

```bash
python3 scripts/install-kiwix.py

# Pin a version:
python3 scripts/install-kiwix.py --version 3.8.2

# Custom ZIM directory:
python3 scripts/install-kiwix.py --zim-dir /mnt/ssd/zim
```

**Download Wikipedia content:**

```bash
python3 scripts/download-wikipedia.py                   # interactive picker
python3 scripts/download-wikipedia.py --edition mini    # ~1 GB, top articles
python3 scripts/download-wikipedia.py --edition nopic   # ~21 GB, no images
python3 scripts/download-wikipedia.py --edition maxi    # ~100 GB, full with images
python3 scripts/download-wikipedia.py --list            # list available editions
```

| Edition | Size | Notes |
|---|---|---|
| `mini` | ~1 GB | Fits on a 32 GB SD card |
| `top25k` | ~1 GB | Top 25 000 articles with images |
| `nopic` | ~21 GB | Full English Wikipedia, no images |
| `maxi` | ~100 GB | Full with images — SSD required |

---

## ICS Forms

All four forms support: PDF export (fills official FEMA AcroForm templates), CSV import/export, print-optimized layout, and auto-save to localStorage. No server required — open the HTML file directly in any browser.

**pdf-lib dependency** — download once (needs internet, ~1 MB):

```bash
curl -L -o static/ics-205/dependencies/pdf-lib.min.js https://unpkg.com/pdf-lib@1.17.1/dist/pdf-lib.min.js
# repeat for ics-213, ics-214, ics-309
```

Or run the per-form `install.sh` / `install.bat`.

| Form | Path | Notes |
|---|---|---|
| ICS 205 | `static/ics-205/ics-205.html` | 8-row channel table; CHIRP CSV import locks freq/tone/mode fields |
| ICS 213 | `static/ics-213/ics-213.html` | Two-part form — originator blocks 1–8, reply blocks 9–10 |
| ICS 214 | `static/ics-214/ics-214.html` | Dynamic resource + activity rows; PDF limit 8 resource / 60 activity rows |
| ICS 309 | `static/ics-309/ics-309.html` | Time auto-fills on row click; PDF limit 29 rows |

**Updating a PDF template** when FEMA issues a new version:

1. Replace the `.pdf` file in the form folder.
2. Run `convert-template.sh` (Linux/macOS) or `convert-template.bat` (Windows) to regenerate the base64 `*-template.js` file.

---

## USB / Portable bundle

`scripts/build-usb.py` packages OASIS into a self-contained folder that runs on Windows and Linux with no Python pre-installed. Can be built from macOS, Linux, or Windows.

```bash
python3 scripts/build-usb.py                           # builds usb-dist/ in repo root
python3 scripts/build-usb.py --out /Volumes/USB/oasis  # write directly to USB drive
python3 scripts/build-usb.py --skip-windows            # copy files only, skip Python download
```

**Tip:** run `python3 scripts/setup-fcc-database.py` before building so the FCC data is included. Without it, callsign lookups return "not found" on the USB copy.

**What it builds:**

```
usb-dist/
├── _runtime/
│   └── windows/       ← embedded Python 3.12 + Flask + psutil (~30 MB)
├── server/
├── static/
├── maps/
├── fcc-offline-database/
├── ... (all project files)
├── start.bat          ← Windows: double-click to launch
└── start.sh           ← Linux: bootstraps venv on first run
```

GrayWolf, Kiwix, APRS stats, and the tile server show as DOWN on the USB bundle — those require the Pi services.

---

## Keeping data fresh

| Data | Command | Frequency |
|---|---|---|
| FCC callsign database | `python3 scripts/setup-fcc-database.py` | FCC publishes every Sunday |
| Offline maps | Re-run ogr2ogr + tippecanoe with a fresh BBBike download | As needed |
| Wikipedia ZIM | `python3 scripts/download-wikipedia.py --edition <edition>` | Monthly snapshots |
| GrayWolf | `python3 scripts/install-graywolf.py` | Check GitHub releases |
| kiwix-serve | `python3 scripts/install-kiwix.py --version <new>` | Check download.kiwix.org |
