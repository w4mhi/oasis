# OASIS — Setup & Configuration Guide

This document covers everything needed to deploy, configure, and maintain OASIS. For an overview of features see the [README](../README.md).

---

## Contents

- [Services index](#services-index)
- [Before you begin (Raspberry Pi)](#before-you-begin-raspberry-pi)
- [Project structure](#project-structure)
- [Guided setup (menu)](#guided-setup-menu)
- [Server setup](#server-setup)
- [FCC Callsign Lookup](#fcc-callsign-lookup)
- [Using OASIS in the field (no internet)](#using-oasis-in-the-field-no-internet)
- [Offline Maps](#offline-maps)
- [GrayWolf APRS](#graywolf-aprs)
- [Winlink (Pat)](#winlink-pat)
- [Winlink RF via DigiRig](#winlink-rf-via-digirig)
- [DRA-Pi-Zero sound card](#dra-pi-zero-sound-card)
- [DRA-Pi RX LED](#dra-pi-rx-led)
- [Kiwix / Wikipedia](#kiwix--wikipedia)
- [RTL-SDR](#rtl-sdr)
- [OpenWebRX (SIGINT)](#openwebrx-sigint)
- [ADS-B Aircraft](#adsb-aircraft)
- [Satellites](#satellites)
- [GPS time sync (gpsd + chrony)](#gps-time-sync-gpsd--chrony)
- [Hardware RTC (Witty Pi 3)](#hardware-rtc-witty-pi-3)
- [webssh / Browser Terminal](#webssh--browser-terminal)
- [Service controls (dashboard power buttons)](#service-controls-dashboard-power-buttons)
- [ICS Forms](#ics-forms)
- [Tools & calculators](#tools--calculators)
- [Reference library](#reference-library)
- [Repeater Book](#repeater-book)
- [File browser](#file-browser)
- [CM4Stack panel display](#cm4stack-panel-display)
- [RGB Cooling HAT](#rgb-cooling-hat)
- [OASIS Dashboard / kiosk display](#oasis-dashboard--kiosk-display)
- [USB / Portable bundle](#usb--portable-bundle)
- [Keeping data fresh](#keeping-data-fresh)
- [Updating OASIS](#updating-oasis)
- [Health check (doctor)](#health-check-doctor)
- [Factory reset / uninstall](#factory-reset--uninstall)
- [Known Limitations](#known-limitations)

---

## Services index

Every systemd service OASIS installs, and the unit-file path it lives at. The first column is the feature that needs the service; the second is the path to the `.service` unit. Manage any of them with `sudo systemctl {start|stop|status} <unit>`.

Units OASIS writes itself live under `/etc/systemd/system/`:

| Feature needing the service | Path to the service unit |
|---|---|
| OASIS web server (autostart on boot) | `/etc/systemd/system/oasis.service` |
| CM4Stack panel display | `/etc/systemd/system/oasis-panel.service` |
| GrayWolf API (dashboard feed) | `/etc/systemd/system/graywolf-api.service` |
| Winlink (Pat) | `/etc/systemd/system/pat.service` |
| Winlink Direwolf modem | `/etc/systemd/system/pat-direwolf.service` |
| Kiwix (Wikipedia server) | `/etc/systemd/system/kiwix.service` |
| webssh / browser terminal | `/etc/systemd/system/webssh.service` |
| RTL-SDR APRS feed | `/etc/systemd/system/aprs-sdr-feed.service` |
| ADS-B recorder + history API | `/etc/systemd/system/adsb-api.service` |
| RGB Cooling HAT | `/etc/systemd/system/rgb-cooling-hat.service` |
| DRA-Pi RX LED | `/etc/systemd/system/dra-rx-led.service` |
| Wi-Fi AP fallback | `/etc/systemd/system/oasis-netwatch.service` |

Package-provided units (installed via `apt`/`.deb`, so they live under `/lib/systemd/system/`):

| Feature needing the service | Path to the service unit |
|---|---|
| GrayWolf APRS | `/lib/systemd/system/graywolf.service` |
| OpenWebRX (SIGINT) | `/lib/systemd/system/openwebrx.service` |
| ADS-B decoder | `/lib/systemd/system/dump1090-fa.service` |
| GPS time sync — gpsd | `/lib/systemd/system/gpsd.service` |
| GPS time sync — chrony | `/lib/systemd/system/chrony.service` |

> Features without their own unit: FCC lookup, offline maps and ICS/tools are served by `oasis.service`; the DRA-Pi sound card (ALSA config) and the Witty Pi 3 RTC (device-tree overlay + `hwclock`) configure the OS directly and register no systemd service.

---

## Before you begin (Raspberry Pi)

If you are setting up a **fresh Raspberry Pi** for the first time, complete these steps before anything else. If you already have a Pi booted with SSH access and `git` installed, skip to [Guided setup](#guided-setup-menu).

### 1. Flash Raspberry Pi OS

Download and install **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)** on your laptop/desktop.

**Which OS version to pick:**

| Situation | Choose |
|---|---|
| Planning to use an **RTL-SDR dongle** for APRS receive | **Raspberry Pi OS Lite (64-bit) — Trixie** |
| Everything else (no RTL-SDR, or using DigiRig/DRA-Pi) | **Raspberry Pi OS Lite (64-bit) — Bookworm** |

**Lite vs. Desktop:** Lite has no graphical desktop — it boots to a command line and uses less memory (important on a Pi Zero 2 W or Pi 3). If you want to plug in a monitor and use a mouse/keyboard with a desktop environment, choose "Raspberry Pi OS (64-bit)" (without "Lite") instead. The kiosk/browser option (`--with-browser`) works on Desktop; on Lite, a browser can still run on a separate attached display if configured.

Use Imager to write your chosen image to a microSD card (32 GB minimum). Before clicking **Write**, open **Advanced Options** (gear icon ⚙️ or Ctrl+Shift+X) and:
- Set a **hostname** (e.g. `oasis`)
- Set a **username and password** (use your callsign or anything you'll remember — it does **not** have to be `pi`)
- Enable **SSH** — SSH lets you type commands on the Pi from your laptop's terminal, so you don't need a monitor. Use **password authentication** (the simpler of the two options).
- Enter your **Wi-Fi SSID and password** so the Pi connects on first boot

> 💡 Doing this in Imager's Advanced Options means the Pi is headless-ready on first boot — no monitor or keyboard needed.

### 2. Find the Pi on your network and connect

Insert the card, power on the Pi, wait ~60 seconds.

**Open a terminal on your laptop:**
- **Mac:** press Cmd+Space, type "Terminal", press Enter.
- **Windows:** press Start, search "PowerShell", press Enter. (`ssh` is built into Windows 10/11 — no extra software needed.)
- **Linux:** open your terminal application.

Then connect to the Pi:

```bash
ssh <username>@oasis.local        # replace <username> with what you set in Imager
# e.g.  ssh w4mhi@oasis.local
```

The first time you connect, SSH shows a "host key fingerprint" and asks if you want to continue — this is your laptop verifying it's talking to the right Pi (normal, expected). Type `yes` and press Enter, then enter your password. You're now typing commands directly on the Pi.

If `oasis.local` doesn't resolve, find the IP address instead:
```bash
# from a Mac/Linux laptop on the same network:
arp -a | grep -i raspberry
# or from the Pi itself once logged in:
hostname -I
```

Write it down — you'll need it throughout setup (`http://<ip>:8083` to open OASIS).

### 3. Install git

Git is not installed on Raspberry Pi OS Lite by default. This step requires the Pi to have internet access — confirm your Wi-Fi credentials were entered correctly in Imager's Advanced Options before running:

```bash
sudo apt update && sudo apt install -y git
```

### 4. Clone OASIS

```bash
git clone https://github.com/W4MHI/oasis-emcomm
cd oasis-emcomm
```

> 💡 **Stay in this directory.** Every command in this guide assumes you are inside `oasis-emcomm/`. If you open a new terminal session or accidentally `cd` somewhere else, type `cd ~/oasis-emcomm` to get back.

Now continue to [Guided setup](#guided-setup-menu).

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
│   ├── map-assets/             ← MapLibre GL JS/CSS, fonts
│   ├── templates/              ← Flask HTML templates
│   ├── wheels/                 ← Vendored wheels: Flask, gunicorn, MarkupSafe,
│   │                              psutil — all platforms × Python 3.9–3.14
│   ├── winlink/                ← Winlink tools
│   ├── tools/                  ← Antenna calc, grid/bearing, net logger
│   └── system/                 ← Dashboard verify, file browser
│
├── common/                     ← Shared code + vendored front-end assets
│   ├── js/                     ← Shared JS modules (units, geo, format, adsb)
│   └── dependencies/           ← Vendored pdf-lib.min.js + glyph fonts
│
├── services/fcc_database/
│   └── data/                   ← EN.dat/HD.dat/EN.idx/zipcodes.csv
│                                  all generated by services/fcc_database/install.py (gitignored)
│
├── maps/                       ← map.html + us-states.geojson; *.pmtiles placed here
│                                  (tiles are gitignored, not tracked)
├── aprs/                       ← APRS map page
├── scripts/                    ← Setup, build, and install scripts (see README)
│                                  (enable-graywolf-api.py = APRS history API, port 8085)
│
├── static/
│   ├── ics-205/                ← ICS 205 Radio Communications Plan
│   ├── ics-213/                ← ICS 213 General Message
│   ├── ics-214/                ← ICS 214 Activity Log
│   ├── ics-309/                ← ICS 309 Communications Log
│   ├── band-plan/              ← U.S. amateur band plan
│   ├── cheatsheets/            ← Radio quick-reference cards
│   ├── graywolf-handbook/      ← GrayWolf offline handbook
│   ├── quick-ref/              ← Q-codes, phonetics, pro-words, RST, ITU
│   ├── chirp/                  ← CHIRP CSVs (samples + saved exports)
│   ├── radio-cards/            ← Per-radio operation cards
│   ├── radio-manuals/          ← Your own PDF manuals (not bundled — gitignored)
│   ├── repeater-guide/         ← Repeater programming with CHIRP
│   └── repeaterbook/           ← RepeaterBook offline browser (CSV gitignored)
│
└── docs/                       ← This file and other documentation
```

> **Which script do I run, and when?** See the [Scripts matrix in the README](../README.md#-scripts--what-to-run-when--why). In short: `setup-server.py` is the only required one; the rest are opt-in per feature. Large data (FCC database, full map regions, PDF manuals, Wikipedia) is downloaded or generated by those scripts — it is not stored in the repo.

---

## Guided setup (menu)

If you'd rather not run each script by hand, `setup-oasis.py` is a
**guided menu** that lists every feature, lets you tick what you want, and runs
the matching `install-*` / `enable-*` scripts in the right order (pulling in any
prerequisites automatically).

```bash
# Run as your normal user — NOT with sudo (it asks for sudo once when needed):
python3 setup-oasis.py

python3 setup-oasis.py --list                       # list features, change nothing
python3 setup-oasis.py --all                        # everything (incl. data downloads)
python3 setup-oasis.py --features graywolf,winlink  # non-interactive subset
```

- **What it looks like:** the menu fills your terminal with a checkbox list — it does not open a browser or a separate window. Use your keyboard only; the mouse does nothing here.
- **Navigation:** ↑/↓ move, **Space** to check/uncheck, **A/N** all/none, **Q** cancels.

> ⚠️ **Common mistake:** pressing **Enter** on a feature row just toggles its checkbox — it does **not** start the install. When you've ticked everything you want, press **Tab** to move the cursor to the **OK** button, then **Enter** on OK to begin. Nothing runs until you do this.

- **Sections:** *Server* (server, auto-start, GrayWolf, Winlink, Kiwix, Web SSH),
  *Audio* (RTL-SDR, the APRS feed, DRA-Pi-Zero), *Content / Data* (FCC, Wikipedia).
  Software/services are pre-checked; large data downloads are opt-in.
- **Privilege model:** runs as your normal user and caches `sudo` once at the start
  (so your password isn't asked again mid-install). Each delegated script keeps the
  right context — e.g. `.venv` is created as you, not root. The script refuses to
  run under `sudo`.
- **Idempotent:** every delegated script is version-aware, so re-run the menu
  anytime to add a feature. Steps that need a reboot (DRA-Pi) are flagged and the
  menu offers to reboot at the end.
- **What to select for a basic net station:** tick **Server** and **FCC Database** for
  the dashboard, callsign lookup, and offline maps. Add **GrayWolf** for APRS,
  **Winlink** for email over radio, **Kiwix** for offline Wikipedia. Everything else
  is optional.

The sections below document each feature on its own, for when you want to run or
re-run a single one.

---

## Server setup

`scripts/setup-server.py` creates a **Python virtual environment** (`.venv`) in the project folder and installs all server dependencies into it — **fully offline** on every supported platform. A virtual environment is an isolated Python installation that keeps OASIS's packages separate from the system Python; you don't need to know the internals, just know that `setup-server.py` creates it for you.

```bash
python3 scripts/setup-server.py           # create .venv and install from bundled wheels
python3 scripts/setup-server.py --check   # report what's installed / missing
```

Flask and gunicorn are the web server stack: Flask handles the OASIS routes and API, and gunicorn (a production-grade HTTP server) runs Flask reliably in the background. You don't need to know the internals — `setup-server.py` and `scripts/start-server.sh` handle everything. Flask, gunicorn, MarkupSafe, and psutil are all vendored in `server/wheels/` as pre-built wheels for Python 3.9–3.14 on Linux (aarch64 + x86-64), macOS (Apple Silicon + Intel), and Windows — so no internet is required. On the rare platform/Python combo without a vendored wheel, `setup-server.py` falls back to PyPI automatically.

The wheel set is kept current by running `scripts/create-oasis-offline.py` (incremental — checks PyPI for newer compatible packages, downloads them to a temp directory, and atomically swaps in the fresh set only if something changed). Run `--check` to confirm every platform resolves offline without downloading anything.

**To start the server manually (test / development):**

```bash
scripts/start-server.sh        # Linux / macOS terminal
```

On macOS, you can also double-click `scripts/start-server.command` in Finder (it opens a Terminal window). On Windows, double-click `scripts\start-server.bat`.

You should see output like:
```
[INFO] Listening at: http://0.0.0.0:8083
```
Open `http://localhost:8083` in a browser (or `http://<pi-ip>:8083` from another device). If the dashboard loads, the server is working.

**What to expect on first run:** service cards for GrayWolf, Winlink, and Kiwix will show as DOWN or greyed out — that's normal if you haven't installed them yet. The dashboard, FCC lookup, maps, ICS forms, and calculators work from the core server install alone.

> ✅ **Verify your setup:** after the server is running, open `http://<pi-ip>:8083/server/system/setup.html` in a browser for a full visual health check. Or run `python3 scripts/doctor.py` from the terminal for the same checks without a browser. See [Health check (doctor)](#health-check-doctor).

> 💡 `scripts/start-server.sh` handles the entire startup sequence — you don't need to activate `.venv` yourself or worry about a previous instance on port 8083.

**To stop the server:** press `Ctrl+C` in the terminal where `scripts/start-server.sh` is running. If you need to stop it from a different terminal (e.g. when setting up auto-start), run `sudo fuser -k 8083/tcp` — this kills whatever process is using port 8083.

<details><summary>What <code>scripts/start-server.sh</code> does under the hood</summary>

1. **Finds Python 3.9+** — scans the system path for a compatible interpreter.
2. **Creates `.venv`** (once) — if the virtual environment doesn't exist yet, creates it.
3. **Installs from bundled wheels** — installs Flask, gunicorn, and psutil offline from `server/wheels/` (idempotent; no-op if already installed).
4. **Pre-flight check** — reports Python, Flask, gunicorn, psutil, FCC index, and system fonts.
5. **Frees port 8083** — if a previous OASIS instance is still bound, sends SIGTERM (then SIGKILL if needed) before starting a fresh one. Re-running the launcher is a clean restart.
6. **Launches gunicorn** — `gunicorn --workers 1 --bind 0.0.0.0:8083 app:app` from the `server/` directory. Falls back to `python app.py` (Flask dev server) if gunicorn isn't available. (Single worker deliberately — the Setup Orchestrator's in-process job/plan state isn't shared across multiple gunicorn worker processes.)

You never need to run any of these steps manually — the launcher handles them.

</details>

**Auto-start on boot (systemd):**

systemd is the Pi's service manager — the equivalent of Windows Services. Once enabled, OASIS starts automatically every time the Pi powers on, without you needing to SSH in and run the launcher. Use the included script to set this up:

```bash
# Stop any manually-started server first (avoids port 8083 conflict):
sudo fuser -k 8083/tcp

# Server only (headless / multi-user)
python3 scripts/enable-autostart-pi.py

# Server + Chromium kiosk (Raspberry Pi OS with Desktop)
python3 scripts/enable-autostart-pi.py --with-browser

# Server + clickable desktop shortcut (Raspberry Pi OS with Desktop)
python3 scripts/enable-autostart-pi.py --desktop-icon

# Remove autostart, kiosk, and desktop icon
python3 scripts/enable-autostart-pi.py --disable
```

The script creates `/etc/systemd/system/oasis.service` and runs `systemctl enable --now oasis`.

> ⚠️ **Port conflict:** OASIS can only run one copy at a time. If you started it manually before enabling auto-start, two copies will try to use port 8083 and one will fail. **The easiest fix is to reboot** — the systemd service takes over cleanly on boot. Alternatively: `sudo fuser -k 8083/tcp` (stops whatever is using port 8083), then `sudo systemctl start oasis`.

> ℹ️ **Auto-start vs `scripts/start-server.sh`:** once auto-start is enabled, the server starts on
> every boot without you doing anything. You do **not** need to run the launcher anymore.
> Run `scripts/start-server.sh` only if you want to test the server manually without enabling auto-start.

<details><summary>Manual service file (if you prefer not to use the script)</summary>

Create `/etc/systemd/system/oasis.service`, replacing `YOUR_USERNAME` with your actual login name (run `whoami` if unsure):

```ini
[Unit]
Description=OASIS suite server
After=network.target

[Service]
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/oasis-emcomm
ExecStart=/home/YOUR_USERNAME/oasis-emcomm/.venv/bin/gunicorn --workers 1 --bind 0.0.0.0:8083 server.app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now oasis
```

</details>

**Server routes:**

| Route | Description |
|---|---|
| `GET /` | Dashboard |
| `GET /lookup` | FCC callsign lookup page (callsign · name · grid tabs) |
| `GET /api/lookup?callsign=W7XYZ` | JSON callsign result (exact or `W7*` prefix) |
| `GET /api/lookup/name?last=SMITH&first=JOHN` | JSON name search (up to 50 results) |
| `GET /api/lookup/grid?grid=CN87XN` | JSON grid search (up to 100 results; 2–6 char prefix) |
| `GET /maps/<file>.pmtiles` | PMTiles archive bytes (HTTP range reads; the browser renders) |
| `GET /api/fs/browse?path=<dir>` | Browse allowlisted locations (USB / mounts) for `.pmtiles` |
| `GET /api/fs/pmtiles?path=<file>` | Stream a `.pmtiles` from an allowlisted path (HTTP range) |
| `GET /map-assets/<path>` | MapLibre GL + pmtiles.js JS/CSS, fonts |
| `GET /health` | JSON health check (FCC index presence · callsign count · name/grid index presence) |
| `GET /api/system` | hostname / IP / CPU / temp / RAM / disk / load / uptime / GPS / chrony (drives stats bar and GPS card) |
| `GET /api/audio` | ALSA sound cards: index, name, capture/playback, USB flag — Linux only |
| `GET /api/service` (POST) | Start/stop a controllable service (graywolf, kiwix, webssh, etc.) |
| `GET /server-ports.json` | Port map consumed by dashboard JS |

The dashboard and home page poll these to show live status: a **System** stats bar (Host/CPU/Temp/RAM/Disk/Load/Uptime, colour-coded green/amber/red by threshold), an **Audio Devices** panel, a **Web SSH** card, and a **GPS card** in the header showing fix mode, satellites, HDOP, lat/lon, altitude, and chrony clock-lock status. A **Units** pill (Imperial/Metric) toggles all displayed measurements (temperature, altitude, speed) in one tap — preference is persisted per browser.

The stats bar leads with **HOST** — the hostname and LAN IP of the machine actually serving the page. The **HDOP** value is colour-coded across six steps from ideal (bright green, < 1) through excellent, good, moderate, fair, to poor (red, > 20). Service cards include **START/STOP** buttons for controllable services (GrayWolf, Winlink, Kiwix, Web SSH, OpenWebRX), so you never need to SSH in just to restart a service.

---

## FCC Callsign Lookup

Offline lookup of U.S. amateur licenses. No database engine — binary-search over FCC flat files. Three search modes are available:

| Mode | Example | Returns |
|------|---------|---------|
| **Callsign** (exact) | `W4MHI` | Single record |
| **Callsign** (prefix wildcard) | `W4*` | Up to 50 active licenses starting with W4 |
| **Name** | Last: `SMITH`, First: `JOHN` (optional) | Up to 50 records, prefix match on last name |
| **Grid** | `CN87` or `CN87XN` (2–6 chars) | Up to 100 records in that grid area |

Lookups return callsign, name, city, state, and Maidenhead grid (derived from ZIP centroid). Name and grid search require the secondary indexes built by `services/fcc_database/install.py`.

**Setup (internet-connected machine, one-time):**

```bash
# Download FCC data, build all indexes, and generate zipcodes.csv — do this once:
python3 services/fcc_database/install.py

# Re-index only (rebuild indexes from existing EN.dat, no download):
python3 services/fcc_database/install.py --index-only

# Force refresh zipcodes.csv even if it already exists:
python3 services/fcc_database/install.py --full-zip
```

> ℹ️ **Where to run this:** run on any machine with internet (your laptop, another Pi, etc.) then copy the `data/` folder to the Pi with `scp`. Alternatively, run it directly on the Pi if the Pi has internet access.
>
> **`--full-zip` vs plain invocation:** the plain command already downloads `zipcodes.csv` if it doesn't exist. Use `--full-zip` only when you want to force a fresh download of `zipcodes.csv` even if it already exists (e.g. after a GeoNames update).

Files written to `services/fcc_database/data/`:

| File | Description |
|---|---|
| `EN.dat` | FCC entity records (~200 MB) — gitignored |
| `HD.dat` | FCC license headers (~120 MB) — gitignored |
| `EN.idx` | Primary callsign binary-search index — gitignored |
| `EN_name.idx` | Secondary index sorted by last name — gitignored |
| `EN_grid.idx` | Secondary index sorted by 6-char Maidenhead grid — gitignored |
| `zipcodes.csv` | ZIP → lat/lon — gitignored; generated from GeoNames by `services/fcc_database/install.py` |

**How it works:** `HD.dat` identifies active licenses. `EN.dat` holds the entity records. The primary index points to byte offsets in `EN.dat` by callsign. The name index is keyed by `LASTNAME\tFIRSTNAME`; the grid index by 6-char uppercase grid. All three use the same binary-search engine. Only active licenses are indexed.

> ℹ️ **Name and grid search require `zipcodes.csv`.** The grid index is derived from ZIP centroids. If `zipcodes.csv` is absent when `--index-only` is run, the grid index is skipped (a warning is shown) — run without `--index-only` to download it.

**Copy to Pi** (if you ran `services/fcc_database/install.py` on a different machine):

`scp` copies files from your laptop to the Pi over the network — like a command-line file transfer. Run this from your laptop (not the Pi), replacing `<username>` and `<hostname>` with the values you set in Imager:

```bash
# Mac / Linux laptop:
scp -r services/fcc_database/data <username>@<hostname>.local:/home/<username>/oasis-emcomm/services/fcc_database/
# e.g.  scp -r services/fcc_database/data w4mhi@oasis.local:/home/w4mhi/oasis-emcomm/services/fcc_database/
```

> 💡 **Windows users:** PowerShell has `scp` built in (Windows 10 1809 or later). If it doesn't work, use [WinSCP](https://winscp.net/) (free GUI app) or [FileZilla](https://filezilla-project.org/) (SFTP mode) — connect to `<hostname>.local`, port 22, with your Pi username and password, then drag the `services/fcc_database/data/` folder to the same path on the Pi.

If you ran `services/fcc_database/install.py` directly on the Pi, no copying is needed.

---

## Using OASIS in the field (no internet)

> 💡 **Set up at home, deploy in the field.** Complete the full setup — server, FCC database, GrayWolf, GPS, and AP fallback — while the Pi has internet access. Validate everything works, then take the Pi offline. Any step that needs internet (FCC data download, `apt install`, GrayWolf `.deb`) must run before the activation. This is the same practice as charging your radio and testing it before going to the EOC — not something to sort out on arrival.

For deployment at an activation or field event, the Pi needs to act as a Wi-Fi access point so other devices can connect without a separate router. OASIS can do this **automatically** — preferring a known Wi-Fi network when one is in range, and falling back to its own access point when none is.

**Arm the AP fallback (one time):**

```bash
python3 scripts/enable-ap-fallback.py
# custom hotspot name / password:
python3 scripts/enable-ap-fallback.py --ssid FIELD1 --password mypassword
```

This installs `avahi-daemon` (so the dashboard resolves at `oasis.local`), creates a WPA2 hotspot profile, and arms a watcher (`oasis-netwatch.service`) that raises the AP whenever no known network is reachable. Undo any time with `--disable`; check status with `--check`.

**Defaults** (a public, EmComm-friendly hotspot):

| Setting | Value |
|---|---|
| SSID | `OASIS` (`--ssid`) |
| Password | `oasis-emcomm` — WPA2, publicly documented for quick field access (`--password`) |
| AP address | `10.42.0.1` (DHCP hands clients `10.42.0.2–254`) |
| Hostname | `oasis.local` (via avahi) |

Once the AP is up, other devices connect to the `OASIS` Wi-Fi (password `oasis-emcomm`) and open `http://10.42.0.1:8083` (or `http://oasis.local:8083`).

**Joining a Wi-Fi network from the dashboard:** tap the **Wi-Fi** button under the clock, pick a network, and enter its password. Because the Pi Zero 2 W has a single radio, joining a network takes the OASIS AP offline — any device connected to the AP must reconnect via the Pi's new address.

> 💡 The Wi-Fi pill under the clock shows the current SSID or `AP: OASIS`. The System Monitor **IP** row shows the address to reach the dashboard on (e.g. `10.42.0.1` when hosting the AP).

**Manual hotspot** (without the fallback watcher) still works too:

```bash
sudo nmcli device wifi hotspot ssid OASIS password oasis-emcomm
```

### How it works

- **`oasis-netwatch.service`** — a small daemon that, at boot, waits ~25 s for NetworkManager to auto-join a saved network; if none is reachable it raises the `OASIS-AP` hotspot. During operation it polls every 15 s and, after a client link has been gone ~45 s, falls back to the AP. It logs every decision:
  ```bash
  journalctl -u oasis-netwatch -f
  ```
- **`/usr/local/bin/oasis-netctl`** — a pinned privileged helper (scan / connect / forget / ap-up) the dashboard calls via a scoped `sudo` rule (`/etc/sudoers.d/oasis-wifi`); no password touches the web layer.
- **Single radio:** the Pi Zero 2 W has one Wi-Fi radio, so it is **either** a client **or** the AP, never both. Joining or forgetting a network from the dashboard therefore drops the AP; reconnecting to a known network after an AP fallback happens on the next reboot or via the dashboard.

Check status any time:

```bash
python3 scripts/enable-ap-fallback.py --check
```

### Troubleshooting the access point

These are the real issues seen bringing the AP up on a Pi Zero 2 W (Broadcom `brcmfmac` radio), in the order to check them.

**1. The `OASIS` network doesn't broadcast at all.** NetworkManager can report the AP as "up" while the radio isn't actually beaconing. The two usual causes are a soft-blocked radio and an unset Wi-Fi regulatory domain (`country 00`), which suppresses 2.4 GHz beaconing.

```bash
iw dev wlan0 info        # want: type AP · ssid OASIS · channel 6
rfkill list              # wifi must NOT be "Soft blocked: yes"
iw reg get               # want a real country (e.g. US), NOT "country 00"
```

Fix — set your country and unblock the radio (the enable script does this when you pass `--country`):

```bash
sudo python3 scripts/enable-ap-fallback.py --country US
# or manually:
sudo raspi-config nonint do_wifi_country US
sudo rfkill unblock wifi
sudo iw reg set US
sudo nmcli connection up OASIS-AP
```

> A bare `raspi-config` country change doesn't always apply to the live radio until reboot — `iw reg set US` applies it immediately.

**2. The `OASIS` network shows, but the phone asks for a WPS PIN (4–8 digits) instead of a password.** This happens when the AP advertises an ambiguous/mixed security mode; Windows and Android then fall back to the WPS flow. The fix is to force clean **WPA2-only (RSN / CCMP-AES)** — the enable script now sets this. To apply by hand:

```bash
sudo nmcli connection modify OASIS-AP \
  802-11-wireless-security.key-mgmt wpa-psk \
  802-11-wireless-security.proto rsn \
  802-11-wireless-security.pairwise ccmp \
  802-11-wireless-security.group ccmp \
  802-11-wireless-security.pmf disable
sudo nmcli connection down OASIS-AP; sudo nmcli connection up OASIS-AP
```

**3. Joining a network from the dashboard fails with `802-11-wireless-security.key-mgmt: property is missing`.** A stale saved profile for that SSID has an incomplete security section, and NetworkManager reactivates it instead of creating a fresh one. Delete the offending profile and reconnect:

```bash
nmcli -f NAME,TYPE connection show                          # find the wifi profile
nmcli -g 802-11-wireless-security.key-mgmt connection show <NAME>   # empty = broken
sudo nmcli connection delete <NAME>                          # e.g. MH-500 or netplan-<SSID>
```

The dashboard helper now purges a stale same-SSID profile automatically before every connect, so this shouldn't recur after re-running the enable script.

**4. The System Monitor IP shows `127.0.0.1`.** Resolved — the server now falls back to the first non-loopback interface address (preferring `wlan*`) when there's no default route, so it reports `10.42.0.1` in AP mode.

**5. Starting OASIS fails with "address already in use".** `scripts/start-server.sh` handles this automatically — it frees port 8083 before starting. If the port is held by the systemd `oasis.service`, use `sudo systemctl restart oasis` instead.

---

## Offline Maps

Vector tile maps rendered in the browser with MapLibre GL. A **PMTiles file** is a single file that contains an entire regional map — think of it as an offline Google Maps for your region, packaged into one `.pmtiles` file you put on the Pi. The browser reads it directly via HTTP range requests — no internet, no tile server, no database engine.

```
Prep machine (internet, one-time)
  └─ Download / convert a region to *.pmtiles
       └─ drop into maps/  (or onto a USB stick)

Pi (no internet)
  └─ server/app.py :8083
       ├─ GET /maps/<file>.pmtiles             → archive bytes (HTTP range)
       ├─ GET /api/fs/browse · /api/fs/pmtiles → load maps off USB at runtime
       └─ maps/map.html  ← MapLibre + pmtiles.js render in browser
```

### Step 1 — Get a PMTiles file

**Option A — GrayWolf offline maps *(easiest, recommended if GrayWolf is installed)*:** GrayWolf has a built-in offline map downloader. Open `http://<pi-ip>:8080`, sign in (GrayWolf registration is free — it requires a callsign), then go to **Maps → Offline Maps → Add a region**. A drawer opens with a country tree: expand **United States** to pick individual states, or pick a whole country. Download the region(s) you need. GrayWolf saves them to `/var/lib/graywolf/tiles/`, which OASIS's **Load maps** button already knows about — no copying required.

**Option B — Download a pre-built PMTiles archive:** Protomaps publishes free daily basemap builds at **[maps.protomaps.com/builds/](https://maps.protomaps.com/builds/)**. The full-planet file is very large (~90 GB), so use the `pmtiles extract` command to cut out just your state or region before copying to the Pi:

```bash
# Install the pmtiles CLI (one-time, needs internet — see Option C below for the binary download)
# Then extract a region from the remote daily build — no need to download the full planet:
pmtiles extract https://maps.protomaps.com/builds/latest.pmtiles washington.pmtiles \
  --bbox=-124.8,45.5,-116.9,49.1    # minLon,minLat,maxLon,maxLat
```

Find bounding-box coordinates for your state at [bboxfinder.com](http://bboxfinder.com). The resulting file for a US state is typically 500 MB–2 GB. Copy it to `maps/` on the Pi (see Step 2 below).

> ℹ️ If you already have the `pmtiles` binary from Option C, you can use the same binary here.

**Option C — Convert an existing MBTiles file:** if you already have an MBTiles archive, use the helper script:

#### Converting MBTiles → PMTiles

`maps/convert-mbtiles.py` wraps the `pmtiles` CLI and handles binary detection,
output naming, and cleanup on failure.  Do this on a machine with internet
**before** going to the field — only the one-time binary download needs a
connection.

**1. Get the `pmtiles` binary (once, needs internet):**

| Platform | Archive |
|---|---|
| Raspberry Pi 64-bit (arm64) | `go-pmtiles_*_Linux_arm64.tar.gz` |
| Linux x86-64 | `go-pmtiles_*_Linux_x86_64.tar.gz` |
| macOS Apple Silicon | `go-pmtiles-*_Darwin_arm64.zip` |

Download from <https://github.com/protomaps/go-pmtiles/releases>, extract the
`pmtiles` binary, and place it in `maps/` (or anywhere on `$PATH`):

```bash
# Example — Raspberry Pi 64-bit (run on the Pi, or on any Linux arm64 machine)
# The * glob works when exactly one matching file is in the current directory.
# If you downloaded multiple versions, delete the old one first.
tar -xzf go-pmtiles_*_Linux_arm64.tar.gz pmtiles
mv pmtiles maps/
chmod +x maps/pmtiles   # marks the file as executable so the OS can run it
```

The script checks `maps/` first, so it works even under the trimmed `PATH`
that systemd uses.

**2. Verify the binary is found:**

```bash
python3 maps/convert-mbtiles.py --check
```

**3. Convert:**

```bash
python3 maps/convert-mbtiles.py region.mbtiles              # output: region.pmtiles (same dir)
python3 maps/convert-mbtiles.py region.mbtiles maps/region.pmtiles
```

The script refuses to overwrite an existing file and removes partial output if
the conversion is interrupted.

OASIS expects the **OpenMapTiles (OMT) v3.x** schema.

> **Recommended:** a state or regional extract (e.g. *US State — Washington*) keeps the file manageable on a Pi SD card or USB stick. Files are licensed under ODbL (OpenStreetMap data).

### Step 2 — Deploy

Place the `.pmtiles` file in `maps/`. The map opens at the archive's own center/zoom — no config file needed:

```bash
# Run this on your laptop, replacing <username> and <hostname> with what you set in Imager:
scp maps/<region>.pmtiles <username>@<hostname>.local:/home/<username>/oasis-emcomm/maps/
# e.g.  scp maps/washington.pmtiles w4mhi@oasis.local:/home/w4mhi/oasis-emcomm/maps/
```

> 💡 **Windows users:** use WinSCP or FileZilla (SFTP, port 22) to drag the `.pmtiles` file into `/home/<username>/oasis-emcomm/maps/` on the Pi.

Or skip copying entirely: keep `.pmtiles` files on a USB stick and use the **Load maps** button on the map page to browse and load them at runtime. The locations the browser may read from are controlled by the `OASIS_MAP_ROOTS` environment variable (default: `/media`, `/mnt`, `/run/media`, `/Volumes`, plus `maps/`).

The browser reads the archive directly — no build step, no external tools, no extra Python packages.

### Refreshing JS libraries

MapLibre GL and pmtiles.js are bundled in `server/map-assets/`. To update:

```bash
cd server/map-assets
curl -L -o maplibre-gl.js  https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js
curl -L -o maplibre-gl.css https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css
curl -L -o pmtiles.js      https://unpkg.com/pmtiles@3.2.1/dist/pmtiles.js
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Map stuck on "Loading…" | Server not restarted after `app.py` update | Restart the server — new routes won't load until restarted |
| Blank basemap, 404 for `/maps/<file>.pmtiles` | File not in `maps/` (or wrong name) | Confirm the `.pmtiles` is in `maps/`, or load it via **Load maps** |
| No roads / blank map | Layer filter mismatch | Open browser console — check diagnostics panel for layer names |
| Labels/fonts missing | Glyph PBF files absent | Check `server/map-assets/fonts/Open Sans Regular/` |
| **Load maps** lists nothing | Mount not in the allowlist | Set `OASIS_MAP_ROOTS` to include your mount point, then restart |

---

## GrayWolf APRS

[GrayWolf](https://github.com/chrissnell/graywolf) is a browser-based APRS TNC, iGate, and digipeater that runs on port 8080.

```bash
python3 services/graywolf/install.py

# Pin a version:
python3 services/graywolf/install.py --version 0.13.16

# Help:
python3 services/graywolf/install.py --help
```

The script automatically picks the install source: if the bundled `.deb` is present in `offline-packages/graywolf/` (put there by `create-oasis-offline.py`) it is used without any network access; otherwise the matching `.deb` is downloaded from GitHub releases. After install, open `http://<pi-ip>:8080` to configure GrayWolf.

**Hardware prerequisites** — before configuring GrayWolf, make sure you have:
- A **VHF/UHF radio** capable of 2 m APRS (144.390 MHz in North America), e.g. Yaesu FT-65, Kenwood TH-D74, Baofeng UV-5R, or any FM transceiver
- An **audio/PTT interface** connecting the radio to the Pi. Common options:
  - **[DigiRig Mobile](https://digirig.net/)** — USB, supports most radios, recommended for beginners. The DigiRig needs a radio-specific cable — check **[digirig.net/product-category/cables/](https://digirig.net/product-category/cables/)** to find the cable for your exact radio model before ordering.
  - **[AIOC (All-In-One-Cable)](https://github.com/skuep/AIOC)** — USB, designed for Baofeng/Kenwood
  - **MastersCommunications DRA-Pi-Zero** — I²S HAT for the Pi GPIO header (see [DRA-Pi-Zero setup](#dra-pi-zero-sound-card))
  - **RTL-SDR dongle** — receive only, no transmit (see [RTL-SDR](#rtl-sdr))
- The interface plugged into the Pi **before** you start configuring channels

> ⚠️ **Restart GrayWolf after adding a device and channel.** GrayWolf only reads channel config when the modem starts — adding a device at runtime won't take effect until you restart:
> ```bash
> sudo systemctl restart graywolf
> ```
> If you configured everything and nothing is decoding, restart GrayWolf first.

> **APRS history API (port 8085) is enabled automatically.** `services/graywolf/install.py`
> also sets up the `graywolf-api` systemd service (via `services/graywolf/enable-graywolf-api.py`)
> — a small Flask app that feeds the OASIS APRS map and the dashboard's
> system-stats bar from GrayWolf's history DB. It runs under the repo's `.venv`,
> so run `setup-server.py` first. To re-enable it on its own (e.g. after moving
> the repo): `python3 services/graywolf/enable-graywolf-api.py`.

### 1. Station Callsign

Go to **Settings → Station Callsign** and enter your callsign with SSID (e.g. `W4MHI-5`).

The **SSID** (the `-5` part) distinguishes multiple APRS stations on the same callsign — `-5` is the conventional suffix for a fixed home/base station, `-9` for a mobile, `-1` for a digipeater. Pick one that matches your role; `-5` is a safe default for a Pi-based EOC station.

### 2. Audio Devices

Go to **Settings → Audio Devices → Add Device**. Add your sound card for both input (RX) and output (TX). Use **Detect Devices** to find the ALSA path.

- **Sample Rate:** 96000 Hz, **Channels:** Mono
- Adjust LEVEL and GAIN sliders after a test transmission

> 💡 **Do this before adding Channels** — the channel configuration (step 3 below) needs your audio device to already be listed here, or the dropdown will be empty.

![GrayWolf — Audio Devices configured](images/file5.png)

### 3. Channels

Go to **Settings → Channels → Add Channel**. Configure a modem-backed VHF APRS channel:

- **Modem Type:** AFSK
- **Bit Rate:** 1200 / **Mark:** 1200 Hz / **Space:** 2200 Hz
- **Input / Output Device:** select the sound card you added in step 2
- **TX Delay:** 300 ms — key-up time before sending

![GrayWolf — Edit Channel (VHF APRS)](images/file9.png)

After saving, the channel card shows the backing status and modem parameters at a glance:

![GrayWolf — Channels list (VHF APRS configured)](images/file10.png)

### 4. PTT

Go to **Settings → PTT → Add PTT**. Use **Detect Devices** — GrayWolf will recommend the best match for your hardware.

**Which PTT method to use:**
- **DigiRig, AIOC, or any CM108-based USB cable** → select the `/dev/hidraw*` device (e.g. `/dev/hidraw1`). Leave the GPIO Pin field blank. These cables handle PTT via USB HID, not GPIO.
- **Direct wire from a Pi GPIO pin to your radio's PTT line** → leave Device blank and enter the GPIO BCM number (e.g. GPIO 12 for the DRA-Pi-Zero). Use this only if you've wired your radio PTT directly to the Pi header.

Do not configure both — pick one method based on your cable.

![GrayWolf — PTT Configuration](images/file23.png)

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

## Winlink (Pat)

> **Current capability:** Internet Winlink (via Telnet gateway) works immediately after install — you can send and receive Winlink email as long as the Pi has internet access. RF Winlink (sending over radio when there's no internet) via GrayWolf's KISS TNC interface is **experimental** — the wiring is functional but the integration is not yet polished or documented for general use. For a fully offline Field Day where you need RF Winlink, plan accordingly.

[Pat](https://getpat.io) is a Winlink client with a browser UI — compose, read,
and send Winlink (radio email) from `http://<pi-ip>:8082`. Installed with:

```bash
python3 services/winlink/install.py                 # bundled .deb if present, else download
python3 services/winlink/install.py --callsign W4MHI
python3 services/winlink/install.py --no-service    # install + config only
python3 services/winlink/install.py --help
```

The script installs the Pat `.deb` (offline-first from `offline-packages/pat/`,
else GitHub — version-aware), writes a starter `~/.config/pat/config.json` (mode
600; prompts for your Winlink password), and enables a `pat` systemd service
running `pat http` on **:8082**. A Winlink account for your callsign is required.

**Phase 1 — Telnet (internet gateway)** works immediately: compose a message,
then *Action → Connect → telnet*. If you skipped the password prompt, set it with
`pat configure` before connecting.

**Phase 2 — RF (off-grid)** reuses GrayWolf's KISS TNC as the modem (no second modem). Connect Pat to GrayWolf's KISS port (Settings → KISS Interfaces in GrayWolf, then configure Pat's `ax25.json`) — the plumbing is there but the end-to-end setup is experimental. See the [GrayWolf KISS docs](../static/graywolf-handbook/kiss.html) in the offline handbook.

**Dedicated Direwolf modem (default-on).** The installer also sets up its own
Direwolf AX.25 modem behind Pat's AGWPE transport (`pat-direwolf` service on
:8000, start-on-demand from the dashboard — hardware-exclusive with GrayWolf).
It writes **both** interface configs to `~/.config/direwolf/` and points the
service at the one you pick:

| Interface | Config file | PTT | Selected by |
|---|---|---|---|
| **DRA-Pi-Zero** (default) | `oasis-winlink.conf` | GPIO 12 | default |
| **DigiRig Mobile** | `oasis-winlink-digirig.conf` | CP210x serial **RTS** | `--modem-interface digirig` / the *Winlink RF via DigiRig* menu tick |

`MYCALL` in both configs comes from your `station.json` callsign (seeded by the
first setup step). See [Winlink RF via DigiRig](#winlink-rf-via-digirig) to wire
up a DigiRig, including audio-level tuning and debugging. Skip the modem entirely
with `--no-modem` (Telnet-only Winlink).

> Plain HTTP, and `config.json` holds your Winlink password — keep this on your
> trusted LAN, not the open internet.

---

## DRA-Pi-Zero sound card

If you use a **MastersCommunications DRA-Pi-Zero** (Wolfson WM8731 I²S codec) as
GrayWolf's radio interface, configure it with:

```bash
python3 features/dra-audio-interface/enable-dra-pi.py            # auto-detects the phase
python3 features/dra-audio-interface/enable-dra-pi.py --dry-run   # preview the config.txt changes first
```

It runs in two phases, auto-detected by whether the card is present yet:

1. **Before reboot** (card absent): edits `/boot/firmware/config.txt` to load the
   WM8731 overlay and free the I²S bus — backs the file up once
   (`config.txt.oasis-bak`), comments the conflicting Pi defaults
   (`dtparam=audio=on`, plain `vc4-kms-v3d`), and adds an idempotent managed
   block. **A reboot is required** for the codec to appear.
2. **After reboot** (card present): applies the ALSA mixer routing — the critical
   `Input Mux = Mic`, capture gain, TX path — and persists it with `alsactl store`.

> **Run `--dry-run` first** to review the `config.txt` diff before it writes —
> this edits a boot file. The full hardware writeup is in
> [`graywolf-dra-pi.md`](graywolf-dra-pi.md).

---

## DRA-Pi RX LED

GrayWolf keys GPIO 12 (the red TX LED) during transmit, but it has no carrier-detect output, so the DRA-Pi-Zero's green RX LED (GPIO 16) stays dark even while APRS packets are decoding. `enable-dra-rx-led.py` installs a lightweight daemon that pulses the green LED on every newly decoded APRS position packet.

```bash
python3 features/dra-audio-interface/enable-dra-rx-led.py                # install + enable dra-rx-led.service
python3 features/dra-audio-interface/enable-dra-rx-led.py --no-enable     # write the unit, don't start it
python3 features/dra-audio-interface/enable-dra-rx-led.py --gpio 16       # override the LED GPIO (BCM)
python3 features/dra-audio-interface/enable-dra-rx-led.py --self-test     # blink the LED 5× and exit
python3 features/dra-audio-interface/enable-dra-rx-led.py --uninstall     # stop and remove the service
```

The daemon polls GrayWolf's history database (`/var/lib/graywolf/graywolf-history.db`) for new rows in the `positions` table and pulses GPIO 16 for 120 ms on each hit. It runs as root so it can drive the GPIO via `pinctrl` (ships with Pi OS Bookworm/Trixie) and read the GrayWolf DB.

> **Prerequisites:** DRA-Pi-Zero configured (`enable-dra-pi.py` + reboot), GrayWolf installed and running, and `pinctrl` available on the path. **Do not drive GPIO 12 from this script** — GrayWolf already owns that pin for PTT.

Service management:
```bash
sudo systemctl status dra-rx-led
sudo systemctl restart dra-rx-led
journalctl -u dra-rx-led -f
```

---

## Winlink RF via DigiRig

A [DigiRig Mobile](https://digirig.net/) is an alternative to the DRA-Pi-Zero for
the Winlink Direwolf modem: it's a **USB sound card** plus a **CP210x USB-serial
bridge** whose **RTS** line keys PTT. No GPIO, no boot overlay, no reboot — plug
it in and go. (The DRA-Pi-Zero keys PTT on GPIO 12 and needs the WM8731 overlay +
a reboot; the DigiRig needs neither.)

Install/point the modem at the DigiRig with either:

```bash
# menu: tick "Winlink RF via DigiRig" (needs the Winlink tick)
python3 setup-oasis.py

# or directly — only (re)writes the modem config + re-points the service,
# it does NOT touch the Pat install or its saved password:
python3 services/winlink/install.py --modem-interface digirig --modem-only
```

Both interface configs are always written to `~/.config/direwolf/`; the
`pat-direwolf` service points at the DigiRig one (`oasis-winlink-digirig.conf`)
and, because the DigiRig keys PTT over serial RTS (not GPIO), its service carries
**no GPIO unexport** `ExecStopPost`. `MYCALL` comes from your `station.json`
callsign. The installer **auto-detects** the DigiRig — plug it in first.

### 1. Find the two devices (debugging)

If auto-detect fails, or you want to confirm what was picked, identify the USB
sound card and the CP210x serial port by hand:

```bash
# Audio: note the DigiRig's card name (e.g. "Device [USB Audio Device]")
aplay -l
arecord -l
arecord -L | grep -i -A1 'CARD='        # the name-based plughw:CARD=… forms

# PTT serial: the DigiRig's CP210x → a stable by-id path (survives replug)
ls -l /dev/serial/by-id/
dmesg | grep -i cp210                    # confirms which ttyUSB it grabbed
```

Feed non-default values back in via overrides (they win over auto-detect):

```bash
python3 services/winlink/install.py --modem-interface digirig --modem-only \
  --modem-adevice 'plughw:CARD=Device,DEV=0' \
  --modem-ptt-serial /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_XXXX-if00-port0
```

A generated `oasis-winlink-digirig.conf` looks like:

```conf
ADEVICE   plughw:CARD=Device,DEV=0
ARATE     48000
ACHANNELS 1
CHANNEL 0
MYCALL W4MHI
MODEM 1200
PTT /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_XXXX-if00-port0 RTS
AGWPORT 8000
KISSPORT 8001
MAXV22 0
```

### 2. Set the USB card audio levels (alsamixer)

The DigiRig's USB codec exposes different controls than the WM8731 (no
`Input Mux`). Set the TX drive and RX level, disable AGC, then persist:

```bash
CARD=Device            # your aplay -l name
alsamixer -c "$CARD"   # interactive: 'Speaker' = TX drive, 'Mic' = RX level

# …or non-interactively:
amixer -c "$CARD" sset 'Speaker' 80%              # TX audio into the radio (start conservative)
amixer -c "$CARD" sset 'Mic' 50%                  # RX level from the radio
amixer -c "$CARD" sset 'Auto Gain Control' off 2>/dev/null || true
sudo alsactl store                                # persist across reboots
```

Overdriving `Speaker` distorts your transmit audio; too little `Mic` and Direwolf
won't decode. Tune `Mic` for clean RX decodes and `Speaker` for a non-clipped TX.

### 3. Test standalone before wiring it in

```bash
# RX: confirm the card opens and packets decode
direwolf -c ~/.config/direwolf/oasis-winlink-digirig.conf -t 0

# PTT: keys the radio for the transmit-calibration tone (RTS should key it)
direwolf -c ~/.config/direwolf/oasis-winlink-digirig.conf -x
```

If RX decodes and `-x` keys the rig, start the service and use Pat's AGWPE
transport as usual:

```bash
sudo systemctl start pat-direwolf         # stops GrayWolf; frees the audio path
journalctl -u pat-direwolf -f             # watch the modem
```

> Switching back to the DRA-Pi-Zero: re-run with `--modem-interface dra`
> `--modem-only` (or the DigiRig config stays on disk, unused). Both configs
> coexist; only the service target changes.

---

## Kiwix / Wikipedia

Kiwix serves offline snapshots of Wikipedia and other content through a local web server on port 8081.

```bash
python3 services/kiwix/install.py

# Pin a version:
python3 services/kiwix/install.py --version 3.8.2

# Custom ZIM directory:
python3 services/kiwix/install.py --zim-dir /mnt/ssd/zim

# Help:
python3 services/kiwix/install.py --help
```

The script automatically picks the install source: if the bundled package is present in `offline-packages/kiwix/` (put there by `create-oasis-offline.py`) it is used without any network access; otherwise the package is downloaded from kiwix.org.

**Download Wikipedia content:**

```bash
python3 services/kiwix/download-wikipedia.py                        # interactive picker
python3 services/kiwix/download-wikipedia.py --edition top-mini     # ~316 MB, 50K best articles (default)
python3 services/kiwix/download-wikipedia.py --edition simple-mini  # ~447 MB, Simple English
python3 services/kiwix/download-wikipedia.py --edition top-nopic    # ~2.1 GB, 50K articles full text
python3 services/kiwix/download-wikipedia.py --edition simple-maxi  # ~3.2 GB, Simple English with pics
python3 services/kiwix/download-wikipedia.py --list                 # list all editions
```

ZIM files are saved to `~/oasis-offline/zim/` by default (use `--zim-dir PATH` to override).

| Edition | Size | Notes |
|---|---|---|
| `top-mini` | ~316 MB | 50K best articles, no pictures — good for small SD cards |
| `simple-mini` | ~447 MB | Simple English, ~394K articles, no pictures |
| `simple-nopic` | ~937 MB | Simple English, full details, no pictures |
| `top-nopic` | ~2.1 GB | 50K best articles, full details, no pictures |
| `simple-maxi` | ~3.2 GB | Simple English with all pictures |
| `all-mini` | ~11.7 GB | All ~19M articles, no pictures |
| `all-nopic` | ~48 GB | Full English Wikipedia, no images — SSD required |
| `all-maxi` | ~115 GB | Full with images — large SSD required |

> **Note:** Sizes reflect the 2026 Kiwix catalog and grow slightly each month.

---

## RTL-SDR

> ⚠️ **RTL-SDR requires Raspberry Pi OS Trixie (Debian 13).** The RTL-SDR Blog V4 dongle needs `librtlsdr ≥ 2.0`, which is only available on Trixie. Bookworm and Bullseye ship `librtlsdr 0.6.0` which cannot drive the V4.
>
> **Check your OS version:** `cat /etc/os-release | grep VERSION_CODENAME`
> - `bookworm` or `bullseye` → re-flash with Trixie before proceeding (if you're starting fresh, follow [Step 1](#1-flash-raspberry-pi-os) and choose Trixie)
> - `trixie` → you're good

**How to tell if you have a V3 or V4:**
- **V4** (2023 or later): has a robust **True SMA Female** antenna connector (standard SMA thread) and uses the **R828D** tuner chip. The product page and packaging clearly state "V4". If you bought it from the RTL-SDR Blog website or Amazon after mid-2023, it's almost certainly a V4.
- **V3** (older): has a lighter **RP-SMA** or PCB-trace antenna connector and uses the **R820T2** tuner. Look at the PCB itself — V3 says "RTL-SDR Blog R820T2 RTL2832U" on the silkscreen.
- **Not sure?** Run `lsusb` with the dongle plugged in. V4 shows `0bda:2838` (same USB ID as V3) but `rtl_test -t` will report the tuner chip name.

> V3 dongles (older RTL-SDR Blog, Nooelec) may work on Bookworm but are not officially supported with these scripts.

```bash
python3 features/rtl-sdr/install-rtl-sdr.py                # auto: bundled .debs if present, else apt
python3 features/rtl-sdr/install-rtl-sdr.py --help
```

The script automatically picks the install source: if the bundled `.deb` packages are present in `offline-packages/rtl-sdr/` (put there by `create-oasis-offline.py`) they are installed without any network access; otherwise `rtl-sdr` is installed via `apt`. Install is version-aware — each bundled `.deb` is only installed if it's absent or newer than what's on the system, so a stale bundle can't downgrade a package.

Packages installed: `rtl-sdr`, the librtlsdr runtime (`librtlsdr0` on Bullseye/**Bookworm**, `librtlsdr2` on Trixie/sid — the script accepts either), and `libusb-1.0-0`.

**After install, verify the dongle:**

```bash
rtl_test -t                                       # enumerate and test the dongle
rtl_fm -f 144.390M -M fm -s 48000 - | aplay -r 48000 -f S16_LE -t raw -c 1   # receive 2m APRS audio
```

> **Note:** a reboot may be required for the kernel module blacklist to take full effect.
> Use `-s 48000` directly (set the output rate) — this `rtl_fm` build ignores `-r`.

**Enable the dongle as a GrayWolf receive-only APRS feed:**

```bash
python3 features/rtl-sdr/enable-rtl-sdr.py                 # test SDR, install the feed service, print GrayWolf steps
python3 features/rtl-sdr/enable-rtl-sdr.py --check         # test the SDR only, no system changes
python3 features/rtl-sdr/enable-rtl-sdr.py --freq 144.800M --gain 28 --ppm 12
python3 features/rtl-sdr/enable-rtl-sdr.py --help
```

This tests the dongle (measures live audio at the APRS frequency), installs and
enables `aprs-sdr-feed.service` (`rtl_fm … | socat -u -b 1920 - UDP-SENDTO:127.0.0.1:7355`),
inspects GrayWolf's journal, and prints the browser steps to add the `sdr_udp`
device and AFSK/RX channel. Receive-only — an RTL-SDR cannot transmit. Full
writeup and troubleshooting: [`graywolf-rtl-sdr.md`](graywolf-rtl-sdr.md).

---

## OpenWebRX (SIGINT)

A browser-based, **receive-only** SDR receiver/decoder — the OASIS monitoring
front end. From an RTL-SDR it gives a waterfall + audio and decodes a wide range
of modes (voice AM/FM/SSB, CW, RTTY, FT8/FT4/WSPR, SSTV, ACARS, AIS, ADS-B,
POCSAG, …) in the browser on **port 8073**. It cannot transmit — for FT8/2-way
operating you'd use a transceiver + WSJT-X instead.

### Install

```bash
python3 services/openwebrx/install.py        # add the OpenWebRX+ apt repo, install, off by default
python3 services/openwebrx/install.py --check # report install + boot state
```

This adds the **OpenWebRX+** (`luarvique`) upstream apt repo and installs the
`openwebrx` package. It needs **internet** — OpenWebRX+ is a third-party repo and
is **not** vendored into the offline bundle (install it on a connected build/host;
offline bundling is a future task). Targets Debian/Raspberry Pi OS **bookworm/trixie**.

### Running it — RTL-SDR is exclusive

OpenWebRX is installed **off by default** (disabled at boot) because it grabs the
RTL-SDR exclusively — the same dongle the **APRS SDR feed → GrayWolf** path uses.
Manage it from the dashboard **OpenWebRX** card:

- **Start** → stops + disables `aprs-sdr-feed` and `graywolf`, then enables + starts
  `openwebrx` (so it survives a reboot).
- **Stop** → disables + stops `openwebrx`, then re-enables + starts the APRS stack.

So exactly one consumer of the radio is ever active, at runtime *and* after a
reboot. The card's **title** links to the OpenWebRX UI (`:8073`) in a new tab.
Requires `scripts/enable-service-controls.py` (grants the scoped systemctl rule).

### Recommended monitoring profiles (band presets)

OpenWebRX profiles are per-SDR and depend on your dongle, antenna, and local
frequencies, so add them in **Admin → SDR devices → profiles** (open `:8073`, log
in, ⚙ Settings) rather than from a fixed file that could clobber a tuned setup. A
plain RTL-SDR covers ~VHF/UHF (HF needs a direct-sampling dongle or upconverter).
A useful EmComm starter set:

| Profile | Center | Mode | Notes |
|---|---|---|---|
| 2 m APRS | 144.390 MHz | NFM | National APRS; also feeds your GrayWolf interest |
| 2 m calling | 146.520 MHz | NFM | National simplex calling |
| 2 m band | ~145.5 MHz | NFM | Wide view of the 2 m repeater/simplex segment |
| 70 cm band | ~435 MHz | NFM | UHF repeaters/simplex |
| NOAA weather | 162.400–162.550 MHz | NFM | NWS weather radio (pick your local channel) |
| Marine VHF | ~157 MHz | NFM | Coastal/inland waterway traffic |
| Airband | 118–137 MHz | AM | Aircraft / ATC |
| Local repeaters | *your outputs* | NFM | Add your area's repeater output frequencies |

> HF nets (80/40 m, etc.) require an HF-capable SDR or a direct-sampling RTL-SDR
> (Q-branch) — a stock RTL-SDR can't reach them.

### Time matters for the decoders

FT8/FT4/WSPR/SSTV decoding and spot timestamps need an accurate clock. With no
internet, discipline it from GPS (`gpsd` + `chrony`) backed by the RTC — see the
time-sync setup. The dashboard's clock indicator (when present) tells you when
timing is trustworthy.

---

## ADS-B Aircraft

Local ADS-B aircraft tracking (1090 MHz) via **`dump1090-fa`**, decoded
entirely on the Pi and plotted as a live layer on the offline map — no
FlightRadar24 or other internet feed involved, in keeping with the
offline-first prime directive.

### Hardware — shares the RTL-SDR

ADS-B uses the same RTL-SDR dongle as the APRS SDR feed (`aprs-sdr-feed`) and
OpenWebRX, so only one of those SDR modes can run at a time. **Starting ADS-B
stops** `aprs-sdr-feed`, `graywolf`, and `openwebrx` — but **stopping ADS-B
does not auto-restart** any of them; bring the next mode up by hand from the
dashboard. GrayWolf running on a Digirig sound-card TNC (no SDR involved) is
unaffected either way.

### Units

- **`dump1090-fa`** — the Mode S/ADS-B decoder. Owns the RTL-SDR and writes
  live aircraft positions to `aircraft.json`. Off by default.
- **`adsb-api`** — the OASIS recorder + history API. Polls `aircraft.json`,
  records observations, evaluates alerts, and serves it all on
  `127.0.0.1:8086`. Off by default.

### Install

```bash
python3 services/adsb/install.py
```

Or select the **`adsb`** feature in the setup wizard (`python3 setup-oasis.py`).
Either way, ADS-B is installed **off by default** — start it from the
dashboard's **ADS-B Aircraft** card.

**Sourcing:** `dump1090-fa` isn't in base Debian/Raspberry Pi OS apt — it
ships from FlightAware's own apt repo. Install is offline-first: it prefers a
vendored `.deb` under `services/adsb/packages/dump1090-fa/<suite>/`; if none
is found it falls back to adding the FlightAware repo and installing online
(needs internet once). The build-time step that fetches the `.deb` into the
offline bundle is a pending follow-up, so today's offline image installs
ADS-B via the online fallback.

### Alerts

The recorder watches every observation for:

- **Emergency squawks** — transponder codes `7500` (unlawful interference),
  `7600` (radio failure), `7700` (general emergency).
- **Station proximity** — aircraft within a radius of your station's
  coordinates (`station.json`). Distances follow the dashboard's
  imperial/metric toggle.

### History

Every observation is persisted to a local SQLite database at
`/var/lib/adsb/adsb-history.db` (WAL mode), mirroring GrayWolf's history-DB
pattern — nothing heard is lost, even between dashboard sessions.

### Env overrides (for off-Pi testing)

| Variable | Default | Purpose |
|---|---|---|
| `ADSB_DB_PATH` | `/var/lib/adsb/adsb-history.db` | SQLite history database path |
| `ADSB_JSON_PATH` | `/run/dump1090-fa/aircraft.json` | dump1090-fa live aircraft JSON to poll |
| `ADSB_API_PORT` | `8086` | HTTP API port |
| `ADSB_ALERT_RADIUS_KM` | `50` | Proximity alert radius around the station |
| `ADSB_POLL_SECS` | `1.0` | Poll interval for `aircraft.json` |

Set these to point at a local JSON file and a writable DB path to run the
recorder/API on a dev machine without `dump1090-fa` installed.

> Like GrayWolf and OpenWebRX, ADS-B is Pi/Linux only. Pi 3/4/5 is the
> target; Pi Zero is not.

---

## Satellites

Offline satellite pass prediction — and, with an RTL-SDR, live pass audio — for
the amateur and weather birds. Everything at runtime is computed on the Pi: no
online tracker, no `.bsp` ephemeris download. The `/server/satellites/` page
lists the roster, the next passes, and a live sky/footprint view.

### How it's built — two layers

1. **Roster + TLEs (online, periodic).** `build-roster.py` aggregates two
   sources into `configuration/satellites.json`:
   - **SatNOGS** — satellite identity, transmitters, and downlink/uplink
     frequencies + modes.
   - **CelesTrak** — the TLEs (orbital elements) the propagator needs.

   The list and TLEs go stale in a few days — refresh with the **age pill** on
   the Satellites page, or re-run the script when you next have internet.
2. **Pass prediction (offline, runtime).** `predict.py` uses **Skyfield** over
   the SGP4 propagator with a *builtin* timescale, so it never touches the
   network. `/api/satellites/passes` and `/api/satellites/track` return rise/set
   times, peak elevation, and the ground track. Predictions are time-budgeted
   and cached incrementally, so a 150-sat roster never overruns the worker
   timeout.

### Install

Enable **Satellite list (SatNOGS + CelesTrak)** in the setup menu, or run the
scripts directly:

```bash
python3 services/satellites/build-roster.py     # online: build the roster + TLEs
python3 services/satellites/install-predict.py  # Skyfield + numpy into the venv
python3 services/satellites/install-voice.py    # optional: spoken pass alerts
```

`install-predict.py` is **required** for passes/track — without Skyfield (and its
compiled numpy) those endpoints return 500 and the page shows the roster but no
passes. The prediction wheels ship in the offline bundle; the voice stack
(speech-dispatcher + espeak-ng) is a small apt step and is optional — alerts
still chime without it.

### Views & alerts

- **Monitored·1h / All·1h** — the birds you've armed (🔔) versus *every* bird
  passing in the next hour, each sorted by next-pass time.
- **Pass alerts** — a Morse-"V" chime at T-10 minutes; with the voice stack, a
  spoken announcement of which bird is coming and its peak elevation.
- **Sky / footprint** — a live footprint drawn from 15 min before AOS through
  LOS, brightening with elevation.

### Live SDR audio (RTL-SDR)

Arm a downlink button on a monitored bird — armable from **5 min before AOS
through LOS** — and use the header transport to **Listen** (stream the audio to
the browser) or **Record** (capture a WAV under `configuration/sat-recordings/`
for offline APT/LRPT/SSTV decode). Demodulation follows the transmitter mode:
**FM / APRS** (wide FM), **CW** (USB with a 700 Hz tone offset), and **SSB**
(USB / LSB). VHF/UHF Doppler stays within the FM passband, so FM birds need no
active retuning.

Like ADS-B and OpenWebRX, listening **owns the RTL-SDR** — the APRS SDR feed (or
any other SDR mode) must be stopped first. The transport is global (one dongle,
one capture at a time) and the header shows which bird currently holds it.

> Satellites is served by the main OASIS server on **:8083** — no separate port.
> Pass prediction is Pi-cheap; the live-audio capture needs an RTL-SDR (Pi/Linux
> only) and momentary sole use of the dongle.

---

## GPS time sync (gpsd + chrony)

A field station has no internet to pull NTP from, so the clock drifts — and an
inaccurate clock breaks FT8/FT4/WSPR/SSTV decode windows and timestamps. A cheap
USB GPS receiver fixes this: `gpsd` reads the GPS, `chrony` steers the system
clock from it, and the OASIS header **GPS card** shows the result (fix mode,
satellites, HDOP, altitude, lat/lon, and chrony lock status).

```bash
python3 features/gps/install-gps.py                 # autodetect the GPS serial device
python3 features/gps/install-gps.py --device /dev/ttyACM0
python3 features/gps/install-gps.py --check         # report gpsd/chrony status, change nothing
python3 features/gps/install-gps.py --help
```

The script installs `gpsd` and `chrony`, points `gpsd` at the receiver (with
`GPSD_OPTIONS=-n` so it polls without a client connected), and adds the chrony
**SHM refclock** that actually disciplines the clock from GPS. **Run this at home while the Pi has internet** — the `apt install` step needs it once; everything after runs offline.

**Fast cold fix (u-blox only) — ⚠️ service discontinued July 2026.** The *AssistNow Offline* service that supplied satellite almanac data has entered end-of-maintenance/end-of-support status as of July 2026. The `--assist-now` flag in `features/gps/install-gps.py` remains in the code but the Thingstream backend may no longer accept new tokens. For most deployments the standard GPS cold-start time (1–5 minutes for a first fix outdoors with a clear sky) is acceptable — pair GPS with a hardware RTC (next section) and the Pi will keep reasonable time between fixes.

> 💡 **Pair GPS with a hardware RTC** (next section) so the clock survives a full
> power-loss with no GPS lock yet — chrony then rides the RTC until GPS reacquires.

> 📡 **Keep it exercised.** GPS satellite almanac data (the "Keplerian elements" that help the receiver find satellites quickly) ages out after weeks without a sky view. Power the Pi with the GPS connected and take it outdoors for a few minutes every few weeks — treat it like any emergency radio: test before deployment, not on arrival.

---

## Hardware RTC (Witty Pi 3)

Without GPS lock or internet, a Pi has **no idea what time it is** after a reboot
(it has no battery-backed clock). A hardware real-time clock keeps accurate time
across reboots and total power loss — the steady baseline that GPS/chrony then
fine-tune. OASIS supports the **UUGear Witty Pi 3** (DS3231SN RTC on I²C `0x68`):

```bash
python3 features/rtc-hat/enable-rtc.py                  # configure the DS3231 RTC overlay
python3 features/rtc-hat/enable-rtc.py --check          # report status, change nothing
```

The script is idempotent and **requires a reboot**. It enables I²C, adds the
`i2c-rtc,ds3231` overlay (so `/dev/rtc0` appears at boot), removes
`fake-hwclock` (which would otherwise overwrite the real RTC), and neutralises
the `--systz` block in `/lib/udev/hwclock-set` (the classic DS3231 boot-reset
fix).

**After the reboot**, once the system clock is correct (from GPS or a one-time
NTP sync), write it to the RTC once:

```bash
sudo hwclock -w        # set the RTC from the system clock
sudo hwclock -r        # read it back to confirm
```

> ℹ️ Any DS3231-based RTC HAT works with the same overlay; the script is tuned
> for the Witty Pi 3 but the I²C address and overlay are standard. Full hardware
> writeup, troubleshooting, and revert steps: [`rtc-witty-pi.md`](rtc-witty-pi.md).

---

## webssh / Browser Terminal

Installs **[ttyd](https://github.com/tsl0922/ttyd)**, a browser-based SSH/terminal on port 7681. Any browser on the local network can open a login shell on the Pi — no SSH client needed. Surfaced as the **Web SSH** card/link in the dashboard and home page.

```bash
python3 services/webssh/install.py                       # auto: bundled binary if present, else download
python3 services/webssh/install.py --dry-run             # show the helper + unit it would write, change nothing
python3 services/webssh/install.py --verify              # check an existing install (helper, live unit, services, port)
python3 services/webssh/install.py --port 8090           # use a different port
python3 services/webssh/install.py --bind 127.0.0.1      # localhost only
python3 services/webssh/install.py --basic-auth          # prompt for an extra user:pass HTTP gate
python3 services/webssh/install.py --basic-auth admin:s3cret
python3 services/webssh/install.py --help
```

**Install source.** `ttyd` is **not in the Debian/Raspberry Pi OS stable repos**, so this installs the upstream **prebuilt static binary** to `/usr/local/bin/ttyd` (a single self-contained file — no `libwebsockets`/`libuv` dependencies). If the matching binary is bundled at `offline-packages/webssh/ttyd.<arch>` (put there by `create-oasis-offline.py`) it is used offline; otherwise it is downloaded from GitHub releases. Install is version-aware: an already-installed newer ttyd is kept, never downgraded.

**Authentication.** Open the Web SSH card in the dashboard — the page asks for your Pi username and password, then gives you a full terminal. It uses your **normal Pi username and password** (the same ones you set in Imager). `sshd` must be running on the Pi; the installer checks for this and warns if it's not.

<details><summary>Technical note — why ssh-to-localhost instead of /bin/login</summary>

Running `login` directly under ttyd's pty hangs — `login`'s `vhangup()` tears down the WebSocket so its password prompt never appears. `ssh` sets up its own pty and PAM session correctly. The command lives in a small helper script `/usr/local/bin/oasis-webssh-login` rather than inline in `ExecStart`, because systemd performs `$`-expansion even inside single quotes and would otherwise eat the `$u` username variable (making it `ssh @localhost` → root → no prompt).

</details>

**Verifying the install.** Because a systemd unit can silently drift from the source, the installer is self-checking:
- `--dry-run` prints the exact helper script and unit file before writing anything.
- A normal run ends with a verification step that reads back the **live** system — confirming the helper exists and is executable, the running unit's `ExecStart` actually references the helper, and that `webssh`/`sshd` are active and ttyd is listening on the port.
- `--verify` re-runs just those checks against an existing install (handy for confirming a deployed image wasn't altered).

> ⚠️ **Security:** ttyd serves plain HTTP. Keep it on your trusted LAN or hotspot. Do not expose it to the internet without a TLS reverse proxy, and do not pass it over an encryption-prohibited RF link.

Service management:
```bash
sudo systemctl status webssh
sudo systemctl restart webssh
journalctl -u webssh -f
systemctl cat webssh | grep ExecStart   # confirm it runs the helper, not bare `login`
```

---

## Service controls (dashboard power buttons)

By default the dashboard shows each companion service's status but can't change
it — restarting GrayWolf or switching to OpenWebRX means SSHing in. This optional
step adds **START / STOP** buttons to the service cards so you never have to.

```bash
python3 scripts/enable-service-controls.py            # grant for the current user
python3 scripts/enable-service-controls.py --user pi  # grant for a specific user
python3 scripts/enable-service-controls.py --check    # report status
python3 scripts/enable-service-controls.py --disable  # remove the permission
```

It installs a **narrow, validated sudoers rule**
(`/etc/sudoers.d/oasis-service-controls`) that allows *only*
`systemctl start|stop|restart|enable|disable` for the known OASIS units
(GrayWolf, Winlink, Kiwix, webssh, OpenWebRX, the APRS SDR feed) plus one fixed
read-only `tcpdump` for the APRS feed flow meter — and nothing else. The web
endpoints then run `sudo -n …`, so the OS authorizes each exact command and **no
password ever touches the web layer**.

> 🔒 **The OASIS web server's own unit is deliberately excluded** — stopping it
> would kill the dashboard with no way to bring it back from the browser. The
> rule is opt-in and fully reversible with `--disable`.

> ℹ️ OpenWebRX's dashboard card needs this rule, because Start/Stop there also
> flips the APRS stack on/off (the RTL-SDR can only feed one consumer at a time).

---

## ICS Forms

All four forms support: PDF export (fills official FEMA AcroForm templates), CSV import/export, print-optimized layout, and auto-save to localStorage. No server required — open the HTML file directly in any browser.

**pdf-lib dependency** — already vendored at `common/dependencies/pdf-lib.min.js` and shared by all four forms, so there is nothing to download. To refresh it on a prep machine (optional):

```bash
curl -L -o common/dependencies/pdf-lib.min.js https://unpkg.com/pdf-lib@1.17.1/dist/pdf-lib.min.js
```

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

## Tools & calculators

A set of self-contained calculators and loggers. They are **static HTML** — no
server, no internet, no install — so they work from the dashboard, opened
directly, or from the USB bundle. State is saved to `localStorage` per browser.

| Tool | Path | What it does |
|---|---|---|
| **Antenna Calculator** | `tools/antenna-calc.html` | Dipole / vertical / loop lengths and feedline cuts from a target frequency |
| **Power & Battery Budget** | `tools/power-calc.html` | Estimates runtime from battery capacity and a per-device load list; exports a printable report |
| **Grid / Distance / Bearing** | `tools/grid-calc.html` | Maidenhead grid ↔ lat/lon, plus great-circle distance and heading between two points |
| **Gray Line** | `tools/grayline.html` | Gray-line terminator for HF DX timing |
| **Solar / Propagation** | `tools/solar.html` | Band-condition summary and solar indices reference |
| **Net Check-in Log** | `tools/net-log.html` | Logs check-ins during a net (callsign, name, location, traffic) and exports CSV |

> 💡 **Units toggle.** The dashboard's Imperial/Metric pill switches displayed
> temperature, altitude, and speed everywhere at once; the preference is stored
> per browser.

---

## Reference library

The offline operator's bookshelf — all static HTML, served by OASIS or browsable
from the bundle. Nothing here needs the network.

| Section | Path | Contents |
|---|---|---|
| **U.S. band plan** | `tools/band-plan/index.html` | Per-band privilege/segment charts, HF → 23 cm, plus an all-bands view |
| **Quick reference** | `static/quick-ref/index.html` | Q-codes, NATO phonetics, procedure words (incl. ICS plain-language table), RST, ITU prefixes |
| **Radio cheat-sheets** | `static/cheatsheets/index.html` | Per-radio quick cards (Kenwood, Yaesu, Icom, BTECH, …) and APRS bot guides |
| **Radio cards** | `static/radio-cards/index.html` | Per-radio operation cards generated from `static/radio-cards/radio-cards.csv` |
| **Repeater programming guide** | `static/repeater-guide/index.html` | Step-by-step CHIRP programming walkthroughs per radio |
| **GrayWolf handbook** | `static/graywolf-handbook/index.html` | Full offline GrayWolf documentation (channels, PTT, iGate, digipeater, API) |
| **Radio manuals** | `static/radio-manuals/` | **Your own** PDF manuals — drop files into this folder; browse them via the dashboard **Radio Manuals** card |

> 📄 **Radio manuals are not bundled** (copyright). Copy your PDFs into
> `static/radio-manuals/<MAKE MODEL>/` and they appear in the file browser. The
> dashboard card turns green once any PDF is present.

---

## Repeater Book

Browse RepeaterBook listings offline at `static/repeaterbook/repeaterbook.html` — live
search/filter by name, frequency, callsign, city, mode (FM · DMR · YSF · P25 ·
D-STAR · NXDN · M17) and open/closed status, with an EMCOMM auto-badge and CSV
export to a ready-to-import frequency plan.

There is **no bundled data** — RepeaterBook listings are **not redistributable**,
so you download your own:

1. At [repeaterbook.com](https://www.repeaterbook.com), sign in (free) and search your region.
2. Export → **CHIRP** format. Save the `.csv` file on your laptop.
3. Copy it to the Pi as `static/repeaterbook/repeaterbook.csv`:

```bash
# Mac / Linux laptop:
scp /path/to/exported.csv <username>@<hostname>.local:/home/<username>/oasis-emcomm/static/repeaterbook/repeaterbook.csv
```

> 💡 **Windows users:** in WinSCP or FileZilla, navigate to `/home/<username>/oasis-emcomm/static/repeaterbook/` on the Pi and drag the file in, renaming it to `repeaterbook.csv`.

The dashboard **Repeater Book** card turns green when the CSV is present, red when
missing. **Export to Frequency Plan** writes the visible repeaters as CHIRP CSV to
`static/chirp/<datetime>_repeaters.csv` — ready for ICS-205 or CHIRP.

> ⚠️ **Do not redistribute the CSV** — no public repos or shared USB bundles.
> It's gitignored for this reason.

---

## File browser

`server/system/browser.html` is a read-only browser for the user-facing folders
(radio manuals, CHIRP exports, etc.), backed by the server's allowlisted
`/api/fs/*` routes. It's how the dashboard surfaces **Radio Manuals** and lets you
download generated frequency plans without SSH. The allowlisted locations are
fixed in the server; it cannot escape the project's user folders.

> 🔒 Like the rest of OASIS, the file browser has **no authentication** — keep the
> server on a trusted LAN/hotspot.

---

## CM4Stack panel display

For an **M5Stack CM4Stack** (Raspberry Pi CM4) with the built-in ST7789V2 SPI
panel, GT911 touch, and GPIO fan, this configures the on-device OASIS panel
display:

```bash
python3 displays/cm4stack/install-cm4stack.py             # auto-detect the setup phase
python3 displays/cm4stack/install-cm4stack.py --dry-run    # preview config.txt changes
python3 displays/cm4stack/install-cm4stack.py --config-only # only config.txt + headless boot
python3 displays/cm4stack/install-cm4stack.py --service-only # only install the panel service
```

It runs in **two phases** (auto-detected) and **requires a reboot** between them:

1. **Panel not live (first run):** patches `config.txt` with the OASIS-managed
   M5Stack overlay block, sets headless boot, installs the Python runtime deps.
   Exits with code `10` (reboot required) — the panel only appears after reboot.
2. **Panel live (after reboot):** builds and installs the GT911 touch-fix overlay,
   then installs and enables `oasis-panel.service`. May exit `10` again if a second
   reboot is needed for the touch fix.

Exit codes: `0` = done · `10` = reboot required · `1` = error. Full hardware
writeup: [`displays/cm4stack/cm4stack-oasis-panel.md`](../displays/cm4stack/cm4stack-oasis-panel.md).

---

## RGB Cooling HAT

For a **Yahboom RGB Cooling HAT** (PWM fan + status OLED + RGB LEDs), this installs
a small daemon that drives the fan from CPU temperature and shows live stats on the
OLED:

```bash
python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py            # install + enable the service
python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --user pi  # run the service as 'pi'
python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --check    # report status
python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --disable  # remove the service + daemon
```

It enables I²C, installs the apt deps (`python3-pil`, `python3-smbus`,
`i2c-tools`), confirms the HAT is on the bus (`0x0d` fan/RGB MCU + `0x3c` OLED),
and installs the daemon (`rgb-cooling-hat/rgb-cooling-hat.py`) to `/opt` as a
systemd service. The daemon itself needs **no internet** (it has an inlined SSD1306
driver); on a fully offline box, install the three apt deps from your apt cache or
bundled `.deb`s first.

---

## OASIS Dashboard / kiosk display

`oasis-dashboard/dashboard.html` is a touch-friendly dashboard tuned for
dedicated panels. It ships in two resolutions — **800×480** (7″ touchscreen) and
**1920×1200** (10″ wide panel). Use `--resolution` to configure the kiosk
automatically:

```bash
python3 scripts/enable-autostart-pi.py --resolution 800x480     # 7″ touchscreen
python3 scripts/enable-autostart-pi.py --resolution 1920x1200   # 10″ wide panel
```

`--resolution` implies `--with-browser` and sets the kiosk URL to
`http://localhost:8083/oasis-dashboard/dashboard.html?res=<WxH>`, enables touch
events, and sizes the window to match. The page applies the resolution as a
`[data-res]` attribute and the layout uses `oasis-dashboard/kiosk.css` for large
tap targets and a condensed status strip. (The old `--7inch` flag still works as
an alias for `--resolution 800x480`.)

---

## USB / Portable bundle

`scripts/create-oasis-offline.py` updates all offline packages and packages OASIS into a self-contained folder that runs on Windows and Linux with no Python pre-installed. Can be built from macOS, Linux, or Windows.

```bash
python3 scripts/create-oasis-offline.py                           # incremental build (Linux/macOS/Pi target)
python3 scripts/create-oasis-offline.py --for-windows             # also bundle Windows embedded Python runtime
python3 scripts/create-oasis-offline.py --rebuild                 # wipe oasis-offline/ and full clean rebuild
python3 scripts/create-oasis-offline.py --update                  # refresh packages + sync source files into oasis-offline/
python3 scripts/create-oasis-offline.py --verify                  # verify oasis-offline/ checksums
python3 scripts/create-oasis-offline.py --check                   # verify offline assets (CI mode)
python3 scripts/create-oasis-offline.py --help
```

Package update phases (smart: only downloads what changed):

| Phase | What is updated | Destination |
|---|---|---|
| 1 | Python wheels (Flask, gunicorn, psutil — all platforms/versions) | `server/wheels/` |
| 2 | GrayWolf APRS `.deb` (arm64, amd64) | `offline-packages/graywolf/` |
| 3 | Kiwix binaries (linux-aarch64, linux-x86_64) | `offline-packages/kiwix/` |
| 4 | FCC callsign database index | `services/fcc_database/data/` |
| 5 | RTL-SDR `.deb` packages (arm64, armhf, amd64) | `offline-packages/rtl-sdr/` |
| 6 | webssh — ttyd static binaries (`ttyd.aarch64`, `ttyd.armhf`, `ttyd.arm`, `ttyd.x86_64`) | `offline-packages/webssh/` |

**Tip:** Run `scripts/create-oasis-offline.py` regularly to keep packages current — it only downloads what changed. Use `--rebuild` when you need a guaranteed clean slate.

**What it builds:**

```
oasis-offline/
├── _runtime/
│   └── windows/                  ← embedded Python 3.12 + Flask + psutil (~30 MB, --for-windows only)
├── server/
├── static/
├── maps/
├── services/fcc_database/
├── ... (all project files)
└── scripts/
    ├── start-server.bat          ← Windows: double-click to launch
    └── start-server.sh           ← Linux/macOS: run from terminal
```

The dashboard, ICS forms, FCC lookup, **offline maps**, calculators, and the reference library all work from the USB bundle — maps included, since they're served by the core OASIS app. Only the separate Pi companion services — GrayWolf APRS (8080), Kiwix (8081), and the APRS history API (8085) — show as DOWN, because they run as their own services on the Pi.

---

## Updating OASIS

To pull a new version of OASIS when a release comes out:

```bash
cd ~/oasis-emcomm
git pull                          # download the latest code
python3 scripts/setup-server.py  # update server dependencies if requirements changed
```

If OASIS is running as a systemd service, restart it after updating:

```bash
sudo systemctl restart oasis
```

If you're running manually, stop the current server (`Ctrl+C`), then run `scripts/start-server.sh` again.

> ℹ️ `git pull` downloads code changes only — it does not update the FCC database, map tiles, or Wikipedia (those are data files you manage separately with the commands in [Keeping data fresh](#keeping-data-fresh)).

---

## Keeping data fresh

| Data | Command | Frequency |
|---|---|---|
| FCC callsign database | `python3 services/fcc_database/install.py` | FCC publishes every Sunday |
| Offline maps (GrayWolf) | Re-download in GrayWolf UI: **Maps → Offline Maps → Add a region** | As needed |
| Offline maps (MBTiles → PMTiles) | `python3 maps/convert-mbtiles.py <source>.mbtiles` | When you have a new MBTiles source to convert |
| Wikipedia ZIM | `python3 services/kiwix/download-wikipedia.py --edition <edition>` | Monthly snapshots |
| GrayWolf | `python3 services/graywolf/install.py` | Check GitHub releases |
| kiwix-serve | `python3 services/kiwix/install.py --version <new>` | Check download.kiwix.org |
| RTL-SDR packages | `python3 scripts/create-oasis-offline.py` | Run anytime — only downloads if a newer version is in Debian Bookworm |
| webssh (ttyd) packages | `python3 scripts/create-oasis-offline.py` | Run anytime — only downloads if a newer version is in Debian Bookworm |
| Offline wheel set *(maintainers)* | `python3 scripts/create-oasis-offline.py` | Run anytime — updates only if newer packages are available |

---

## Health check (doctor)

`scripts/doctor.py` is a headless health check that mirrors every verification the browser setup page (`server/system/setup.html`) performs — useful for post-deploy confirmation over SSH when no browser is available, or as part of a CI / automated test.

```bash
python3 scripts/doctor.py                          # run all checks
python3 scripts/doctor.py --core                   # core checks only (server, FCC, maps, disk)
python3 scripts/doctor.py --json                   # machine-readable JSON output
python3 scripts/doctor.py --host 192.168.1.10      # check a remote OASIS instance
python3 scripts/doctor.py --host HOST --port PORT  # non-default host and port
```

**Exit codes:** `0` = all core checks pass (optional services may still show warnings) · `1` = one or more core checks failed.

**Core checks** (determine the exit code):

| Check | What it verifies |
|---|---|
| Server reachable | `GET /health` returns 200 on port 8083 |
| FCC index present | `services/fcc_database/data/EN.idx` exists and reports a callsign count |
| Maps directory | `maps/` exists and contains at least one `.pmtiles` file |
| Disk space | Reports free space on the SD card / disk |

**Optional-service checks** (warnings only, do not affect exit code): GrayWolf (8080) · Kiwix (8081) · Winlink (8082) · APRS history API (8085) · Web SSH (7681) · RTL-SDR blacklist · GrayWolf offline-tiles directory.

**Typical usage** — run immediately after setup to confirm the deployment is healthy before going to the field:

```bash
python3 scripts/doctor.py
# All core checks pass  → exit 0
# One core check failed → exit 1 (read the ✗ lines for the cause)
```

For scripted / CI use:

```bash
python3 scripts/doctor.py --json | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d['core_ok'] else 1)"
```

---

## Factory reset / uninstall

To undo everything the setup scripts installed — stop, disable, and remove all
OASIS services, delete OASIS-managed system files, and strip the OASIS blocks from
`config.txt` (DRA-Pi, CM4Stack, and the DS3231 RTC overlay):

```bash
# Dry-run first — shows exactly what would be removed, changes nothing:
python3 scripts/remove-oasis.py

# Report what OASIS state is currently present:
python3 scripts/remove-oasis.py --check

# Perform the teardown, then reboot to drop the config.txt overlays:
python3 scripts/remove-oasis.py --apply
sudo reboot
```

By design it leaves apt packages installed (chromium, lxde, rtl-sdr, gpsd, …) and
**never deletes downloaded data** — maps, the FCC database, ZIMs, and vendored
wheels are expensive (often impossible) to re-fetch offline. The script lists those
paths with their sizes so you can `sudo rm -rf` them manually if you want a fully
clean slate.

---

## Known Limitations

These are by design or accepted tradeoffs, documented here for transparency.

- **No authentication.** Any device on the local network can access OASIS,
  including the file browser. This is intentional for emergency mesh network
  deployments where speed matters more than access control.

- **No HTTPS.** The server runs plain HTTP. Appropriate for trusted local
  networks; not recommended for public or internet-facing deployments.

- **FCC grid squares are ZIP-centroid derived.** The FCC license database
  contains ZIP codes, not GPS coordinates. Grid squares are computed from ZIP
  centroids and may be several miles from the operator's actual location.

- **GrayWolf and Kiwix are external services.** OASIS links to them but does
  not manage their installation beyond the provided scripts, and they show as
  DOWN on the dashboard when not running. (The map tile server is **not**
  external — it is part of the OASIS Flask app and works wherever OASIS runs,
  including the USB bundle.)

- **USB bundle runs on Windows, Linux, and macOS.** Windows uses the embedded
  Python bundled by `scripts/create-oasis-offline.py` (AMD64); Linux and macOS bootstrap
  from the vendored wheels using system `python3` on first run. Only the
  Windows embedded runtime is AMD64-specific.
