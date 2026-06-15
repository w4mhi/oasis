# OASIS — Off-grid Amateur Station Information Suite

> A fully offline amateur radio EmComm toolkit for Raspberry Pi. No internet required at runtime. Any browser on the local network just works.

Designed by **KM4ACK** and extended here by **W4MHI** [OASIS] runs on a Raspberry Pi Zero 2 W and serves a complete browser-based dashboard to every device on your local network or hotspot. No app to install, no cloud account, no internet needed in the field.

---

## Features

**Emergency communications forms**
- ICS 205 Radio Communications Plan — with CHIRP frequency import
- ICS 213 General Message
- ICS 214 Activity Log
- ICS 309 Communications Log
- All forms: PDF export (fills official FEMA templates), CSV import/export, auto-save

**FCC callsign lookup**
- Offline binary-search over a local copy of the FCC amateur database
- Returns name, city, state, Maidenhead grid, lat/lon — sub-millisecond, no DB engine

**Offline maps**
- Vector tiles from OpenStreetMap via MapLibre GL + PMTiles
- No tiles fetched from internet at runtime — everything served from the Pi
- Multi-region support with switchable layers

**Tools & calculators** (all browser-only, no server)
- Antenna Calculator — dipole, vertical, J-pole, EFHW and more
- Grid Square / Distance / Bearing calculator
- Power & Battery budget planner
- Gray Line propagation tool
- Solar / band conditions display
- Net check-in logger with map view and CSV export

**Winlink**
- Frequency/mode reference for common radios
- Position report viewer
- Nearby-mobile-users → YAAC `.pos` converter

**Reference library** (no setup required)
- U.S. amateur band plan
- Quick reference: Q-codes, NATO phonetics, pro-words, RST, ITU prefixes
- Per-radio cheatsheets, operation cards, and PDF manuals
- Repeater programming guides (CHIRP)
- GrayWolf offline handbook

**Companion services** (optional, Pi only)
- [GrayWolf](https://github.com/chrissnell/graywolf) APRS TNC/iGate/digipeater — port 8080
- [Kiwix](https://kiwix.org/) offline Wikipedia — port 8081

**USB / portable bundle**
- `scripts/build-usb.py` packages everything into a self-contained folder
- Windows: embedded Python, double-click `start.bat` — nothing to install
- Linux: bootstraps a venv on first run via `start.sh`
- Can be built from macOS, Linux, or Windows

---

## Hardware

| Component | Spec |
|---|---|
| Board | Raspberry Pi Zero 2 W (also runs on Pi 3/4/5, Linux, macOS) |
| RAM | 512 MB |
| Storage | 32 GB SD card minimum |
| OS | Raspberry Pi OS Lite 64-bit |
| Network | Local hotspot or LAN — no internet |

---

## Quick start

```bash
git clone https://github.com/<your-handle>/oasis-emcomm
cd oasis-emcomm

# Install server dependencies (all offline-vendored except psutil)
python3 scripts/setup-server.py

# Download FCC callsign data — one-time, needs internet (~160 MB)
python3 scripts/setup-fcc-database.py --full-zip

# Start the server
source .venv/bin/activate && python3 server/app.py
```

Open `http://<pi-ip>:8083` on any device on the same network.

---

## Port map

| Port | Service |
|---|---|
| 8083 | OASIS — dashboard, FCC lookup API, map tile server |
| 8080 | GrayWolf APRS client (optional, separate install) |
| 8081 | Kiwix offline Wikipedia (optional, separate install) |

---

## Documentation

Full setup guides: **[docs/SETUP.md](docs/SETUP.md)**

- Server setup & systemd auto-start
- FCC callsign database pipeline
- Offline maps — building PMTiles from OSM data
- GrayWolf APRS install
- Kiwix / Wikipedia download
- ICS forms — PDF template management
- USB portable bundle
- Keeping data fresh

---

## Design principles

- **Fully offline.** No asset, font, tile, or API call may use the internet at runtime.
- **Minimal backend.** Flask handles only data-heavy tasks. No new services without strong justification.
- **No CDN links.** All JS libraries load from local files served by the Pi.
- **localStorage only.** No server-side writes from the browser.
- **Zero client install.** Any browser on the LAN works with no setup.

---

## Credits

**OASIS** is designed and maintained by **W4MHI** (Pacific Northwest), extending the original concept for field deployment in Washington state.

OASIS grew out of the **ACK Off-Grid Ham Radio Server** project by **Jason, KM4ACK** (Tennessee). The original concept — a fully offline, browser-accessible amateur radio toolkit on a Raspberry Pi — is his. Jason also wrote the initial architectural guidelines this project builds on.

[KM4ACK on GitHub](https://github.com/km4ack)
