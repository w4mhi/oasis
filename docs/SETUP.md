# OASIS — Setup & Configuration Guide

This document covers everything needed to deploy, configure, and maintain OASIS. For an overview of features see the [README](../README.md).

---

## Contents

- [Services index](#services-index)
- [Before you begin (Raspberry Pi)](#before-you-begin-raspberry-pi)
- [Project structure](#project-structure)
- [Guided setup (menu)](#guided-setup-menu)
- [Server setup](#server-setup)
- [Privileged installs from the browser (`oasis-installer`)](#privileged-installs-from-the-browser-oasis-installer)
- [FCC Callsign Lookup](#fcc-callsign-lookup)
- [Using OASIS in the field (no internet)](#using-oasis-in-the-field-no-internet)
- [Offline Maps](#offline-maps)
- [GrayWolf APRS](#graywolf-aprs)
- [Winlink (Pat)](#winlink-pat)
- [Winlink RF via DigiRig](#winlink-rf-via-digirig)
- [DRA-Pi-Zero sound card](#dra-pi-zero-sound-card)
- [DRA-Pi RX LED](#dra-pi-rx-led)
- [DRAWS HAT (audio + GPS)](#draws-hat-audio--gps)
- [Kiwix / Wikipedia](#kiwix--wikipedia)
- [RTL-SDR](#rtl-sdr)
- [OpenWebRX (SIGINT)](#openwebrx-sigint)
- [ADS-B Aircraft](#ads-b-aircraft)
- [Satellites](#satellites)
- [NOAA Weather Radio (SAME/EAS)](#noaa-weather-radio-sameeas)
- [Speech (Piper voice)](#speech-piper-voice)
- [GPS time sync (gpsd + chrony)](#gps-time-sync-gpsd--chrony)
- [GPS L76X HAT (Waveshare)](#gps-l76x-hat-waveshare)
- [Hardware RTC (Witty Pi 3 · BigTreeTech 7″)](#hardware-rtc-witty-pi-3--bigtreetech-7)
- [webssh / Browser Terminal](#webssh--browser-terminal)
- [Service controls (dashboard power buttons)](#service-controls-dashboard-power-buttons)
- [Service Operations console (hardware assignment)](#service-operations-console-hardware-assignment)
- [ICS Forms](#ics-forms)
- [Tools & calculators](#tools--calculators)
- [Reference library](#reference-library)
- [Repeater Book](#repeater-book)
- [File browser](#file-browser)
- [CM4Stack panel display](#cm4stack-panel-display)
- [RGB Cooling HAT](#rgb-cooling-hat)
- [Argon ONE case (fan control)](#argon-one-case-fan-control)
- [OASIS Dashboard / kiosk display](#oasis-dashboard--kiosk-display)
- [USB / Portable bundle](#usb--portable-bundle)
- [Keeping data fresh](#keeping-data-fresh)
- [Updating OASIS](#updating-oasis)
- [Diagnostics page (browser)](#diagnostics-page-browser)
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
| DRAWS HAT modem (both ports) | `/etc/systemd/system/direwolf-draws.service` |
| Kiwix (Wikipedia server) | `/etc/systemd/system/kiwix.service` |
| webssh / browser terminal | `/etc/systemd/system/webssh.service` |
| RTL-SDR APRS feed | `/etc/systemd/system/aprs-sdr-feed.service` |
| ADS-B recorder + history API | `/etc/systemd/system/adsb-api.service` |
| NOAA Weather Radio watch (SAME/EAS) | `/etc/systemd/system/oasis-nwr.service` |
| RGB Cooling HAT | `/etc/systemd/system/rgb-cooling-hat.service` |
| Argon ONE fan | `/etc/systemd/system/argon-fan.service` |
| GeeekPi case (fan + OLED) | `/etc/systemd/system/geek-pi-case.service` |
| DRA-Pi RX LED | `/etc/systemd/system/dra-rx-led.service` |
| Wi-Fi AP fallback | `/etc/systemd/system/oasis-netwatch.service` |
| Privileged installer worker (browser Setup page) | `/etc/systemd/system/oasis-installer.service` (+ `oasis-installer.path`) |

Package-provided units (installed via `apt`/`.deb`, so they live under `/lib/systemd/system/`):

| Feature needing the service | Path to the service unit |
|---|---|
| GrayWolf APRS | `/lib/systemd/system/graywolf.service` |
| OpenWebRX (SIGINT) | `/lib/systemd/system/openwebrx.service` |
| ADS-B decoder | `/lib/systemd/system/dump1090-fa.service` |
| GPS time sync — gpsd | `/lib/systemd/system/gpsd.service` |
| GPS time sync — chrony | `/lib/systemd/system/chrony.service` |

> Features without their own unit: FCC lookup, offline maps and ICS/tools are served by `oasis.service`; the DRA-Pi sound card (ALSA config) and both hardware RTCs (device-tree overlay + `hwclock`) configure the OS directly and register no systemd service.

### Checking any service (works for every unit above)

Every feature in the tables above runs as a **systemd service** — a background program the Pi keeps alive. Three commands answer "is it working / why isn't it" for *any* unit name in the tables, so learn them once and reuse them everywhere below. Replace `<unit>` with a name from the tables (e.g. `graywolf`, `dump1090-fa`, `gpsd`):

```bash
systemctl status <unit>        # is it running?
```
Healthy = a green **`active (running)`** line. Trouble signs: **`inactive (dead)`** (never started), **`failed`** (crashed), or **`activating (auto-restart)`** flipping over and over (a *crash loop* — it keeps dying and restarting).

```bash
journalctl -u <unit> -f        # watch its log live — press Ctrl+C to stop watching
```
This is the single most useful debug tool: it prints new log lines as they happen. Do the thing that should work (send a packet, wait for a GPS fix) and watch for the service to react.

```bash
journalctl -u <unit> -e        # jump to the end of the recent log (for a crash that already happened)
sudo systemctl restart <unit>  # clean stop + start — fixes most "stuck" states
```

> 💡 **When in doubt, run the doctor first.** `python3 scripts/doctor.py` checks the whole station in one shot and prints a ✓/✗ per feature — see [Health check (doctor)](#health-check-doctor). Start there, then point the three commands above at whatever it flags red.

---

## Before you begin (Raspberry Pi)

**Minimum hardware: Raspberry Pi 3 (2 GB) or better** (Pi 4/5 recommended). Older or smaller boards are not supported.

If you are setting up a **fresh Raspberry Pi** for the first time, complete these steps before anything else. If you already have a Pi booted with SSH access and `git` installed, skip to [Guided setup](#guided-setup-menu).

### 1. Flash Raspberry Pi OS

Download and install **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)** on your laptop/desktop.

**Which OS version to pick:**

| Situation | Choose |
|---|---|
| Planning to use an **RTL-SDR dongle** for APRS receive | **Raspberry Pi OS Lite (64-bit) — Trixie** |
| Everything else (no RTL-SDR, or using DigiRig/DRA-Pi) | **Raspberry Pi OS Lite (64-bit) — Bookworm** |

**Lite vs. Desktop:** Lite has no graphical desktop — it boots to a command line and uses less memory (important on a Pi 3). If you want to plug in a monitor and use a mouse/keyboard with a desktop environment, choose "Raspberry Pi OS (64-bit)" (without "Lite") instead. The kiosk/browser option (`--with-browser`) works on Desktop; on Lite, a browser can still run on a separate attached display if configured.

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
git clone https://github.com/W4MHI/oasis
cd oasis
```

> 💡 **Stay in this directory.** Every command in this guide assumes you are inside `oasis/`. If you open a new terminal session or accidentally `cd` somewhere else, type `cd ~/oasis` to get back.

Now continue to [Guided setup](#guided-setup-menu).

---

## Project structure

```
oasis/
├── index.html                  ← Dashboard (start here)
├── setup-oasis.py              ← Guided setup menu
├── start-oasis.py              ← Detached launcher + the one-time privilege grants
├── css/common.css              ← Shared design system
│
├── server/                     ← Flask web server (port 8083) — four things, nothing else
│   ├── app.py                  ← App, blueprint registration, blanket static mount
│   ├── appconfig.py            ← Suite root + port
│   ├── routes/                 ← API blueprints: system · health · hardware · setup ·
│   │                              speech · wifi · files · diagnostics · service_control
│   └── system/                 ← setup.html · diagnostic.html · browser.html
│       (server/wheels/ appears after a bundle build — vendored Flask, gunicorn,
│        MarkupSafe, psutil for every platform × Python 3.9–3.14; gitignored)
│
├── common/                     ← Shared Python + vendored front-end assets
│   ├── lookup.py               ← FCC binary-search engine
│   ├── maidenhead.py           ← ZIP → grid square
│   ├── diagnostics.py          ← The check registry behind doctor.py and /api/diagnostics
│   ├── hardware.py, guardian.py ← Device inventory · resource guardian
│   ├── js/                     ← Shared JS modules (units, geo, format, adsb, sat-*,
│   │                              hw-console, service-registry, clock-chime)
│   ├── css/
│   └── dependencies/           ← Vendored pdf-lib.min.js + glyph fonts
│
├── services/                   ← One directory per service: installer + routes + page
│   ├── fcc_database/data/      ← EN.dat/HD.dat/EN.idx/zipcodes.csv — generated by
│   │                              services/fcc_database/install.py (gitignored)
│   ├── aprs/                   ← APRS API, station store, warnings
│   ├── satellites/             ← Roster, pass prediction, live SDR audio
│   ├── winlink/                ← Winlink (Pat) install + mailbox routes
│   └── adsb/ graywolf/ kiwix/ openwebrx/ rtl-feed/ webssh/
│
├── features/                   ← One directory per HAT / board / hardware add-on
│   ├── draws-audio/ draws-gps/       ← DRAWS HAT (codec + on-board GPS)
│   ├── gps/ gps-L76X/                ← USB GPS · Waveshare L76X GPS HAT
│   ├── rtc-hat/ rtc-raspad/          ← Witty Pi 3 · BigTreeTech 7″ RTCs
│   ├── argon-fan/ rgb-cooling-hat/ geek-pi-case/ cm4stack/
│   └── dra-audio-interface/ rtl-sdr/ speech/
│
├── maps/
│   ├── traffic/map.html        ← The traffic / APRS map page (+ assets/ APRS sprites)
│   ├── mapengine/              ← MapLibre GL JS/CSS, pmtiles.js, glyph fonts
│   ├── tiles/                  ← *.pmtiles land here (gitignored, not tracked)
│   └── routes.py, mapctl.py, convert-mbtiles.py, us-states.geojson
│
├── oasis-dashboard/            ← Kiosk dashboard (dashboard.html), 800×480 + 1920×1200
├── overlays/                   ← Self-compiled device-tree overlays (draws, udrc, m5stack)
├── configuration/              ← Runtime state: station.json · hardware.json ·
│                                  satellites.json · installed-services.json · hazards.json
├── scripts/                    ← Setup, build, and install scripts (see README)
│                                  (enable-graywolf-api.py = APRS history API, port 8085)
├── tools/                      ← Antenna calc, grid/bearing, power, gray line, net log,
│                                  band-plan/
├── tests/                      ← unittest suites + the `node --test` JS harness
│
├── static/
│   ├── ics-205/                ← ICS 205 Radio Communications Plan
│   ├── ics-213/                ← ICS 213 General Message
│   ├── ics-214/                ← ICS 214 Activity Log
│   ├── ics-309/                ← ICS 309 Communications Log
│   ├── oasis-handbook/         ← The offline OASIS handbook (replaced cheatsheets/)
│   ├── graywolf-handbook/      ← GrayWolf offline handbook
│   ├── quick-ref/              ← Q-codes, phonetics, pro-words, RST, ITU
│   ├── chirp/                  ← CHIRP CSVs (samples + saved exports)
│   ├── radio-cards/            ← Per-radio operation cards
│   ├── radio-manuals/          ← Your own PDF manuals (not bundled — gitignored)
│   ├── repeater-guide/         ← Repeater programming with CHIRP
│   └── repeaterbook/           ← RepeaterBook offline browser (CSV gitignored)
│
└── docs/                       ← This file, api.md (full route reference), and more
```

> **Which script do I run, and when?** See the [Setup scripts table in the README](../README.md#-setup-scripts). In short: `setup-server.py` is the only required one; the rest are opt-in per feature. Large data (FCC database, full map regions, PDF manuals, Wikipedia) is downloaded or generated by those scripts — it is not stored in the repo.

> **Working on this code?** OASIS is built by W4MHI with Claude Code as the day-to-day dev partner — plan first, human review before anything merges, same offline-first constraints apply to AI-suggested code as to anyone's. See [How OASIS Is Built](../README.md#-how-oasis-is-built) in the README.

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

- **Sections:** six, in this order —
  *Server* (server, auto-start, GrayWolf, Winlink, Winlink RF via DigiRig, Kiwix,
  Web SSH, OpenWebRX, ADS-B, dashboard service controls, Wi-Fi AP fallback),
  *Display* (Piper speech, the three kiosk modes, desktop icon, CM4Stack panel),
  *Audio* (RTL-SDR tools, the RTL-SDR → GrayWolf APRS feed, DRA-Pi-Zero, DRA-Pi RX LED),
  *GPS* (gpsd + chrony), *RTC* (Witty Pi 3, BigTreeTech 7″), and
  *Content / Data* (FCC, Wikipedia, the satellite roster, the satellite pass-alert
  voice, Repeater Book, ICS Forms).
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

### Starting without systemd — `start-oasis.py`

`scripts/start-server.sh` `exec`s into gunicorn, so it **owns the terminal** that ran
it: close the SSH session and the server goes with it. `start-oasis.py`, at the repo
root, does the same job but starts the server as a **detached background process**
and then exits — you get your prompt back, and the server survives logout. It is the
right launcher for any box without systemd (macOS, Windows, a container), and the
friendliest one on a Pi you have not enabled auto-start on yet.

```bash
python3 start-oasis.py            # start (or restart) detached, then return
```

Re-running is a clean restart: anything already bound to port 8083 is stopped first.
Server output goes to `oasis-server.log` in the repo root — nothing stays attached to
the process's stdout once it exits, so that file is where the log lives.

On **Linux** it also does three one-time chores before starting, each idempotent and
each skipped once it has been done:

1. `scripts/enable-service-controls.py` — the sudoers rule behind the dashboard's
   START/STOP/restart/reboot buttons.
2. `scripts/enable-oasis-installer.py` — the root worker the browser Setup page needs
   (see the next section).
3. **Web SSH (ttyd)** — installed if its unit is absent, then recorded in
   `configuration/installed-services.json` so its dashboard card appears. It is the
   remote-admin lifeline a headless operator needs before anything else.

Steps 1 and 2 ask for your sudo password **in that terminal**, once. After that the
dashboard never needs a password again. All three are skipped on non-Linux, where the
underlying scripts refuse to run anyway.

**Field-debug — did it actually come up?**

```bash
python3 start-oasis.py
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8083/health
tail -20 oasis-server.log
```

**Healthy:** the script prints its own ✓ lines and exits on its own; `curl` prints
`200`; `oasis-server.log` ends with a gunicorn `Listening at: http://0.0.0.0:8083`.

**Broken:** the script exits reporting the port never came up, `curl` prints `000`
(nothing listening), and the tail of `oasis-server.log` carries the real reason —
almost always a missing venv (`python3 scripts/setup-server.py`) or a traceback from
`server/app.py`. A `403`/`404` from `curl` means something *else* owns port 8083.

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

> **If the auto-started server doesn't come up after a reboot:** `sudo systemctl status oasis` (want `active (running)`), then `journalctl -u oasis -e` for the reason. `systemctl is-enabled oasis` should print `enabled`; if not, re-run `python3 scripts/enable-autostart-pi.py`.

<details><summary>Manual service file (if you prefer not to use the script)</summary>

Create `/etc/systemd/system/oasis.service`, replacing `YOUR_USERNAME` with your actual login name (run `whoami` if unsure). This is what `scripts/enable-autostart-pi.py` writes, verbatim — the unit invokes the launcher rather than gunicorn directly, so the venv bootstrap, the port-8083 free, and the gunicorn/Flask fallback all still happen:

```ini
[Unit]
Description=OASIS — Off-grid Amateur Station Integrated Suite
After=network.target
Wants=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/oasis
ExecStart=/bin/bash /home/YOUR_USERNAME/oasis/scripts/start-server.sh
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now oasis
```

</details>

**Server routes — a selection.** The app registers **104 routes**; the table below is the handful you are most likely to `curl` while setting a station up. **[`docs/api.md`](api.md) is the complete, authoritative reference** — go there for anything not listed here.

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
| `GET /api/diagnostics` | Full station health sweep — the JSON behind the Diagnostics page |
| `GET /api/hardware/console` | Device × service matrix for the Service Operations console |
| `GET /health` | JSON health check (FCC index presence · callsign count · name/grid index presence) |
| `GET /api/system` | hostname / IP / CPU / temp / RAM / disk / load / uptime / GPS / chrony (drives stats bar and GPS card) |
| `GET /api/audio` | ALSA sound cards: index, name, capture/playback, USB flag — Linux only |
| `GET /api/service` (POST) | Start/stop a controllable service (graywolf, kiwix, webssh, etc.) |
| `GET /server-ports.json` | Port map consumed by dashboard JS |

> ℹ️ **Static files have no route of their own.** `server/app.py` mounts the whole
> suite root as Flask's static folder (`Flask(__name__, static_folder=SUITE_ROOT,
> static_url_path="")`), so every page and asset — `maps/traffic/map.html`,
> `maps/mapengine/maplibre-gl.js`, `common/js/*`, everything under `static/` — is
> served by that one blanket mount. Nothing needs registering when you add a page.

`/api/system` drives a row of header cards, polled every 5 seconds: **local + UTC clocks**, a **CPU card** (usage / SoC temp / RAM / disk inline, plus a per-core usage bar for each core), a **PROCESSES card** (top 3 processes by CPU, 1-min load average), a **STATUS card** (uptime, Pi power/throttle state, the HW radio-assignment dots described below, and host/LAN IP), and a **GPS card** showing fix mode, satellites, HDOP, lat/lon, altitude, and chrony clock-lock status. Everything is colour-coded green/amber/red by threshold. A **Units** pill (Imperial/Metric) toggles all displayed measurements (temperature, altitude, speed) in one tap — preference is persisted per browser. `/api/audio` (ALSA sound card list) is served but not rendered on the main dashboard; it's deferred to the Setup/health-check page.

The **STATUS** card's Host row is the hostname and LAN IP of the machine actually serving the page. The **HDOP** value on the GPS card is colour-coded across six steps from ideal (bright green, < 1) through excellent, good, moderate, fair, to poor (red, > 20). Service cards include **START/STOP** buttons for controllable services (GrayWolf, Winlink, Kiwix, Web SSH, OpenWebRX), so you never need to SSH in just to restart a service.

Beyond simple start/stop, the **Service Operations** console opens a device→service matrix: reroute an SDR or sound card between services with one tap, **lock** a device to protect its assignment, or **STOP ALL** — with a server-side resource guardian that can run STOP ALL for you when the box overheats. Full writeup: [Service Operations console (hardware assignment)](#service-operations-console-hardware-assignment).

---

## Privileged installs from the browser (`oasis-installer`)

**Run this, or the browser Setup page cannot install anything.** The Flask server
runs as your normal user with no TTY: it can never `apt install`, write to `/etc`,
drop a systemd unit, or edit sudoers. So the Setup page does not try. It writes a
**job file** into `configuration/installer-queue/`, and a **root worker** picks it up
and does the privileged half. Without that worker installed, jobs queue and nothing
ever happens — the page has no way to tell you why.

```bash
python3 scripts/enable-oasis-installer.py            # install + enable  (asks for sudo once)
python3 scripts/enable-oasis-installer.py --check    # report status, change nothing
python3 scripts/enable-oasis-installer.py --disable  # remove the units
```

It installs **two** units:

| Unit | What it does |
|---|---|
| `/etc/systemd/system/oasis-installer.path` | Watches `configuration/installer-queue/*.job.json` and starts the service the moment a job appears |
| `/etc/systemd/system/oasis-installer.service` | `Type=oneshot`; runs `scripts/oasis_installer_worker.py` **as root**, drains every pending job, exits |

It also creates the queue directory owned by the operator (`0775`) so the non-root
server can write into it, and sets `Environment=SUDO_USER=<operator>` on the service
— without it, install scripts resolve "the operator" to `root` and write config
(Winlink's `config.json`, with your password and locator) under `/root/` where the
dashboard never looks.

> ℹ️ **No password ever touches the web layer.** This script asks for sudo once,
> interactively, to install the units. After that the worker *is* root, via systemd,
> so the `sudo …` calls already inside each install script succeed unattended.

> 💡 `python3 start-oasis.py` runs this for you on first launch (see
> [Starting without systemd](#starting-without-systemd--start-oasispy)), as does the
> guided menu. Run it by hand if you enabled auto-start straight from
> `scripts/enable-autostart-pi.py` and never used either.

### Field-debug — the Setup page's install button does nothing

```bash
python3 scripts/enable-oasis-installer.py --check
systemctl is-active oasis-installer.path
ls -la configuration/installer-queue/
curl -s http://localhost:8083/api/setup/permissions
```

**Healthy:** `--check` reports both units present; `is-active` prints `active`; the
queue directory exists, is owned by *you*, and holds no stale `*.job.json`; and
`/api/setup/permissions` returns `"installerDaemonActive": true` alongside
`"serviceControlsGranted": true`.

**Broken:** `is-active` prints `inactive` or `Unit oasis-installer.path could not be
found` — the worker was never installed, and every install you started from the
browser is sitting unread in the queue. `"installerDaemonActive": false` in the API
response says the same thing; the page's own remedy text is the command in
`installerLocalCommand`. A queue directory owned by `root` (rather than you) is the
other failure: the server hits `EACCES` queueing the job and never enqueues at all —
re-running the install fixes the ownership in place.

**Watch a job go through:**

```bash
journalctl -u oasis-installer.service -f
```

Healthy: a burst of install output the moment you click, then the oneshot exits `0`.
Broken: nothing at all appears when you click (the `.path` unit isn't watching), or
the unit enters `failed` — in which case the log carries the install script's own
error, exactly as if you had run it in a terminal.

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
scp -r services/fcc_database/data <username>@<hostname>.local:/home/<username>/oasis/services/fcc_database/
# e.g.  scp -r services/fcc_database/data w4mhi@oasis.local:/home/w4mhi/oasis/services/fcc_database/
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

**Joining a Wi-Fi network from the dashboard:** tap the **Wi-Fi** button under the clock, pick a network, and enter its password. Because the Pi has a single Wi-Fi radio, joining a network takes the OASIS AP offline — any device connected to the AP must reconnect via the Pi's new address.

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
- **Single radio:** the Pi has one Wi-Fi radio, so it is **either** a client **or** the AP, never both. Joining or forgetting a network from the dashboard therefore drops the AP; reconnecting to a known network after an AP fallback happens on the next reboot or via the dashboard.

Check status any time:

```bash
python3 scripts/enable-ap-fallback.py --check
```

### Troubleshooting the access point

These are the real issues seen bringing the AP up on the Pi (Broadcom `brcmfmac` radio), in the order to check them.

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
       └─ maps/traffic/map.html  ← MapLibre + pmtiles.js render in browser
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
scp maps/<region>.pmtiles <username>@<hostname>.local:/home/<username>/oasis/maps/
# e.g.  scp maps/washington.pmtiles w4mhi@oasis.local:/home/w4mhi/oasis/maps/
```

> 💡 **Windows users:** use WinSCP or FileZilla (SFTP, port 22) to drag the `.pmtiles` file into `/home/<username>/oasis/maps/` on the Pi.

Or skip copying entirely: keep `.pmtiles` files on a USB stick and use the **Load maps** button on the map page to browse and load them at runtime. The locations the browser may read from are controlled by the `OASIS_MAP_ROOTS` environment variable (default: `/media`, `/mnt`, `/run/media`, `/Volumes`, `/var/lib/graywolf/tiles`, plus the suite's own `maps/`).

The browser reads the archive directly — no build step, no external tools, no extra Python packages.

### Refreshing JS libraries

MapLibre GL and pmtiles.js are bundled in `maps/mapengine/`. To update:

```bash
cd maps/mapengine
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
| Labels/fonts missing | Glyph PBF files absent | Check `maps/mapengine/fonts/Open Sans Regular/` |
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

### Debug — I configured everything but nothing decodes

Work through these in order — most common cause first:

1. **Is GrayWolf running, and did you restart it after config changes?** GrayWolf only reads channel/device config at startup:
   ```bash
   systemctl status graywolf          # want active (running)
   sudo systemctl restart graywolf    # after ANY device or channel change
   ```
2. **Watch the audio meter while a packet should arrive.** On the web **Dashboard** the **audio-level meter** should *bounce* when the radio passes audio. A flat meter = no audio is reaching GrayWolf (radio volume up? squelch open? cable seated?). Watch the log at the same time: `journalctl -u graywolf -f`.
3. **Is the RX counter climbing?** Dashboard → packet **RX** count, or the **Logs** view. On a live APRS frequency expect a packet every minute or two. Zero for 10+ minutes on a known-busy band points to audio/antenna, not config.
4. **Right sound card selected?** The channel's Input/Output device must match the card you added under Settings → Audio Devices. `aplay -l` lists the cards the Pi actually sees.
5. **Is another radio consumer holding the device?** If you're feeding GrayWolf from an SDR, only one SDR mode runs at a time — see [who owns the RTL-SDR](#rtl-sdr). For a sound-card TNC, make sure OpenWebRX/ADS-B didn't grab the dongle.
6. **Radio basics:** tuned to **144.390 MHz** (North America), volume ~⅓ up, squelch open enough to pass packet audio. APRS is bursty digital "braaap", not a steady tone.

> **Audio levels:** aim for clean decodes with the meter mid-scale. Pinned/clipping = too hot (turn it down); barely moving = too low (turn it up). Same idea as the [DigiRig level tuning](#2-set-the-usb-card-audio-levels-alsamixer).

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

**Debug:**

- Page at `:8082` won't load → `systemctl status pat` (want `active (running)`), then `journalctl -u pat -f`.
- **Telnet connect test** (needs internet): compose a message, then *Action → Connect → telnet*. A **successful** connect authenticates and reports how many messages were exchanged. A **timeout / auth error** = no internet reaching the Pi, or a wrong Winlink password — re-enter it with `pat configure`. (OASIS surfaces the real reason on a failed connect rather than a bare error code.)

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

**After the reboot, confirm the codec appeared:**

```bash
aplay -l | grep -i wm8731     # the DRA-Pi card should now be listed; no output = overlay didn't load
```
If it's absent, the overlay didn't take — re-check the `config.txt` block and that the HAT is firmly seated. Once it lists, `amixer -c <card-number>` should show **`Input Mux`** set to **`Mic`** (the critical capture route the script sets); if it drifted, re-run `enable-dra-pi.py`.

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

## DRAWS HAT (audio + GPS)

The **NW Digital Radio DRAWS** HAT is the go-box alternative to a DRA-Pi or a DigiRig:
one board carrying a TI **TLV320AIC3204** stereo codec wired to **two** mDin6 radio
ports, plus an on-board GPS. OASIS ships it as two registered features that share one
device-tree overlay:

| Feature | Directory | What it does |
|---|---|---|
| **DRAWS audio** | `features/draws-audio/` | The overlay, the ALSA card, the mixer routing, and the 2-channel Direwolf TNC |
| **DRAWS GPS** | `features/draws-gps/` | The on-board GPS on `/dev/ttySC0`, into gpsd + chrony |

Tick **draws-audio** / **draws-gps** on the browser Setup page, or run them directly:

```bash
python3 features/draws-audio/install-draws-audio.py            # autodetect phase
python3 features/draws-audio/install-draws-audio.py --check    # status only
python3 features/draws-audio/install-draws-audio.py --dry-run  # preview the config.txt change
python3 features/draws-audio/install-draws-audio.py --rx-level=-9.0dB   # note the '='
python3 features/draws-audio/install-draws-audio.py --livetest W4MHI-6  # KEYS THE TX

python3 features/draws-gps/install-draws-gps.py                # autodetect phase
python3 features/draws-gps/install-draws-gps.py --check        # NMEA + fix + gpsd/chrony
python3 features/draws-gps/install-draws-gps.py --force        # retarget gpsd from another GPS feature
```

Both follow the **two-phase, exit-10** convention: the first run on a board without the
overlay writes the managed `config.txt` block and exits `10` (reboot required) — the
sound card and `/dev/ttySC0` only appear after a reboot. Re-run afterwards and it goes
straight to the mixer / verification and exits `0`. Whichever feature you install
second finds the overlay already there and needs no second reboot.

Exit codes: `0` = done · `10` = done, reboot required · `1` = error.

**The two radio ports are channels of ONE Direwolf**, not two instances — DRAWS is a
single stereo PCM, and a second Direwolf on it dies with *device busy*. The installer
writes one config with two profiles and one unit, `direwolf-draws.service`:

| Port | Direwolf channel | PTT (BCM) | Profile | Service |
|---|---|---|---|---|
| left mDin6 | `CHANNEL 0` | GPIO 12 | `oasis-draws-winlink` | Winlink |
| right mDin6 | `CHANNEL 1` | GPIO 23 | `oasis-draws-aprs` | APRS |

Those two names are what you will see as device labels in the
[Service Operations console](#service-operations-console-hardware-assignment) and in
the unit description, so a given port is identifiable everywhere. AGW is on `8000`,
KISS on `8001`.

> ⚠️ **DRAWS and the DRA-Pi cannot coexist.** Both want the same 40-pin header and the
> same I²S bus. The Setup page keeps the checkboxes mutually exclusive, but nothing
> stops a direct run of the installer — do that with an `audioinjector-wm8731-audio`
> overlay already in `config.txt` and you lose *every* sound card. `--check` refuses
> on a conflicting overlay.

> ⚠️ **RTL-SDR needs Pi OS Trixie; DRAWS needs a working overlay.** A self-compiled
> `draws.dtbo` (shipped in `overlays/`) brings the HAT up on Trixie, so DRAWS and
> `librtlsdr ≥ 2.0` can coexist on one box. The rule OASIS applies is to override an
> OS overlay **on evidence** — did the hardware actually enumerate — never on `uname`.

### Field-debug — the DRAWS HAT

```bash
python3 features/draws-audio/install-draws-audio.py --check
aplay -l | grep -i draws            # is the card there at all
amixer -c draws contents | head -40 # is anything muted
systemctl status direwolf-draws
python3 features/draws-gps/install-draws-gps.py --check
```

**Healthy:** `aplay -l` lists card `draws`; `--check` reports the overlay present, the
card live, and the mixer routing applied; `direwolf-draws` is `active (running)` and
its log shows both channels; the GPS `--check` reports valid NMEA *and* a fix *and*
that chrony is steering from it.

**Broken, in the order worth checking:**

- **No `draws` card, installer exited `10`** → you haven't rebooted. That is the whole
  fault.
- **Card present, but nothing is heard and nothing transmits** → the aic32x4 codec
  **boots with capture AND line-out muted**. This masquerades perfectly as a wiring
  fault. Check `amixer -c draws contents` *before* you touch a cable; re-run the
  installer (or `--mixer-only`) to reapply and `alsactl store` the known-good routing.
- **`direwolf-draws` in a crash loop with *device busy*** → a second Direwolf (a
  leftover `pat-direwolf`) is holding the PCM. Only one instance may own the card.
- **`/dev/ttySC0` exists but the clock never steers** → the device node proves nothing.
  A dead antenna, a baud mismatch, and a gpsd still pointed at `features/gps` all look
  identical at the node. `--check` separates them: talking vs. silent vs. bytes that
  don't parse, then fix vs. no fix, then whether gpsd/chrony are actually the ones
  reading it. Use `--force` to retarget gpsd from another GPS feature.

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

**Reading `rtl_test -t` (what good vs bad looks like):**

```
Found 1 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00000001
Found Rafael Micro R828D tuner       ← R828D = a V4 dongle; R820T2 = a V3
```
- **`Found 1 device(s)`** + a tuner line = the dongle is seen and usable. Good.
- **`No supported devices found`** = not plugged in, a dead USB cable, **or** the TV driver stole it (next check).
- A flood of **`lost samples`** / the test stalling = USB power problem (undervoltage) — use a good power supply and a short, quality USB cable; check `vcgencmd get_throttled` returns `throttled=0x0`.

**The #1 RTL-SDR failure — the TV driver grabbed the dongle.** Linux ships a DVB-T television driver that claims the dongle before SDR tools can. The install blacklists it, but a reboot may be needed. Confirm it's gone:

```bash
lsmod | grep dvb_usb_rtl28xxu     # want NO output (empty). Any output = TV driver still holding the dongle → reboot
```

**What the audio test should sound like.** The `rtl_fm … | aplay` pipe on 144.390 plays **open-squelch hiss with occasional short digital "braaap" bursts** (those bursts are APRS packets). Continuous static with *no* bursts on a busy band, or dead silence, = no antenna / wrong device, not "it's working." Press Ctrl+C to stop.

**Who's using the RTL-SDR right now?** The dongle is shared — the APRS feed, ADS-B, OpenWebRX, and satellite listening each need it exclusively, so only one runs at a time. To see the current owner:

```bash
systemctl is-active aprs-sdr-feed dump1090-fa openwebrx    # whichever prints "active" holds the dongle
```
(Satellite live-audio also grabs it while a bird is armed — the Satellites page header shows which one.) If a mode reports "no device / busy", stop the active one first.

**Enable the dongle as a GrayWolf receive-only APRS feed:**

```bash
python3 services/rtl-feed/install.py                 # test SDR, install the feed service, print GrayWolf steps
python3 services/rtl-feed/install.py --check         # test the SDR only, no system changes
python3 services/rtl-feed/install.py --freq 144.800M --gain 28 --ppm 12
python3 services/rtl-feed/install.py --help
```

This tests the dongle (measures live audio at the APRS frequency), installs and
enables `aprs-sdr-feed.service` (`rtl_fm … | socat -u -b 1920 - UDP-SENDTO:127.0.0.1:7355`),
inspects GrayWolf's journal, and prints the browser steps to add the `sdr_udp`
device and AFSK/RX channel. Receive-only — an RTL-SDR cannot transmit. Full
writeup and troubleshooting: [`graywolf-rtl-sdr.md`](graywolf-rtl-sdr.md).

**Debug the APRS SDR feed:**

The pipeline is `rtl_fm` (RX audio) → `socat` (sends it as UDP to port 7355) → GrayWolf (decodes it). Check it end to end:

```bash
systemctl status aprs-sdr-feed        # active (running) = good; auto-restart loop = the dongle is busy or gone
journalctl -u aprs-sdr-feed -f        # watch it start and hold the device (Ctrl+C to stop)
```

Confirm audio is actually reaching GrayWolf on 7355 (packets are sporadic — leave it running through a couple of beacons):

```bash
sudo tcpdump -ni lo udp port 7355     # a line every so often = audio is flowing to GrayWolf; Ctrl+C to stop
```

Then the proof it decoded: GrayWolf's **Dashboard RX counter** climbs and packets appear in **Logs**. RX stuck at 0 on a live band with UDP flowing = a gain/antenna issue (raise `--gain`, check the antenna) or GrayWolf's channel isn't pointed at the `sdr_udp` device — see [GrayWolf debug](#debug--i-configured-everything-but-nothing-decodes).

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

> **If you see `dpkg returned an error code (1)` on `dump1090-fa-minimal`** — that's
> expected on a box that already has **ADS-B** installed, and OpenWebRX is fine. The
> OpenWebRX+ repo *recommends* `dump1090-fa-minimal`, whose only binary is
> `/usr/bin/dump1090-fa` — the same path FlightAware's real `dump1090-fa` owns, with
> no `Conflicts:` declared either way, so dpkg refuses the overwrite. The installer
> now vetoes that recommends when `dump1090-fa` is present, and no longer aborts on
> it. OpenWebRX's ADS-B mode uses the real decoder instead. Confirm with:
>
> ```bash
> dpkg-query -W -f='${Version}\n' openwebrx   # a version = installed
> systemctl is-enabled openwebrx              # must print "disabled"
> ```

### Running it — RTL-SDR is exclusive

OpenWebRX is installed **off by default** (disabled at boot) because it grabs the
RTL-SDR exclusively — the same dongle the **APRS SDR feed** and **ADS-B** use.
Manage it from the **START / STOP** button on the dashboard's **OpenWebRX** card:

- **START is gated on a dongle** — and there is currently **no UI to make that
  assignment**. Until an RTL-SDR is assigned to `openwebrx` the button stays
  blocked, and its tooltip ("Assign an SDR dongle in Setup before starting")
  points at UI that no longer exists: the Setup page's Hardware card only
  *detects* devices, and ORX has no column in the HW/SRV matrix (see below). Until
  ORX joins the matrix, assign it over the API:

  ```bash
  # device ids come from configuration/hardware.json -> devices[].id
  curl -s -X POST http://localhost:8083/api/hardware/assign \
    -H 'Content-Type: application/json' -H 'X-OASIS-Request: 1' \
    -d '{"service":"openwebrx","device_id":"rtl-sdr-00000031"}'
  ```

  The card's `device:` row then shows the dongle and START unblocks.
- **Same-dongle conflicts are resolved at click time.** If the APRS SDR feed or
  ADS-B is already running *on that same dongle*, START asks first — **OK** stops
  the other one (click START again once it's down), **Cancel** tries anyway and
  will fail on the shared dongle. A different dongle is no conflict: nothing is
  asked and both keep running.
- **Boot state is left untouched.** START/STOP are plain runtime actions on
  `openwebrx`, so it stays disabled at boot however you leave it — deliberate,
  since it would otherwise seize the dongle on every boot. If you do want it back
  automatically: `sudo systemctl enable openwebrx`. (The APRS feed and ADS-B are
  the opposite — starting one *enables* it and stopping it *disables* it, so their
  choice does survive a reboot. That's why stopping the feed via the conflict
  prompt also takes it off boot.)
- **Stopping ORX does not restart anything.** The APRS stack is not brought back
  for you — start the feed again from its own card when you want it.
- **ORX is not in the SRV OPS matrix.** APRS, ADS-B, Winlink and SAT are routed
  there; OpenWebRX is not, because OASIS cannot tell it which dongle to use — that
  is picked inside ORX's own **Admin → SDR profiles**. The OASIS assignment is
  advisory bookkeeping so the conflict check above knows which dongle ORX wants —
  which is also why it has to be made by hand today.
- **STOP ALL** (matrix header, and the resource guardian's countdown) *does* stop
  `openwebrx`.

The card's **OPEN ↗** button opens the OpenWebRX UI (`:8073`) in a new tab.
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

### Debug — waterfall frozen or "no SDR device"

- **Working looks like:** open `:8073`, pick a profile, and the **waterfall scrolls** with audio when tuned to an active frequency. A **frozen or black waterfall** = the SDR isn't delivering samples.
- **"No SDR device" / black waterfall** almost always means another service is holding the dongle. START only offers to stop a *same-dongle* holder, and only if you confirm — so a consumer on a different dongle, or one you told it to leave running ("Cancel — try anyway"), is still there. Check who owns it — see [who owns the RTL-SDR](#rtl-sdr):
  ```bash
  systemctl is-active aprs-sdr-feed dump1090-fa openwebrx   # only openwebrx should be active
  journalctl -u openwebrx -f                                # watch it try to open the device
  ```
- **Decoders (FT8/FT4/WSPR) spot nothing** even with a good signal: this is almost always the **clock**. Confirm GPS/chrony is disciplined — see [GPS debug](#debug--do-i-have-a-fix-and-is-it-steering-the-clock).

---

## ADS-B Aircraft

Local ADS-B aircraft tracking (1090 MHz) via **`dump1090-fa`**, decoded
entirely on the Pi and plotted as a live layer on the offline map — no
FlightRadar24 or other internet feed involved, in keeping with the
offline-first prime directive.

### Hardware — shares the RTL-SDR

ADS-B uses the same RTL-SDR dongle as the APRS SDR feed (`aprs-sdr-feed`) and
OpenWebRX, so only one of those SDR modes can run at a time. When you START one
of the three and another is already running **on that same dongle**, the dashboard
asks first: **OK** stops the other one (then click START again once it's down),
**Cancel** starts anyway and fails on the shared dongle. Nothing is stopped
without your say-so, and **stopping a mode does not auto-restart** any of the
others — bring the next one up by hand from its card. On a multi-dongle box,
consumers assigned to *different* dongles never conflict and no prompt appears.

`graywolf` itself is never touched by this: GrayWolf opens no dongle (only
`aprs-sdr-feed` does), so it is unaffected either way — including when it runs on
a Digirig sound-card TNC with no SDR involved.

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

### Debug — am I actually hearing planes?

"The service is running" and "I'm decoding aircraft" are different things — the decoder can run flawlessly while hearing nothing (bad antenna, interference, wrong dongle). Here's how to tell them apart.

**The one-command health read** (from the Pi, or any device on the LAN):

```bash
curl -s http://<pi-ip>:8083/api/adsb/health
```
```jsonc
{
  "flowing": true,            // dump1090 is producing fresh data
  "aircraft_count": 7,        // planes seen right now  → >0 = hearing traffic
  "messages_per_min": 643,    // decoded messages/min → climbing over ~30s = healthy
  "signal_dbfs": -14.7,       // strongest signal level
  "noise_dbfs": -25.3         // noise floor (see below)
}
```
- **`aircraft_count > 0`** with **`messages_per_min`** climbing = you're hearing planes. Done.
- **`aircraft_count: 0`, `messages_per_min: 0`** for minutes with an antenna up = running but deaf → keep reading.

> ⚠️ **`/api/adsb/recent` will fool you** — it lists the *last* aircraft heard, possibly hours ago. For "am I hearing planes *right now*", use `messages_per_min` from `/health` (or `aircraft.json` below), never `/recent`.

**Look at the raw live data:**

```bash
cat /run/dump1090-fa/aircraft.json    # "aircraft":[ … ] populated = live; "aircraft":[] = nothing this moment
```

**Is it the antenna, not the software?** The noise floor is the tell:

```bash
grep -oE '"noise":[-0-9.]+|"accepted":\[[0-9,]+\]|"strong_signals":[0-9]+' /run/dump1090-fa/stats.json
```
- **Healthy:** `noise` around **−25 to −45**, and `accepted` (CRC-valid messages) greater than 0.
- **RF front-end problem:** `noise` **near 0** (e.g. `-2.4`) with **`accepted:[0,0]`**, and `journalctl -u dump1090-fa` repeating **`available dynamic range … < required dynamic range`** = the input is a wall of broadband noise, **not** aircraft. No software change fixes a saturated front end. Causes, in order: a **loose or disconnected antenna coax**, **missing ferrite chokes** on the cable, the dongle next to an **EMI source** (USB hub, charger, the Pi itself), or the **wrong dongle/antenna**. Reseat the coax and move the dongle away from noise — the noise floor should drop ~20 dB and messages start flowing within seconds.

**Service-level checks:**

```bash
systemctl status dump1090-fa      # the decoder — must be active (running)
systemctl status adsb-api         # the recorder + API on :8086
journalctl -u dump1090-fa -e      # shows which dongle it opened + its gain/noise history
```

> 📡 **Antenna placement beats everything.** 1090 MHz is line-of-sight — the stock whip indoors may hear nothing while the same dongle near a window or on an outdoor antenna sees dozens of planes. Rule out placement before suspecting software.

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

> Like GrayWolf and OpenWebRX, ADS-B is Pi/Linux only. Pi 3/4/5 is the target.

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

   `create-oasis-offline` runs `build-roster` at **bundle-build time**, so a
   fresh Pi ships with a populated roster — pass prediction works out of the box,
   no first-run internet. The list and TLEs go stale in a few days — refresh with
   the **age pill** on the Satellites page, or re-run the script when you next
   have internet. (If a box ever boots with an *empty* roster, the page falls
   back to the bare TLE cache so passes and tracks still work — you just don't see
   the SatNOGS labels/downlinks until the first refresh.)
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

- **Filters** — the **Filters** menu narrows the roster by **capability**
  (Weather · Voice · FM · APRS · SSTV · Linear · SSB · Data · Crewed), **band**
  (VHF · UHF · L-band), and **orbit class** (LEO · MEO · GEO · HEO). Orbit class
  is derived from the TLE, so it works even on a bare roster before the SatNOGS
  metadata arrives; an active-filter count shows on the collapsed button.
- **Monitored·1h / All·1h** — the birds you're monitoring versus *every* bird
  passing in the next hour, each sorted by next-pass time.
- **Monitoring a bird arms its bell.** Ticking a satellite to monitor it arms its
  pass alert in the same act, and un-ticking it disarms it — one decision, not two.
  The 🔔 on each monitored row is an **override**, not the gate: tap it to keep
  watching a bird on the map while telling it not to wake the shack. A disarmed row
  reads *"Alerts OFF for this bird — monitored, but it will not wake the shack."*
- **Pass alerts** — a Morse-"V" chime at T-10 minutes and again (higher-pitched)
  at T-5; with the voice stack, a spoken announcement of which bird is coming and
  its peak elevation. There is deliberately no alert at AOS — by the time the
  bird is over the horizon it's too late to get the rig on frequency.
  **The bell is stored in the shared roster, not in your browser**, so monitoring a
  bird on a laptop reaches every screen, including the kiosk (see below).
  **Muting is per-device** — silencing the shack kiosk overnight leaves a laptop
  chiming, and vice versa.
- **Sky / footprint** — a live footprint drawn from 15 min before AOS through
  LOS, brightening with elevation.

### Pass alerts on the kiosk (the shack hears them)

The Chromium kiosk chimes and speaks pass alerts itself, so the shack gets a
warning without a laptop being open. It alerts on **whatever bells are armed in
the roster** — which is every bird you're monitoring, from the Satellites page on
any device — and shows a bell on each armed row. The **bell glyph beside the SATELLITES title mutes this
screen** (amber = alerts live, dim + crossed bell = muted); that mute is local to
the Pi, so it doesn't quieten anything else.

Two things must be in place, and **both fail silently**:

```bash
# 1. The kiosk launcher must pass --autoplay-policy=no-user-gesture-required.
#    Re-run the installer if your kiosk predates it, then reboot:
python3 scripts/enable-autostart-pi.py --with-browser

# 2. Optional, for the SPOKEN part (the chime works without it):
python3 services/satellites/install-voice.py
```

> ⚠️ **Why the flag matters.** Browsers keep an audio context *suspended* until
> someone clicks or taps. On a laptop that's invisible — you interacted with the
> page to arm the bell. A kiosk boots unattended and is never touched, so no
> gesture ever arrives and **no chime ever sounds**, with nothing on screen to say
> why. The launcher only gets rewritten when you re-run the command above, so an
> existing kiosk keeps its old flags until you do.

### Debug — the kiosk makes no sound

Work down this list; each step rules out one layer.

```bash
# 1. Does the launcher carry the flag? (empty output = this is your problem)
grep -o 'autoplay-policy=[^ ]*' /usr/local/bin/oasis-browser-launch

# 2. Is a bell actually armed? (the roster is the source of truth, not the browser)
curl -s http://localhost:8083/api/satellites | python3 -c "
import json,sys
armed = [s for s in json.load(sys.stdin)['satellites'] if s.get('bell')]
print('\n'.join(f\"{s['norad']} {s['name']}\" for s in armed)
      or 'NO BELLS ARMED — arm one on the Satellites page')"

# 3. Can the Pi make ANY sound, and out of which device?
aplay -l                                   # list playback devices
speaker-test -c2 -t sine -f 660 -l1        # should be audible; Ctrl+C to stop

# 4. Is a TTS voice installed? (chime works without one; the voice does not)
spd-say "test" && echo "speech-dispatcher OK"
```

- **Flag present, bell armed, `speaker-test` audible, still silent:** check the
  kiosk isn't muted — the bell beside the SATELLITES title should be **amber**, not
  dim. That mute persists across reboots.
- **`speaker-test` is audible but the chime isn't:** the default ALSA sink may be
  the **radio codec** (DRAWS / DRA), not a speaker — the sound is going into the
  transmit audio path. Check which card `aplay -l` lists first, and set the
  desired one as default in `/etc/asound.conf` or `~/.asoundrc`.
- **Chime sounds but nothing is spoken:** the voice stack is missing or Chromium
  was started without `--enable-speech-dispatcher`. Run `install-voice.py`, then
  re-run the kiosk installer and reboot. Alerts are designed to degrade to
  chime-only, so this is cosmetic, not broken.

  Note the flag lives in a **generated** launcher, `/usr/local/bin/oasis-browser-launch`.
  Adding a flag to `scripts/enable-autostart-pi.py` does not update a Pi that was
  set up earlier — nothing re-runs the installer on upgrade. A kiosk installed
  before the flag existed is silently mute:

  ```bash
  pgrep -af chromium | grep -o -- '--enable-speech-dispatcher' || echo "FLAG ABSENT"
  ```

  Healthy: prints the flag. Broken: `FLAG ABSENT` — re-run the kiosk installer
  (`--resolution 800x480` or `1920x1200` for the dashboard kiosk; plain
  `--with-browser` gives the index.html kiosk instead) and restart the session.
- **Chime sounds, but the spoken part is a robotic male voice, not the neural
  one:** the station is speaking through the **fallback** ladder, not Piper.
  Check what the fallback picked (this only runs when the primary path — the
  station's own Piper voice via `/api/speech/say` — failed or isn't installed):

  ```bash
  # in the kiosk browser console
  oasisPickVoice(speechSynthesis.getVoices()).name
  ```

  Healthy: `English (America)+Steph2` (espeak female, the free floor) — a male
  variant such as `+Adam` means neither preference matched; confirm
  `install-voice.py` has run. But first confirm Piper itself is installed and
  reachable — that's the primary path and takes priority over this fallback —
  see [Speech (Piper voice)](#speech-piper-voice) for install + debug.
- **Nothing at all, and you're testing from a laptop browser:** that's expected on
  a page you haven't clicked yet. Click anywhere on the page once, then wait for
  the next alert — the flag only covers the kiosk.

To test without waiting for a real pass, arm a bell on a bird rising in ~15
minutes and leave the kiosk up: you should get a 620 Hz "VVV" at T-10, the spoken
name, then a higher 780 Hz "VVV" at T-5.

### Live SDR audio (RTL-SDR)

Each monitored bird carries a **downlink dropdown** — one `— pick a downlink —`
select listing that satellite's transmitters as `<modes> · <freq> MHz` (e.g.
`FM/FSK/SSTV · 437.8 MHz`). Pick one to arm it — armable from **5 min before AOS
through LOS** — then use the header transport to **Listen** (stream the audio to
the browser) or **Record** (capture a WAV under `configuration/sat-recordings/`
for offline APT/LRPT/SSTV decode). Modes OASIS cannot demodulate (PSK, LRPT, DVB…)
stay in the list, greyed, suffixed *— not supported*, so you can see what the bird
carries even when you can't hear it.

> ⚠️ **Nothing in the dropdown is selectable until an SDR is assigned to the
> station.** With no device assigned to `satellites`, the list still opens and every
> option is readable — but all of them are disabled, the control is dimmed with a
> dashed border, and hovering it says *"no device assigned — set one in Setup →
> Hardware"*. This is the answer to "why can't I arm anything"; assign a dongle in the
> [Service Operations console](#service-operations-console-hardware-assignment) first.

Demodulation follows the transmitter mode: **FM / APRS / FSK / SSTV / APT** (wide FM),
**CW** (USB with a 700 Hz tone offset), and **SSB** (USB / LSB). VHF/UHF Doppler stays
within the FM passband, so FM birds need no active retuning.

**A frequency is not a channel.** `/listen` resolves the downlink by **frequency *and*
mode**, so picking CW rather than FM on a shared frequency selects a genuinely
different demodulator. This used to resolve on frequency alone: asking for CW could
capture with the FM demod and hand back a **WAV of silence**, with nothing on screen
to say why. (It looked correct only because the case that comes up most — the ISS on
437.800 — has three modes that all demodulate to FM.) Today a mode that isn't on that
frequency is refused outright with `MODE_NOT_ON_FREQ` rather than quietly substituted.

Like ADS-B and OpenWebRX, listening **owns the RTL-SDR** — the APRS SDR feed (or
any other SDR mode) must be stopped first. The transport is global (one dongle,
one capture at a time) and the header shows which bird currently holds it.

**Recordings have a disk budget.** At 48 kHz/16-bit mono a pass costs ~5.8 MB per
minute (a 10-minute pass ≈ 58 MB; the 20-minute cap ≈ 115 MB), so
`configuration/sat-recordings/` is swept oldest-first before each capture and
after each stop. Defaults, overridable as environment variables on the server:

| Variable | Default | Meaning |
|---|---|---|
| `SAT_RECORD_MAX_BYTES` | `2147483648` (2 GB) | Total budget for the directory — roughly 35 typical passes. Oldest go first. |
| `SAT_RECORD_MIN_FREE_BYTES` | `1073741824` (1 GB) | Free space required to *start*. Below it, Record returns 507 rather than risk filling the card mid-pass. |
| `SAT_RECORD_MAX_AGE_S` | `0` (off) | Optional age sweep. Set to `259200` for 72h. Off by default because deleting under no space pressure is pure data loss. |

The newest recording is never pruned, so a mis-set budget can't delete the pass
you just captured. There is no delete button yet — clear space by hand with
`rm configuration/sat-recordings/*.wav` if you need to.

### Debug — passes, clock, and live audio

- **Roster shows but every pass is blank:** the Skyfield predictor isn't installed or is erroring. Confirm the endpoint directly:
  ```bash
  curl -s http://<pi-ip>:8083/api/satellites/passes | head -c 200
  # JSON with rise/set times = working; an error / HTTP 500 = run install-predict.py (Skyfield missing)
  ```
- **Every bird says "no pass in 24h" but the roster looks healthy:** suspect the **clock or your station location**, not the satellites — predictions need the correct time *and* your lat/lon. Confirm the clock is GPS-disciplined ([GPS debug](#debug--do-i-have-a-fix-and-is-it-steering-the-clock)) and that your coordinates in `station.json` are right. Then check the **pass floor** — a high `min_elev` empties the list in a way that looks identical to a broken predictor:
  ```bash
  curl -s http://<pi-ip>:8083/api/satellites/passes | python3 -c 'import json,sys; print("min_elev:", json.load(sys.stdin)["min_elev"])'
  # 10.0 = the default; a large number means station.json is filtering your passes out
  ```

**Pass floor (`min_elev`).** A pass is listed only if its *culmination* reaches this
many degrees; lower ones never appear in the roster, the 1 h views or the alerts.
The right value is a property of **your horizon**, not of the satellites — treeline,
roofline, the hill to the north-east — so it lives in `configuration/station.json`
and defaults to `10`:

```json
{ "lat": 35.1234, "lon": -80.5678, "min_elev": 10 }
```

Raise it if low passes are unworkable from your site (a valley, dense trees);
drop it toward `0` if you look out over water. Out-of-range or non-numeric values
fall back to `10` rather than emptying the list. It is one number for a horizon
that is not a circle — you may see 5° south over water and nothing under 20° to
the north — so set it to your *worst* useful direction and judge individual
passes from the `max NN°` on each card.
- **Every downlink option is greyed out — I can't arm anything.** No SDR is assigned
  to `satellites`. The list is deliberately readable-but-inert in that state, so this
  looks like a broken page rather than a missing assignment. Confirm and fix:
  ```bash
  curl -s http://localhost:8083/api/satellites/listen/status | python3 -m json.tool
  # "assigned": false                       → no device; nothing is armable
  # "assigned": true, "device": "RTL-SDR (00000031)"  → you're good
  curl -s http://localhost:8083/api/hardware/console | python3 -m json.tool | head -30
  ```
  Assign one from the [Service Operations console](#service-operations-console-hardware-assignment)
  (or `POST /api/hardware/assign` with `{"service":"satellites","device_id":"…"}`).
  Options that stay greyed *after* a device is assigned are modes OASIS cannot
  demodulate — they carry the *— not supported* suffix and are not a fault.
- **Live audio: hit Listen and hear nothing?** First, [who owns the RTL-SDR](#rtl-sdr) — listening needs sole use of the dongle, so stop the APRS feed / ADS-B first. Then confirm the bird is actually above the horizon (the header shows AOS/LOS). For a NOAA weather bird you should hear the steady **tick-tick** of the APT carrier while it's overhead.
- **A recording comes back as silence.** If this is a **CW** or **SSB** downlink on a
  frequency the bird also uses for FM, make sure you picked the mode you meant — the
  dropdown's value carries both, and the two select different demodulators. A request
  for a mode that isn't on that frequency now fails loudly (`400
  MODE_NOT_ON_FREQ`) rather than returning a silent WAV, so an actual 200 with silence
  points at RF (antenna, gain, the bird being below your horizon), not at mode
  resolution.

> Satellites is served by the main OASIS server on **:8083** — no separate port.
> Pass prediction is Pi-cheap; the live-audio capture needs an RTL-SDR (Pi/Linux
> only) and momentary sole use of the dongle.

---

## NOAA Weather Radio (SAME/EAS)

An always-on watch on the seven NOAA Weather Radio channels (162.400 –
162.550 MHz). An RTL-SDR feeds `rtl_fm` into `multimon-ng`, which demodulates
the **SAME** (Specific Area Message Encoding) header that prefixes every
National Weather Service alert. OASIS parses the header offline — event code,
issuing office, county FIPS list, issue and expiry times — plots the affected
counties on the traffic map, and can speak the alert aloud. No internet, no
NWS API, no subscription: the alert arrives by radio the way it was designed to.

### It is a watch, not a session

**Assigning a dongle to `nwr` in the [Service Operations console](#service-operations-console-hardware-assignment)
is the whole start action, and it holds that dongle until you take it away.**
Releasing the device stops the watch. This is the opposite of the first version,
where the operator pressed *Listen* and the capture lived inside Flask; an alert
you miss because nobody pressed a button is worth very little, and a capture that
dies with a web-server restart or does not come back after a reboot is not a
watch.

So the Weather Radio page has **no Listen, Stop or Scan button**. It pins a
channel, listens in on the audio the daemon is already producing, and shows the
decode log.

### Units

- **`oasis-nwr`** — the watch. Sweeps the band with `rtl_power`, keeps the
  strongest channel, runs `rtl_fm | multimon-ng -a EAS` continuously, records
  every alert, and serves status, audio and a retune notification on
  `127.0.0.1:8089`. Written by the installer but **not enabled** by it — the
  hardware assignment is what starts and enables it, so the station comes back
  after a power cut in the state you left it.

The OASIS web server only reads: it has no encoder, no capture, and never writes
the alert store. `configuration/nwr-alerts.json` has exactly one writer, because
two read-modify-write processes lose records under precisely the conditions that
matter — several counties, several messages, close together.

### Install

```bash
python3 services/nwr/install.py
```

Or tick **`nwr`** in the setup wizard (`python3 setup-oasis.py`) or on the
browser Setup page. It needs the `rtl-sdr` feature (for `rtl_fm` and
`rtl_power`) and `multimon-ng`, which comes from apt — so the install itself
wants internet once, unless `multimon-ng` is in your offline bundle. The SAME
parser and its county tables are vendored in-repo; there is nothing to install
on the Python side.

```bash
python3 services/nwr/install.py --serve   # run the watch in the foreground (dev)
```

### Configuration

`configuration/nwr.json`, written from the Weather Radio page:

| Key | Default | Meaning |
|---|---|---|
| `pinned_channel` | `null` | `null` lets the watch pick the strongest channel; set it to hold one. |
| `channel_hz` | `162550000` | The channel in force (WX7 is the most commonly assigned). |
| `gain` | `"auto"` | Tuner gain. `auto` means *omit `-g`* — see the debug block below. |
| `ppm` | `0` | Crystal correction. |
| `watch_fips` | `[]` | County FIPS codes you care about. **Empty means everything.** |
| `bell` | `false` | Speak matched alerts aloud. Opt-in. |

The bell is off by default and honours the station-wide quiet hours in
`common/quiet-hours.json` (22:00 – 07:00 local by default), with an override
that expires by itself at the next window end — a stormy night is a night, not a
change of policy. There is deliberately **no severity filter**: turning the bell
on means you hear the Required Weekly Test too, and that weekly announcement is
the only regular proof that demodulation, parsing, county matching and speech
all still work.

### Debug — the watch says it is running but I hear nothing

`oasis-nwr` is `active (running)` long before it is *decoding*, and the two
failure signatures below both hit this station for real. Each one reports
healthy everywhere except in the audio.

**Start with the one-command read** (from the Pi, or any device on the LAN):

```bash
curl -s http://<pi-ip>:8083/api/nwr/status | python3 -m json.tool
```
- **Healthy:** `"reachable": true`, `"phase": "listening"`, a `channel` such as
  `"WX7"`, and `last_decode` advancing over the hours. `"alerts_seen": 0` on a
  quiet week is normal — the Required Weekly Test is often the only traffic.
- **Broken:** `"reachable": false` with a `detail` = the daemon is not running
  (`systemctl status oasis-nwr`). `"phase": "retrying"` with `retry_in_s`
  counting down and a `last_error` = it cannot get the dongle; read the error.
  `"phase": "retuning"` or `"retune_pending": true` is **not** a fault — it is
  the deliberate gap while a channel you just pinned takes effect.

> `ok: true` on this endpoint means the request succeeded, not that the watch is
> well. "The watch is not running" is an answer, and it arrives with `ok: true`
> and `watch.reachable: false`. Branch on `reachable` and `phase`, never on `ok`.

**Signature 1 — another service already holds the dongle.**

```bash
journalctl -u oasis-nwr -e | grep -i "usb_claim_interface\|Failed to open"
```
- **Healthy:** nothing, and the log shows `Tuner gain set to automatic.` followed
  by silence (a working capture logs nothing per-sample).
- **Broken:** `usb_claim_interface error -6` — the RTL-SDR is open in another
  process. **This appears within 100–200 ms of spawn**, so a check that only asks
  "did the process start" sees a healthy start and a dead radio. Find the holder
  and take the device away from it, or fit a second dongle:
  ```bash
  systemctl is-active dump1090-fa aprs-sdr-feed openwebrx oasis-nwr   # the other SDR claimants
  curl -s http://localhost:8083/api/hardware/console | python3 -m json.tool | head -40
  ```
  `dump1090-fa`, `aprs-sdr-feed`, `openwebrx` and a satellite capture all claim a
  dongle exclusively. Route the device in the
  [Service Operations console](#service-operations-console-hardware-assignment) —
  routing displaces the previous holder cleanly, which killing `rtl_fm` by hand
  does not.

**Signature 2 — the audio is weak or dead while everything reports healthy.**

```bash
journalctl -u oasis-nwr -e | grep -i "tuner gain"
```
- **Healthy:** `Tuner gain set to automatic.`
- **Broken:** `Tuner gain set to 0.00 dB.` — the tuner is running at **zero
  gain**, not automatic. `rtl_fm` parses `-g` with `atof()`, and `atof("auto")`
  is `0.0`: passing the string `auto` silently asks for 0 dB. Omitting `-g`
  entirely is the only way to ask for real AGC, and `rtl_power` takes `-g` the
  same broken way, so a scan run this way can also pick the wrong "strongest"
  channel. Measured on this station at 162.550 MHz: mean **−33.3 dB → −23.4 dB**
  and peak **−19.4 dB → −5.2 dB** once the flag was dropped. In OASIS this is
  handled centrally by `common/sdr_rx.gain_flag()` — if you are hand-rolling an
  `rtl_fm` command line to test, do not "fix" it by adding `-g auto`.

**Is there anything on the air at all?** `rtl_power` is the instrument. Measuring
RMS on demodulated audio is not: an empty channel demodulates to full-scale
noise and reads as a strong signal, which is how a dead band has been mistaken
for a live one.

```bash
sudo systemctl stop oasis-nwr     # the sweep needs the dongle exclusively
rtl_power -f 162.39M:162.56M:5k -i 1 -e 6 -
sudo systemctl start oasis-nwr
```
- **Healthy:** one of the seven channels stands well clear of its neighbours.
- **Broken:** all 34 bins within a few dB of each other = nothing is being heard.
  That is antenna, coax or siting, not software. 162 MHz is line-of-sight to the
  transmitter; a dongle whip indoors may hear nothing where a rooftop antenna
  hears the same station cleanly. The status endpoint reports this as
  `"scan_weak": true`, and the watch listens on the best channel it found anyway
  rather than refusing to start.

**Nothing decodes, but the audio sounds right.** Listen to it yourself — the
stream is the daemon's own audio, relayed byte-for-byte:

```bash
curl -s http://<pi-ip>:8083/api/nwr/listen/stream --output /tmp/nwr.mp3   # Ctrl+C after ~20 s
```
- **Healthy:** a clear synthesised voice reading the forecast.
- **Broken:** HTTP `409 NWR_NOT_LISTENING` = the watch is not capturing. `503
  NWR_STREAM_UNAVAILABLE` = the daemon has no MP3 encoder (install `ffmpeg`, or
  `sox` with `libsox-fmt-mp3`) or all three stream slots are in use. `503
  NWR_WATCH_UNAVAILABLE` = the daemon is not reachable at all. Audible but
  garbled voice is an RF problem; audible and clear with no decodes for a week is
  most likely a genuinely quiet week — wait for the Required Weekly Test.

**The alert list is empty but the map should show something.**

```bash
curl -s http://<pi-ip>:8083/api/nwr/alerts | python3 -m json.tool | head -30
```
- **Healthy:** `count` greater than 0, with `active` listing the ids that have
  not expired.
- **Broken:** records exist but none is `matched` = your `watch_fips` list does
  not include the counties being alerted. An **empty** `watch_fips` matches
  everything; a wrong one matches nothing, and looks identical to a dead
  receiver. A record with `"clock_suspect": true` was timestamped against a clock
  OASIS does not trust — check [GPS time sync](#gps-time-sync-gpsd--chrony).

> The `oasis-nwr` daemon binds **127.0.0.1 only**, like `adsb-api`. Curling
> `http://<pi>:8089/status` from another machine will always fail; that is by
> design. Go through the Flask proxy on `:8083`, or run the curl on the Pi.

---

## Speech (Piper voice)

A station-wide text-to-speech service: any subsystem asks `common/speech.py`
(or `GET /api/speech/say`) to speak a sentence and gets back a WAV. Satellite
pass alerts are the first consumer — guardian and Winlink announcements are
next. **Optional and opt-in.** Without it, alerts still speak, just with the
espeak-ng fallback voice (see [Satellites → pass alerts](#satellites)) — a
station that never installs this feature behaves exactly as it always has.

> **Credits.** The voice is **Jenny (Dioco)** (`en_GB-jenny_dioco-medium`, from
> the [Jenny TTS dataset](https://github.com/dioco-group/jenny-tts-dataset)),
> and the engine is [Piper](https://github.com/OHF-voice/piper1-gpl)
> (Open Home Foundation, GPL-3.0), which embeds espeak-ng as a phonemiser only.
> The dataset's licence is a **custom attribution licence — not CC-BY** — and it
> asks that any interface generating audio from the voice credit it as "Jenny",
> and where practical "Jenny (Dioco)". OASIS ships neither the engine nor the
> model: your own `create-oasis-offline.py` run fetches both from upstream. The
> installer writes the full notice to `features/speech/voices/ATTRIBUTION.txt`
> so it travels with the model — **pass it along if you hand this station or its
> USB image to someone else.**

Piper runs as a **subprocess per request**, not an in-process library: keeping
a ~60 MB model resident in every gunicorn worker is the difference between
comfortable and swapping on a 2 GB Pi 3. The cost — a real synthesis — is paid
once per distinct sentence; results are cached by content hash under
`features/speech/cache/` (50 MB budget, oldest evicted first, the newest entry
never pruned).

```bash
# menu: tick "Spoken announcements (Piper voice)" under Display
python3 setup-oasis.py

# or directly:
python3 features/speech/install.py
python3 features/speech/install.py --uninstall
```

There is also a **speech** checkbox on the browser Setup page
(`http://<pi-ip>:8083/server/system/setup.html`) — tick it and run the plan, same
install, no terminal. It needs the privileged installer worker in place; see
[Privileged installs from the browser](#privileged-installs-from-the-browser-oasis-installer).

Needs **Python 3.11+** (onnxruntime publishes no older wheels) and is skipped
cleanly — leaving the espeak-ng voice in place, nothing changed — on 32-bit ARM
(`armv7l`/`armv6l`, no onnxruntime wheel there). The ~60 MB voice model has
**no online fallback**: `scripts/create-oasis-offline.py` fetches it at
bundle-build time on a connected machine; OASIS itself never downloads it at
install time. A bundle built without the engine or the voice installs cleanly
and simply reports what's missing.

### Debug — is it installed, and can this box actually hear it?

"The wheel installed" and "this station can speak" are different things, and
so are "the server can speak" and "the Pi's own speaker makes sound." Four
commands separate all of it:

```bash
curl -s localhost:8083/api/speech/status              # available, voice, player
curl -s -o /tmp/t.wav -w '%{http_code} %{size_download}\n' \
     'localhost:8083/api/speech/say?text=test'        # 200 and a non-zero size
pw-play /tmp/t.wav                                    # does this box make sound at all
ls -la features/speech/voices/                        # .onnx AND .onnx.json, both
```

1. **`/api/speech/status` says `available:false`, but the wheel is installed.**
   This is the finding that costs the most time. Piper will not load a voice
   model without its **`.onnx.json` sidecar** — a `.onnx` sitting alone in
   `features/speech/voices/` is invisible to it, and the station reports no
   voice even though the 60 MB model is right there:
   ```bash
   ls -la features/speech/voices/
   ```
   Healthy: a matched pair, e.g. `jenny.onnx` and `jenny.onnx.json`. Broken: a
   `.onnx` with no matching `.onnx.json` (or vice versa) — rebuild the offline
   bundle on a connected machine (`scripts/create-oasis-offline.py`) so both
   files ship together, or re-run `features/speech/install.py`.

2. **`/api/speech/say` returns `200` and real bytes, but the Pi's own speaker
   stays silent — and the same page in a browser is fine.** That last part is
   the tell: the audio itself is good, so this is **`XDG_RUNTIME_DIR`, not the
   sound card**. `oasis.service` is a *system* unit running as the operator's
   user (`scripts/enable-autostart-pi.py`) — right UID, no session environment.
   PipeWire's socket lives at `$XDG_RUNTIME_DIR/pipewire-0`, owned by that same
   UID, so a player the server spawns can't find a PipeWire it's entitled to
   use. `common/speech_play.py` already falls back to `/run/user/<uid>` when
   the variable is unset and that directory exists; if playback still fails,
   confirm the socket is actually there for that UID:
   ```bash
   ls /run/user/$(id -u)/pipewire-0
   ```
   Healthy: the socket exists. Broken: no such file — that user's systemd/
   PipeWire session hasn't started yet at boot (it normally starts at login).
   `loginctl enable-linger $(whoami)` keeps it running from boot without a
   login and is the usual fix for a headless/kiosk box.

3. **No player found at all.** `common/speech_play.py` probes `pw-play`,
   `paplay`, `aplay`, in that order:
   ```bash
   which pw-play paplay aplay
   ```
   Healthy: at least one resolves. Broken: none do — a headless box legitimately
   has nowhere to play a local test utterance, and that's fine: `/api/speech/say`
   still serves browsers on the LAN either way; only the Pi's own speaker is
   affected.

4. **Text gets rejected outright.** `/say` validates before ever calling Piper:
   ```bash
   curl -s 'localhost:8083/api/speech/say?text='
   ```
   `400 {"ok":false,"code":"EMPTY_TEXT",...}` is working as designed (also
   `TEXT_TOO_LONG` past 300 characters, `INVALID_TEXT` for control characters)
   — satellite names and message subjects are not text OASIS authored, so
   `/say` never trusts them blindly.

> Browser playback and the Pi's own local playback are two *different* things
> this service does. A healthy `curl` with a silent Pi speaker is a normal
> state to see while debugging, not proof the feature is broken — the kiosk and
> any laptop on the LAN still hear the same voice.

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

**Fast cold fix (u-blox only) — ⚠️ service discontinued 31 May 2026.** The *AssistNow Offline* service that supplied satellite almanac data entered end-of-maintenance/end-of-support on **31 May 2026** (u-blox product change notice); its successor is *AssistNow Live Orbits / Predictive Orbits*. The `--assist-now` flag in `features/gps/install-gps.py` remains in the code but the Thingstream backend may no longer accept new tokens, and Thingstream signup warns that the platform is for commercial use rather than individual consumers. Note also that AssistNow is **u-blox only** — the Waveshare L76X HAT is a Quectel L76 and cannot use it at all. For these reasons the automatic-update loop deliberately does **not** manage AssistNow. For most deployments the standard GPS cold-start time (1–5 minutes for a first fix outdoors with a clear sky) is acceptable — pair GPS with a hardware RTC (next section) and the Pi will keep reasonable time between fixes.

> 💡 **Pair GPS with a hardware RTC** (next section) so the clock survives a full
> power-loss with no GPS lock yet — chrony then rides the RTC until GPS reacquires.

> 📡 **Keep it exercised.** GPS satellite almanac data (the "Keplerian elements" that help the receiver find satellites quickly) ages out after weeks without a sky view. Power the Pi with the GPS connected and take it outdoors for a few minutes every few weeks — treat it like any emergency radio: test before deployment, not on arrival.

### Debug — do I have a fix, and is it steering the clock?

Two separate questions: **(a)** is the receiver seeing satellites, and **(b)** is chrony actually using it to set the clock. Check them in that order.

**(a) Is the GPS locked?**

```bash
cgps -s      # live GPS screen — press 'q' or Ctrl+C to quit
```
Read the top-left of the screen:
- **`Status: 3D FIX`**, **`Used: 6`** (or more satellites), **`HDOP`** around **1–2** = a solid fix. This is what you want before trusting the clock or beaconing your position.
- **`Status: NO FIX`** but the satellite table on the right lists birds (any SNR at all) = the chain works and it's **acquiring**. Go outside with a clear view of the sky; a cold receiver needs 1–5 minutes to download the almanac, and indoors it may never lock.
- **`Status: NO FIX` and the satellite table is EMPTY** = the receiver hears *nothing*. That's the **antenna, not the sky**, and waiting will not fix it: check it's in the GPS jack (a combo board has more than one SMA), that an *active* antenna is getting bias power, and that the cable isn't damaged.
- **Blank screen / "connection refused" / "no gpsd"** = gpsd isn't running or isn't pointed at the receiver → `systemctl status gpsd`.

> **Seen vs. used is the fork that matters.** "0 satellites in use" is the same
> reading whether the antenna is unplugged or the receiver is simply warming up,
> and those need opposite responses. The count of satellites *in view* is what
> tells them apart — which is why the feature `--check` scripts now report both.

Prefer raw numbers to the full screen?

```bash
gpspipe -w -n 10 | grep -oE '"mode":[0-9]|"uSat":[0-9]+|"nSat":[0-9]+|"hdop":[0-9.]+'
# "mode":3 = 3D fix (good) · "mode":2 = 2D (marginal) · "mode":1 = no fix
# "nSat" = satellites SEEN · "uSat" = used · "hdop" < 2 = good geometry, > 5 = poor
# nSat 0 = antenna fault (see above). nSat high + uSat 0 = still acquiring.
```

Or let the feature script do the interpreting for you — it reports device nodes,
whether the receiver is talking, how many satellites it sees versus uses, and
whether gpsd and chrony are steering:

```bash
python3 features/gps/install-gps.py --check          # USB / serial GPS
python3 features/gps-L76X/install-gps-l76x.py --check
python3 features/draws-gps/install-draws-gps.py --check
```

**(b) Is chrony disciplining the clock from GPS?**

```bash
chronyc sources
```
Find the **`GPS`** line and read the **first two characters**:
```
MS Name/IP address    Stratum Poll Reach LastRx Last sample
#* GPS                     0    4   377    21   +12us[ +12us] +/- 200us   ← GPS IS the clock (GOOD)
#? GPS                     0    4     0     -     +0ns[  +0ns] +/-   0ns   ← unreachable: gpsd down / no fix yet
#x GPS                     0    4   377    18  +357ms[+357ms] +/- 200ms   ← seen but REJECTED as wrong
```
- **`#*`** = GPS is the **selected** source — the clock is being set from GPS. This is the goal for an offline field station.
- **`#?`** = chrony can't reach the GPS (gpsd down, or no fix yet).
- **`#x`** = "falseticker": chrony sees GPS but thinks it's wrong and ignores it — often because a *better* source (internet NTP) is present. With the internet unplugged in the field, a healthy GPS should promote itself to `#*`.

Then confirm the clock is genuinely synced:

```bash
chronyc tracking
```
- **`Leap status : Normal`** with a tiny **`System time`** offset (micro/milliseconds) = clock is disciplined and trustworthy.
- **`Leap status : Not synchronised`** = the clock is free-running; FT8/WSPR/SSTV timing and timestamps are not reliable yet.

> On the dashboard, the **GPS card** tells the same story without a terminal: a **3D** fix, a healthy satellite count, HDOP in green, and a chrony "locked" indicator mean you're good. If the card is blank, run `systemctl status gpsd` first, then `cgps -s`.

---

## GPS L76X HAT (Waveshare)

The **Waveshare L76X GPS HAT** (Quectel L76 GNSS — GPS/BDS/QZSS) wires onto the 40-pin
header rather than USB: TX/RX/5V/GND, and optionally a **1PPS** line on **BCM GPIO4**.
It is the HAT alternative to the generic USB receiver in
[GPS time sync](#gps-time-sync-gpsd--chrony), and it uses the same gpsd + chrony
plumbing underneath.

```bash
python3 features/gps-L76X/install-gps-l76x.py                 # enable UART, verify NMEA, wire up gpsd/chrony
python3 features/gps-L76X/install-gps-l76x.py --pps           # also add the 1PPS overlay (GPIO4)
python3 features/gps-L76X/install-gps-l76x.py --device /dev/ttyAMA0
python3 features/gps-L76X/install-gps-l76x.py --no-gpsd       # UART + NMEA verify only
python3 features/gps-L76X/install-gps-l76x.py --force         # retarget gpsd from features/gps
python3 features/gps-L76X/install-gps-l76x.py --check         # report status only
python3 features/gps-L76X/install-gps-l76x.py --keep-console  # don't disable the serial login shell
```

It adds `enable_uart=1`, **removes the serial console** so it stops stealing bytes from
the GPS, installs `python3-serial`, verifies NMEA output, and (unless `--no-gpsd`)
points gpsd + chrony at the device. Exit codes: `0` = done · `10` = done, reboot
required · `1` = error. The `apt` steps need internet once.

> ⚠️ **Only one GPS may discipline the clock.** This feature and `features/gps` (generic
> USB GPS) are mutually exclusive alternatives, not additive. Running one after the
> other warns and refuses to retarget gpsd unless you pass `--force`.

> ⚠️ **GPIO4 is contended.** The 1PPS wire lands on BCM GPIO4
> (`dtoverlay=pps-gpio,gpiopin=4`) — the same pin Argon's `argononed` watches for the
> case's soft power button. With both running, one pulse per second reads as a stream
> of power-button presses and the Pi reboots then shuts down the moment the HAT is
> seated. See [Argon ONE case (fan control)](#argon-one-case-fan-control), whose
> installer masks `argononed` for exactly this reason.

### Field-debug — the L76X

```bash
python3 features/gps-L76X/install-gps-l76x.py --check
ls -l /dev/ttyS0 /dev/ttyAMA0 2>/dev/null
gpspipe -r -n 5                     # raw NMEA, if gpsd is running
chronyc sources | grep -i -E 'gps|pps'
```

**Healthy:** `--check` reports the UART enabled, the serial console gone, valid NMEA
arriving, a GGA fix with a satellite count, and chrony carrying a `GPS`/`PPS` source.

**Broken:** the device node exists but `--check` says the receiver is *silent* or
*bytes that don't parse* — that is a baud mismatch or the serial login shell still
holding the port (`--keep-console` was used, or the change hasn't been rebooted into).
Valid NMEA but **no fix** is an antenna/sky problem, not a software one — a cold start
outdoors takes 1–5 minutes. NMEA and a fix but chrony ignoring it means gpsd is still
pointed at another GPS feature: `--force`.

---

## Hardware RTC (Witty Pi 3 · BigTreeTech 7″)

Without GPS lock or internet, a Pi has **no idea what time it is** after a reboot
(it has no battery-backed clock). A hardware real-time clock keeps accurate time
across reboots and total power loss — the steady baseline that GPS/chrony then
fine-tune. OASIS supports two boards, one feature each — tick either (or both) in
**Setup**, or run its CLI:

| Board | Chip | Bus | Feature | CLI |
|---|---|---|---|---|
| UUGear **Witty Pi 3** (Rev1/Rev2) | DS3231SN @ `0x68` | GPIO ARM, `i2c-1` | `rtc` | `features/rtc-hat/enable-rtc.py` |
| **BigTreeTech 7″** touchscreen (Raspad) | PCF8563 @ `0x51` | DSI ribbon, `i2c-10` | `rtc-raspad` | `features/rtc-raspad/enable-rtc.py` |

```bash
python3 features/rtc-hat/enable-rtc.py               # Witty Pi 3 (DS3231)
python3 features/rtc-raspad/enable-rtc.py            # BigTreeTech 7" (PCF8563)
python3 features/rtc-raspad/enable-rtc.py --check    # report status, change nothing
```

Each script is idempotent and **requires a reboot**. It writes its board's
`config.txt` lines, removes `fake-hwclock` (which would otherwise overwrite the
real RTC), and neutralises the `--systz` block in `/lib/udev/hwclock-set` (the
classic i2c-rtc boot-reset fix). The lines per board — the `i2c-rtc` one is the
feature's own, the other is a prerequisite it will add but never remove:

```ini
# rtc (Witty Pi 3)
dtparam=i2c_arm=on
dtoverlay=i2c-rtc,ds3231

# rtc-raspad (BigTreeTech 7")
dtoverlay=vc4-kms-dsi-7inch,dsi1
dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi
```

> ⚠️ **The installer owns exactly one line: its `i2c-rtc` overlay.** That line
> goes inside the feature's own `# --- OASIS RTC <board> ---` block, which is all
> an uninstall strips. Everything else is a **prerequisite** — added when missing,
> but always written *outside* the block and **never removed**, because it belongs
> to hardware that outlives the clock. `dtoverlay=vc4-kms-dsi-7inch,dsi1` *is* the
> Raspad's screen, so no uninstall path may ever take it out — whether it was
> already in `config.txt` (the usual case, next to the stock `vc4-kms-v3d` line)
> or this installer added it. Same reasoning for `dtparam=i2c_arm=on`, shared with
> every other I²C user. Both boards may be installed on one Pi — the blocks are
> per-board, so removing one never touches the other.
>
> After installing on a box that needed the display overlay, `config.txt` reads:
>
> ```ini
> # rtc-raspad prerequisite (added by features/rtc-raspad/enable-rtc.py; NOT removed on uninstall — it belongs to the hardware, not the clock)
> dtoverlay=vc4-kms-dsi-7inch,dsi1
> # --- OASIS RTC bigtreetech-7in (managed by features/rtc-raspad/enable-rtc.py) ---
> dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi
> # --- end OASIS RTC bigtreetech-7in ---
> ```

The PCF8563 sits on the **DSI ribbon's** I²C bus, not the GPIO header — which is
why it never appears on `i2cdetect -y 1`. Use `i2cdetect -y 10` (the chip shows
as `UU` at `0x51`), or just read the driver name:

```bash
cat /sys/class/rtc/rtc0/name     # rtc-pcf8563 10-0051   (BigTreeTech 7")
                                 # rtc-ds3231  1-0068    (Witty Pi 3)
```

**After the reboot**, once the system clock is correct (from GPS or a one-time
NTP sync), write it to the RTC once:

```bash
sudo hwclock -w        # set the RTC from the system clock
sudo hwclock -r        # read it back to confirm
```

**Confirm the RTC hardware is really there** (after the reboot):

```bash
ls -l /dev/rtc0        # the device must exist — missing = overlay didn't load, re-check config.txt + reboot
sudo hwclock -r        # must print a SANE current date — a 1970/2000 date = overlay failed or the coin cell is dead
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

> ℹ️ OpenWebRX's dashboard card needs this rule for its own START/STOP, and again
> when you accept its offer to stop a conflicting RTL-SDR consumer — both go
> through `systemctl`, so without the rule the card can only report state.

---

## Service Operations console (hardware assignment)

START/STOP answers "is this service running". It does not answer the question a
station with one dongle and three SDR features actually has: **which radio is
plugged into which service right now, and what happens if I move it.** The **Service
Operations** console does — the mixer-board rail beside the dashboard's service strip,
or the `APRS · ADS-B · ORX · SAT` dot bar on the kiosk.

It renders a **device × service matrix**: attached hardware down one axis, the four
routable services (`aprs`, `adsb`, `winlink`, `satellites`) across the other. Tap a
cell to put that service on that device. The move is exclusive and immediate — the
incumbent is displaced, its unit stopped, the new one started. OpenWebRX is
deliberately *not* a column: it is self-configured and controlled from its own card
(see [OpenWebRX](#openwebrx-sigint) for the `curl` you need to assign it a dongle).

Devices are discovered and auto-declared on every poll — RTL-SDRs (by EEPROM serial),
DigiRig, DRA-Pi, and both DRAWS ports. Assignments persist to
`configuration/hardware.json`:

```json
{ "version": 1,
  "devices": [ { "id": "rtl-sdr-00000031", "kind": "rtl-sdr", "serial": "00000031" } ],
  "assignments": { "aprs": "rtl-sdr-00000031" } }
```

**RTL-SDR is a shared kind** — several services may point at the same dongle, and the
console shows you which one is actually running. `digirig`, `dra-pi` and `draws` are
exclusive: a second claim is refused with `409`.

### Lock

The padlock on a device pins it to its current assignment. A locked device cannot be
claimed as a target (`target-locked`), a service cannot be moved *off* it
(`source-locked`), it is skipped by auto-assign, and even the matrix's stop toggle
refuses it — a lock protects against *any* displacement. Locks live in
`hardware.json` as `"locked": true` on the device; absent means unlocked. `POST
/api/hardware/lock` sets and clears it.

### STOP ALL — and the one service it never stops

**STOP ALL** stops every controllable OASIS service in one tap: `adsb-api`,
`aprs-sdr-feed`, `direwolf-draws`, `dump1090-fa`, `graywolf`, `graywolf-api`,
`kiwix`, `openwebrx`, `pat`, `pat-direwolf`. It is a plain stop, not a disable —
nothing is unenrolled from boot.

**Web SSH is deliberately excluded**, as are the `oasis` server itself and `gpsd`:

```python
# server/routes/hardware.py
_EMERGENCY_STOP = _CONTROLLABLE_SERVICES - {"webssh"}
```

Killing Web SSH would sever the operator's remote connection to the box at the worst
possible moment. You must never lose the way in.

### Resource guardian

A background thread on the *server* — independent of any open browser tab — samples
SoC temperature, CPU, and memory every 3 seconds. Crossing a threshold arms a
**30-second cancellable countdown**, shown as a banner on every dashboard and kiosk
currently open, then runs the same STOP ALL (same Web SSH exclusion) if nobody
cancels. Cancelling drops it into a cooldown state that cannot immediately re-arm.

| Metric | Default threshold | Floor you may tune it to |
|---|---|---|
| SoC temperature | `80.0` °C | `45.0` °C |
| CPU | `95.0` % | `50.0` % |
| Memory | `92.0` % | `60.0` % |

> ⚠️ **The guardian is on by default**, not opt-in — a missing or unreadable config
> file means *enabled with the conservative defaults above*. Config lives in
> `configuration/guardian.json` (`{"enabled": bool, "thresholds": {...}}`), read by
> `GET /api/hardware/guardian` and written by `POST /api/hardware/guardian/config`.
> Thresholds are clamped to the floors — a limit below normal idle would arm
> perpetually. Set `"enabled": false` to switch it off entirely.

### The API

Thirteen paths under `/api/hardware/` (fourteen registrations — `devices` has both a
GET and a POST). Every POST requires the `X-OASIS-Request: 1` header; GETs do not.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/hardware/devices` | Device list + per-service `{device_id, ok, reason}`; auto-declares what it finds |
| POST | `/api/hardware/devices` | Declare a device into `hardware.json` (naming only — never assigns) |
| GET | `/api/hardware/detect` | Read-only enumeration of attached candidates (rtl_sdr, alsa, serial, usb) |
| POST | `/api/hardware/assign` | Assign a device to a service (409 with `holder` if exclusively held) |
| POST | `/api/hardware/release` | Unassign a service, stopping its unit(s) first |
| POST | `/api/hardware/burn-serial` | Burn a unique EEPROM serial onto the sole unclaimed RTL-SDR |
| GET | `/api/hardware/console` | The matrix payload: `services`, `devices`, `warnings` |
| POST | `/api/hardware/route` | Console reroute — put a service on a device, start it, displace the incumbent |
| POST | `/api/hardware/lock` | Lock / unlock a device |
| POST | `/api/hardware/stop-all` | STOP ALL |
| POST | `/api/hardware/service-stop` | Stop one service's unit(s) without changing its assignment |
| GET | `/api/hardware/guardian` | `mode`, `seconds_left`, `reason`, `stats`, `enabled`, `thresholds` |
| POST | `/api/hardware/guardian/cancel` | Operator override — cancel the countdown |
| POST | `/api/hardware/guardian/config` | Enable/disable and tune thresholds (clamped to the floors) |

See [API — Hardware allocation](api.md#hardware-allocation-apihardware) for payload detail.

### Field-debug — "the console says my dongle isn't there"

```bash
curl -s http://localhost:8083/api/hardware/console | python3 -m json.tool | head -40
curl -s http://localhost:8083/api/hardware/guardian
cat configuration/hardware.json
rtl_test -t 2>&1 | head
```

**Healthy:** `console` lists your devices with `"warnings": []`, and each assigned
service shows `running: true` or a state you recognise. `guardian` returns
`"mode": "idle"` with live `stats`. `hardware.json` names the same devices.

**Broken:** a `warnings` entry of kind `device-missing` with severity `crit` —
`"<service> is assigned to a device that isn't present (<device_id>)"`. That is a
*stale assignment*, not a detection bug: the dongle in `hardware.json` is unplugged,
or came back with a different EEPROM serial. Confirm with `rtl_test -t`; if it
reports serial `00000001` on several dongles, use **burn-serial** to give each a
unique one, then reassign. A `guardian` `"mode": "armed"` with a `seconds_left`
countdown means the box is over a threshold and about to stop everything — cancel it
from the banner, then deal with the temperature.

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
| **OASIS handbook** | `static/oasis-handbook/index.html` | The full offline operator handbook — one page per subsystem, plus the radio quick cards and APRS bot guides that used to live in `static/cheatsheets/` |
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
scp /path/to/exported.csv <username>@<hostname>.local:/home/<username>/oasis/static/repeaterbook/repeaterbook.csv
```

> 💡 **Windows users:** in WinSCP or FileZilla, navigate to `/home/<username>/oasis/static/repeaterbook/` on the Pi and drag the file in, renaming it to `repeaterbook.csv`.

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
python3 features/cm4stack/install-cm4stack.py             # auto-detect the setup phase
python3 features/cm4stack/install-cm4stack.py --dry-run    # preview config.txt changes
python3 features/cm4stack/install-cm4stack.py --config-only # only config.txt + headless boot
python3 features/cm4stack/install-cm4stack.py --service-only # only install the panel service
```

It runs in **two phases** (auto-detected) and **requires a reboot** between them:

1. **Panel not live (first run):** patches `config.txt` with the OASIS-managed
   M5Stack overlay block, sets headless boot, installs the Python runtime deps.
   Exits with code `10` (reboot required) — the panel only appears after reboot.
2. **Panel live (after reboot):** builds and installs the GT911 touch-fix overlay,
   then installs and enables `oasis-panel.service`. May exit `10` again if a second
   reboot is needed for the touch fix.

Exit codes: `0` = done · `10` = reboot required · `1` = error. Full hardware
writeup: [`features/cm4stack/cm4stack-oasis-panel.md`](../features/cm4stack/cm4stack-oasis-panel.md).

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
and installs the daemon (`features/rgb-cooling-hat/rgb-cooling-hat.py`) to `/opt` as a
systemd service. The daemon itself needs **no internet** (it has an inlined SSD1306
driver); on a fully offline box, install the three apt deps from your apt cache or
bundled `.deb`s first.

---

## Argon ONE case (fan control)

For an **Argon ONE** case (v2 / v3 / M.2), this installs a small daemon that drives
the case fan from CPU temperature over I²C — **without** the vendor `argononed`
daemon:

```bash
python3 features/argon-fan/install-argon-fan.py            # install + enable the service
python3 features/argon-fan/install-argon-fan.py --user pi  # run the service as 'pi'
python3 features/argon-fan/install-argon-fan.py --check    # report status
python3 features/argon-fan/install-argon-fan.py --disable  # remove the service; unmask argononed
```

It enables I²C, installs the apt deps (`python3-smbus`, `i2c-tools`), confirms the
fan MCU is on the bus (`0x1a`), and installs the daemon (`features/argon-fan/argon-fan.py`)
to `/opt` as `argon-fan.service`. The fan MCU takes a **single byte = speed percent**
(`sudo i2cset -y 1 0x1a 100` = full, `0` = off) — that's the whole protocol. The
daemon needs **no internet**.

> ⚠️ **Why not the vendor daemon? The GPIO4 conflict.** Argon's `argononed` monitors
> **BCM GPIO4** for the case's soft power button. GPIO4 is *also* where the Waveshare
> **L76X GPS HAT** routes its **1PPS** wire (`dtoverlay=pps-gpio,gpiopin=4`). With
> `argononed` running, one PPS pulse per second reads as a stream of power-button
> presses, so the Pi **reboots and then shuts down** the moment the GPS/DRA HAT is
> seated. This installer stops, disables, and **masks** `argononed` to free GPIO4;
> `argon-fan` never touches it. (Masked, not removed — restore the power button with
> `sudo systemctl unmask argononed`, or remove the vendor package entirely with
> `sudo /etc/argon/argon-uninstall.sh`, which leaves the `config.txt` UART/I²C edits.)

> ⚠️ **I²C `0x1a` collision with the DRA-Pi.** The Wolfson **WM8731** codec on the
> MastersComm **DRA-Pi** *also* defaults to `0x1a` — the same address as the Argon
> fan MCU. If both share a bus, fan writes and the codec fight for the address. Strap
> the WM8731 **CSB** pin to `0x1b`, or don't stack both. `--check` warns when the
> WM8731 overlay is present in `config.txt`.

The CPU/temp card on the dashboard shows a fan blade that spins when the fan is
running and the CPU is ≥ ~55 °C (green → amber → red by temperature); it's hidden
when no cooling daemon is installed.

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
events, and sizes the window to match. The page persists the resolution to
`localStorage.oasis_layout` (so ⌂ HOME across the suite routes back to the
dashboard); the layout is fully fluid, scaling with viewport height. (The old
`--7inch` flag still works as an alias for `--resolution 800x480`.)

Plain `--with-browser` (no `--resolution`) opens `index.html` instead — the full
dashboard, not the panel layout.

### ⚠️ The double-kiosk bug — re-run the installer after upgrading

**If your kiosk was installed before this release, it is almost certainly running two
Chromiums right now.**

The installer used to write the XDG autostart entry unconditionally *and* add the
labwc autostart line whenever labwc was present, on the stated assumption that labwc
ignores `~/.config/autostart/*.desktop`. **Pi OS Trixie's labwc honours both.** Every
Wayland kiosk therefore came up **twice** — two independent fullscreen Chromiums
stacked on one screen, on the machine least able to afford it:

- double CPU, GPU and RAM, and every API poll on the box happening twice;
- and, worse, each browser keeps its own page state. **Mute the satellite alerts on
  the window you can see and the one behind it chimes on**, unreachable, with the bell
  on screen showing muted the whole time. Every per-page control looks broken in
  exactly this way.

The installer now writes **one** autostart mechanism, chosen from the running
compositor, and actively **removes** the other. Removing rather than merely
not-writing is what repairs an already-installed station — which is most of them.

**There is no self-repair on `git pull`. Nothing re-runs the installer for you.**
After upgrading, run it again — and it must include `--with-browser` or
`--resolution`, because the cleanup lives in the kiosk install path; a bare re-run
only rewrites `oasis.service`:

```bash
python3 scripts/enable-autostart-pi.py --resolution 1920x1200   # or 800x480, or --with-browser
sudo reboot
```

**Check whether you are affected:**

```bash
pgrep -c chromium                        # count of Chromium processes
ls ~/.config/autostart/                  # XDG autostart entries
grep chromium ~/.config/labwc/autostart  # labwc autostart line
grep oasis-browser-launch ~/.config/labwc/autostart
```

**Healthy** on a labwc (Wayland) box: `~/.config/labwc/autostart` contains exactly one
`/usr/local/bin/oasis-browser-launch &` line, and `~/.config/autostart/` contains **no**
`oasis-browser.desktop`. On an X11/LXDE box it is the mirror image — the `.desktop`
exists, and no OASIS line is in the labwc autostart. Either way `pgrep -c chromium`
settles to the process count of a *single* browser.

**Broken:** `oasis-browser.desktop` **and** an `oasis-browser-launch` line in the labwc
autostart both present. That is the double launch. `pgrep -c chromium` roughly doubles,
and `pgrep -af chromium | grep -c kiosk` returns 2. Re-run the installer as above.

> ℹ️ **wayfire is the exception.** The installer never writes `~/.config/wayfire.ini`.
> If it detects wayfire it installs the XDG entry and prints the line to add by hand:
> `oasis = /usr/local/bin/oasis-browser-launch` under `[autostart]`.

### What is on the panel

The kiosk is not a shrunken `index.html` — it is its own layout:

- **Clocks** — LOCAL and UTC/Zulu, side by side; tap either to cycle its colour.
- **Midcard** — callsign · grid pill (tap to edit), GPS fix pill, a services count
  pill (`N UP · N WRN · N DN`, tap for the list), the Wi-Fi pill, and the
  `APRS · ADS-B · ORX · SAT` dot bar that opens the
  [Service Operations console](#service-operations-console-hardware-assignment).
- **System bar** — CPU · RAM · LOAD · TEMP (with a spinning fan blade when a cooling
  daemon is installed) · DISK · IP, from `/api/system` every 5 s. Tap TEMP for °C/°F.
- **Traffic card** — live APRS/ADS-B stations heard, with `RF` / `IS` / `ADS-B` source
  chips; tap a row for the detail sheet.
- **Satellites card** — see below.
- **Footer** — emergency chip, hazard pills from `/hazards.json`, and SDR flow meters
  for APRS and ADS-B (blue flowing, red silent, dim off).
- **OPS pill** — the launcher overlay for everything else in the suite.

#### The Jenny avatar card (1920×1200 only)

On the wide panel, a small animated avatar card sits to the left of the clocks and
speaks the station's announcements. It has **two gates, both required**:

1. **Resolution** — it is `display:none` on 800×480. On the 7″ panel it would take
   ~15% of the width from three cards that need it more.
2. **A voice is actually installed** — the card stays hidden until
   `GET /api/speech/status` returns `available: true`. No engine, no Jenny, no dead
   square. See [Speech (Piper voice)](#speech-piper-voice).

Tap to hear a greeting; tap again while it is speaking to stop. The clip itself has no
audio — the voice comes from `/api/speech/say`.

#### The hour bell (Zulu chime)

Bottom-right of the **UTC/Zulu** card is a bell with a **three-state** control; each
tap advances it:

| State | Behaviour |
|---|---|
| **off** | nothing |
| **chime** | strikes the hour — two strikes, 330 Hz + 660 Hz, deliberately unlike the satellite "VVV" |
| **voice** | strikes, then speaks the time as two sentences a second apart: *"The time is eighteen hundred Zulu."* … *"Local time is …"* |

Speech-without-strike is deliberately unreachable.

- **It fires on the top of the UTC hour**, not the local hour — the bell belongs to the
  Zulu card and Zulu is what it announces first. In a half-hour zone (UTC+05:30) the
  spoken local time is "eleven thirty", said out loud rather than rounded into a lie.
- **Quiet hours are 22:00–07:00 *local***, not UTC — reading them off UTC would, at
  UTC−7, silence the shack from 05:00 to 14:00 local and chime all night. Arming the
  bell *during* quiet hours sets a one-night override that expires at the next 07:00.
- The state is **per device** (`localStorage`), like the satellite mute. Silencing the
  shack kiosk leaves a laptop chiming.
- An hour is **dropped, not queued**, if a satellite alert owns the speaker at the
  time, and the first tick of a session never announces an hour already missed.

#### The satellites card

Shows only birds **in a pass now, or rising within the next hour**, in-pass first then
by rise time, refreshed every 60 s with a visible `↻ NNs` countdown. In-pass rows are
green and pulse, carry a **live look-angle** (`el NN° ↗ · range`) recomputed locally
every 2 seconds, and show `LOS HH:MM`. Upcoming rows show the rise time, an `↑ Nm`
countdown, the rise compass point and the peak elevation; inside 10 minutes they turn
amber and add *· get ready*. An amber bell marks an armed bird — informational only:
bells are armed on the Satellites page, not here. The header's own bell **mutes this
screen**, per device.

### Field-debug — the kiosk

```bash
pgrep -c chromium                                   # 1 browser's worth, not 2
pgrep -af chromium | head -1                        # the flags it actually launched with
cat /usr/local/bin/oasis-browser-launch             # the generated launcher
systemctl is-active oasis                           # the page has to have a server
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8083/oasis-dashboard/dashboard.html
```

**Healthy:** one Chromium tree; its command line carries `--kiosk`,
`--autoplay-policy=no-user-gesture-required`, `--enable-speech-dispatcher`,
`--use-gl=angle` and `--disable-breakpad`; the launcher's `URL=` matches the panel you
expect; `oasis` is `active`; the page returns `200`.

**Broken, and what each one means:**

- **`pgrep -c chromium` roughly doubles** → the double-kiosk bug above.
- **A flag is missing from the running process** → the launcher is *generated*.
  Adding a flag to `scripts/enable-autostart-pi.py` does not touch a Pi installed
  earlier; nothing re-runs it on upgrade. Re-run the installer and reboot. A missing
  `--autoplay-policy=no-user-gesture-required` is why an untouched kiosk never chimes;
  a missing `--disable-breakpad` shows up as a Chromium crash-loop pinning the CPU;
  `--use-gl=egl` instead of `angle` leaves MapLibre's WebGL context uninitialised.
- **Blank screen, no browser at all** → check the autostart file for the compositor you
  are actually running (`ls ~/.config/autostart/`, `grep chromium
  ~/.config/labwc/autostart`); the installer picks by *running* compositor, so
  installing over SSH before the desktop has ever started can pick the wrong one.
- **Browser up, page blank/erroring** → the launcher polls the URL for up to 60 s
  before launching; if `oasis` came up later than that, the browser is showing an
  error page. `sudo systemctl restart oasis`, then restart the session.

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

### Running the bundle on Linux

On Windows the bundle runs from the embedded Python (`start-server.bat`, `--for-windows` build). On **Linux/macOS** it builds a Python virtualenv from the bundled wheels on first run — `run-portable.sh` (tools-only profile) and `scripts/start-server.sh` both do this. That needs two things from the host, and getting either wrong produces a confusing error:

| Symptom on first run | Cause | Fix |
|---|---|---|
| `…/_runtime/linux/.venv/bin/python: no such file or directory` | `python3` is installed but its **venv module** isn't — Debian/Ubuntu/Raspberry Pi OS split it into a separate package, so `python3 -m venv` fails and leaves a stub `.venv` with no interpreter | `sudo apt install python3-venv` (match your version if apt asks, e.g. `python3.11-venv`). Fedora/Arch already include it. |
| `operation not permitted: …/.venv/lib64` | The bundle is on a **FAT32 / exFAT / NTFS** USB stick, which can't hold the symlinks a virtualenv needs (`lib64 → lib`, `bin/python`) | Copy the bundle onto a **native Linux filesystem** and run it there: `cp -r oasis-offline ~/oasis-offline && cd ~/oasis-offline && ./run-portable.sh` |

Confirm the filesystem type with `df -T .` from inside the bundle (`vfat`/`exfat`/`ntfs`/`fuseblk` = the USB case; `ext4`/`btrfs`/`xfs` = native, fine).

> 🔧 **A failed first run leaves a broken `_runtime/linux/.venv`** that later runs skip over (they only check the directory exists). Delete it before retrying — `rm -rf _runtime/linux/.venv` (prefix `sudo` if the stub is root-owned) — then re-run **as your normal user**, never with `sudo`.

---

## Updating OASIS

To pull a new version of OASIS when a release comes out:

```bash
cd ~/oasis
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

## Diagnostics page (browser)

`server/system/diagnostic.html` is the browser face of the same check registry
`doctor.py` runs — reachable from the dashboard's nav (**OASIS Diagnostic**) and from
the kiosk's OPS overlay under *health*. It is **read-only**: it runs checks and tells
you what is wrong, it never changes anything. That is what makes it different from
`server/system/setup.html`, which installs and configures.

It calls `GET /api/diagnostics` — a thin wrapper around the same `run_all()` the
doctor uses — and renders three things:

- **Capability tiles** across the top: `Core / Access`, `APRS Receive`, `Winlink`,
  `Position / GPS`, `Power / Health`, `Reference Data`. Each rolls up its member
  checks. A tile goes **red only when a *critical* member failed** — nothing usable;
  a non-critical failure or a warning leaves it **amber**, because the capability is
  degraded but still functioning. Tap a tile to expand its checks.
- **One "fix now" pick** — the single highest-impact failure, chosen critical-first
  then by group order (CORE → HARDWARE → SERVICES → SYSTEM → DATA). Deliberately one
  item: a page of twelve red rows tells a tired operator nothing about where to start.
- **Every check**, grouped and sorted worst-first within each group, each with a
  badge, a detail line, what it `breaks`, and where to go and fix it.

**The cached-stale render.** On load the page immediately draws the **last sweep it
ran on this device**, dimmed, with a banner reading *"Showing last run &lt;timestamp&gt;"*
— then you press **[ Run diagnostics ]** for a fresh one. The sweep makes localhost
self-HTTP calls (`/api/system`, `/api/aprs/stations`, …) and is not instant on a Pi 3,
so opening the page to blank boxes for several seconds would be worse than opening it
to what was true an hour ago, clearly labelled. The cache is `localStorage` under
`oasis-diag-last`, per browser — a stale render on the kiosk says nothing about the
laptop.

> ℹ️ **`/api/diagnostics` needs a threaded worker.** `run_all()` calls back into the
> same Flask process that is serving the request, so the launchers start gunicorn with
> `--threads 4`. A single-threaded worker deadlocks on this route.

---

## Health check (doctor)

`scripts/doctor.py` is the headless form of the same station-health registry the two
browser pages use — `server/system/setup.html` (which also installs) and
`server/system/diagnostic.html` (read-only, above). Use it for post-deploy
confirmation over SSH when no browser is available, or as a CI / automated gate.

```bash
python3 scripts/doctor.py                          # run every check
python3 scripts/doctor.py --core                   # DISPLAY only the CORE group
python3 scripts/doctor.py --json                   # machine-readable JSON output
python3 scripts/doctor.py --host 192.168.1.10      # check a remote OASIS instance
python3 scripts/doctor.py --host HOST --port PORT  # non-default host and port
```

Those four flags are all of them.

### What it checks

**22 checks in five groups**, each rolled up into one of six **capabilities**:

| Group | Checks (**bold** = `critical`) |
|---|---|
| **CORE** | **server** · **station_identity** · webssh |
| **HARDWARE** | **rtl_sdr** · **gps** · digirig · dra_pi |
| **SERVICES** | **graywolf** · **pat** · **aprs_feed** · graywolf_api |
| **SYSTEM** | **power** · disk · temp · cpu · cooling_hat |
| **DATA** | fcc · maps · kiwix · repeaterbook · forms · winlink_forms |

Those eight bold checks are the comms-essential set, and they are the *only* thing
that moves the exit code.

| Capability | Rolls up |
|---|---|
| **Core / Access** (`ACCESS`) | `server` · `station_identity` · `webssh` |
| **APRS Receive** (`APRS_RX`) | `rtl_sdr` · `digirig` · `dra_pi` · `graywolf` · `graywolf_api` · `aprs_feed` |
| **Winlink** (`WINLINK`) | `pat` |
| **Position / GPS** (`POSITION`) | `gps` |
| **Power / Health** (`POWER`) | `power` · `temp` · `cpu` · `disk` · `cooling_hat` |
| **Reference Data** (`REFERENCE`) | `fcc` · `repeaterbook` · `kiwix` · `forms` · `maps` (+ `winlink_forms`) |

A capability is **fail** only if a *critical* member failed; any other failure or
warning leaves it **warn**. The sweep also names one **fix now** item — the single
highest-impact failure, critical first, then earliest group.

> ℹ️ `winlink_forms` is a backlog-tier check: `doctor.py` always includes it, but
> `GET /api/diagnostics` (and any other `run_all()` caller using the default) runs the
> other 21. It is non-critical either way.

### `--core` and the exit code are unrelated

**`--core` is a display filter, nothing more.** It narrows what is *printed* to the
CORE group — `server`, `webssh`, `station_identity`. It does not narrow what runs, and
it does **not** change the exit code. In `--json --core` only `groups` is filtered:
`summary`, `capabilities`, and `fix_now` still describe the full sweep, so you can
legitimately see a `fix_now` naming a check that isn't in the emitted `groups`.

**Exit codes:** `0` = no check marked `critical` failed · `1` = at least one did.
Warnings never affect it. Non-critical failures never affect it. `--core` never
affects it. Note this keys off the registry's `critical` flag, *not* off the CORE
group — a failing `power` (SYSTEM) exits 1; a failing `fcc` (DATA) does not.

The text run closes with either `All critical checks passed.` or
`Critical check(s) FAILED: <ids>.`

### Typical usage

```bash
python3 scripts/doctor.py
# "All critical checks passed."        → exit 0
# "Critical check(s) FAILED: gps, pat" → exit 1 (the ✗ lines carry the reason)
```

For scripted / CI use, **read the process exit code** — it already is the contract:

```bash
if python3 scripts/doctor.py --json > /tmp/doctor.json; then
    echo "station healthy"
else
    python3 -c "import json;d=json.load(open('/tmp/doctor.json'));print(d['summary'])"
    exit 1
fi
```

`summary` is `{"fail": N, "warn": N, "ok": N}` over the whole sweep; the full payload
is `{ran_at, summary, capabilities, fix_now, groups}`. There is no `core_ok` key —
don't reach for one.

### Keep logs across reboots (worth doing before you need them)

Pi OS ships journald with `Storage=auto`, which keeps the journal in RAM unless
`/var/log/journal` exists. On a station that reboots — or that gets rebooted *by*
the fault you are trying to explain — that means the evidence dies with the boot:

```bash
journalctl --list-boots        # only one entry, however many times it has rebooted
```

An OASIS box is exactly the case where you want the previous boot: a crash, a
brownout, an out-of-memory kill, or a HAT that stopped enumerating all look
identical after the reboot that hid them. Make it persistent, with a cap so it
cannot eat the SD card:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/oasis-persistent.conf >/dev/null <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=64M
SystemMaxFileSize=8M
MaxRetentionSec=2week
EOF
sudo systemctl restart systemd-journald
```

**Healthy:** `journalctl --list-boots` gains an entry per reboot, and
`journalctl -b -1 -p err` reads the *previous* boot's errors.

**The trade-off, honestly:** this writes the journal to the SD card. The 64 MB cap
and 8 MB file size keep the write volume small — far below what the ADS-B recorder
already does — but on a card you care about, or a read-only-root deployment, leave
it off and accept that post-mortems stop at the reboot.

**One gotcha:** a Pi with no RTC sets its clock late in boot, so the first entries
of each boot are stamped with the *last known* time and can appear weeks in the
past. Sort by boot (`-b -1`), not by timestamp. See
[Hardware RTC](#hardware-rtc-witty-pi-3--bigtreetech-7) if the timestamps matter.

---

## Factory reset / uninstall

To undo everything the setup scripts installed — stop, disable, and remove all
OASIS services, delete OASIS-managed system files, and strip the OASIS blocks from
`config.txt` (DRA-Pi, CM4Stack, and the RTC overlays):

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

- **One RTL-SDR consumer at a time.** The APRS SDR feed, ADS-B, OpenWebRX, and
  satellite listening each need the dongle exclusively, so only one runs at once.
  The [Service Operations console](#service-operations-console-hardware-assignment)
  makes the swap one tap, but it is still a swap — you cannot gate APRS and watch
  aircraft on the same dongle.

- **OpenWebRX has no UI for assigning a dongle.** Every other SDR consumer is a column
  in the assignment matrix; OpenWebRX is not, because OASIS cannot tell it which
  device to use. Until an RTL-SDR is assigned to `openwebrx` its START button stays
  inert, and the assignment has to be made by hand with `curl` against
  `/api/hardware/assign` — see [OpenWebRX (SIGINT)](#openwebrx-sigint).

- **OpenWebRX is not in the offline bundle.** It installs from the third-party
  OpenWebRX+ (`luarvique`) apt repo, so it needs **internet once** and cannot be
  installed in the field. Install it at home, before deployment.

- **ADS-B may need internet on first install.** `dump1090-fa` ships from FlightAware's
  own apt repo, not base Debian. Install prefers a vendored `.deb` under
  `services/adsb/packages/dump1090-fa/<suite>/`, but the build step that fetches it
  into the offline bundle is still a pending follow-up — so today's offline image
  installs ADS-B via the **online fallback**.

- **Speech (Piper) is not available everywhere.** It needs **Python 3.11+**
  (onnxruntime publishes no older wheels) and is skipped cleanly on **32-bit ARM**
  (`armv7l`/`armv6l`, no onnxruntime wheel). On those boxes announcements fall back
  to the espeak-ng voice — degraded, not broken.

- **RTL-SDR APRS receive requires Raspberry Pi OS Trixie**, for `librtlsdr ≥ 2.0`.
  On Bookworm the dongle path is unavailable; use a DigiRig, DRA-Pi, or DRAWS instead.

- **Satellite recordings have no delete button.** Retention is automatic — a 2 GB
  budget swept oldest-first, with the newest capture never pruned — but there is no UI
  to remove a specific WAV. Clear space by hand: `rm configuration/sat-recordings/*.wav`.

- **Winlink over RF is experimental.** The DigiRig/DRAWS RF paths work on the bench but
  are not yet field-proven across radios. Telnet/internet Winlink is the reliable path;
  treat RF as something to test before you depend on it.

- **`/api/system` returns 503 on Windows.** The system-metrics endpoint is Linux/macOS
  shaped; on the Windows portable bundle it reports
  `SYSTEM_METRICS_UNAVAILABLE` and the station-chrome that depends on it is hidden.
  Everything else in the bundle works.

### Automatic data updates

OASIS keeps its perishable datasets current whenever the internet happens to be
reachable, and fails quietly when it is not — so a station can live on a bench,
go to a summit, and come home without anyone thinking about it.

**What refreshes**

| Dataset | Stale after | Size | How |
|---|---|---|---|
| Satellite TLEs (CelesTrak) | 3 days | KB | automatic |
| SatNOGS transmitters | 30 days | KB | automatic |
| FCC callsign database | 14 days | ~160 MB | automatic on an unmetered link, otherwise one tap |
| RepeaterBook directory | 180 days | MB | **not downloaded** - you export the CSV; OASIS reports its age |

**How it decides.** A background pass runs every 30 minutes. There is no
"am I online" probe — the fetch attempt *is* the probe, and a DNS failure costs
milliseconds, so an offline pass is a handful of instant failures and the
datasets stay exactly as they were. Large datasets only download automatically
when NetworkManager reports the link as definitely **unmetered**; unknown counts
as metered, because unknown is what most phone hotspots report. On Windows and
macOS there is no `nmcli` at all, so large downloads are always one tap.

The refresher is the lowest-priority thing on the box: if the resource guardian
is near a threshold, large downloads are skipped and retried next pass. The
updater must never be the thing that trips a STOP ALL.

**Where to see it.** The **Data Updates** section on the Diagnostics page shows
every dataset with what was updated and when, what is missing, and the action
required. A pill in the dashboard header and a chip on the touch kiosk go amber
when anything is stale.

**RepeaterBook: reported, not downloaded**

OASIS does **not** fetch the repeater directory. Export a CSV for your region
from repeaterbook.com or from CHIRP, and drop it in `static/repeaterbook/`.

That is a deliberate choice, not a missing feature. RepeaterBook's API uses a
centralised token model - their partner CHIRP holds one credential and queries
on behalf of every user - and OASIS is the wrong place for such a credential.
Our refresh loop is autonomous rather than user-pressed, and an offline fleet
cannot be patched: if a bug caused a retry storm on deployed stations, the only
remediation would be carrying a USB stick to each one. Being accountable for
traffic we cannot stop is not a trade worth making for an emergency tool.

What OASIS does instead is tell you **how old your copy is**, so you can refresh
it before a deployment rather than discovering it is stale in the field. The
Data Updates section reads "Your copy is 56 days old" and, past the threshold,
tells you to export a new one.

**Field debug**

```bash
python3 scripts/refresh-data.py --list          # what is stale; no network
python3 scripts/refresh-data.py --dry-run       # same, plus the metered verdict
python3 scripts/refresh-data.py --source tle    # refresh one source now
python3 scripts/refresh-data.py --force         # ignore freshness and back-off
python3 scripts/refresh-data.py --json          # machine-readable
```

*Healthy:* every row reads `OK` with an age below its threshold.
*Broken:* a row reads `NONE` (never fetched) or shows an error.
`OFF` means no token is set — that is a switched-off source, not a fault.
`TAP` means a large download is waiting because the link looks metered.
