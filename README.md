<div align="center">

# 🛜 OASIS

### Off-grid Amateur Station Information Suite

**A complete amateur-radio EmComm toolkit that runs with zero internet — on a Raspberry Pi, your laptop, or a USB stick.**

Forms · FCC lookup · offline maps · APRS · calculators · reference library — served to any browser on your local network.

![Offline](https://img.shields.io/badge/internet-not%20required-2ea44f)
![Platforms](https://img.shields.io/badge/runs%20on-Raspberry%20Pi%20%7C%20macOS%20%7C%20Windows%20%7C%20Linux-blue)
![Python](https://img.shields.io/badge/python-3.9%E2%80%933.14-3776ab)
![Backend](https://img.shields.io/badge/backend-Flask-000000)
![Client](https://img.shields.io/badge/client-zero%20install-orange)

</div>

---

## ⚡ At a glance

| | |
|---|---|
| **What it is** | A browser dashboard for emergency communications, served from a tiny local web server. |
| **Who it's for** | Hams running nets, ARES/RACES ops, field deployments, and grid-down preparedness. |
| **The promise** | No internet at runtime. No cloud account. No app to install on client devices. |
| **Runs on** | Raspberry Pi Zero 2 W (primary) · also Pi 3/4/5, macOS, Windows, Linux. |
| **Access it** | Any phone/tablet/laptop on the same Wi-Fi or hotspot → `http://<host-ip>:8083`. |
| **Install size** | ~2.3 MB of bundled Python wheels — the whole server installs offline. |

---

## 🚀 Quick start

```bash
# 1 — Get the code
git clone https://github.com/W4MHI/oasis-emcomm
cd oasis-emcomm

# 2 — Install the server (offline — all dependencies are vendored)
python3 scripts/setup-server.py

# 3 — Download FCC callsign data (one-time, needs internet — ~160 MB)
python3 scripts/setup-fcc-database.py --full-zip

# 4 — Launch
./start.sh                 # Linux / macOS   (Windows: double-click start.bat)
```

Then open **`http://localhost:8083`** — or `http://<host-ip>:8083` from any other device on the network.

> 💡 **Truly offline install.** Step 2 needs no internet — Flask, gunicorn, and psutil ship as pre-built wheels for every supported platform and Python 3.9–3.14. Only the one-time FCC data download (step 3) goes online, and you can do it on any machine and copy the result to the Pi.

---

## 💾 Portable USB bundle

```bash
python3 scripts/build-usb.py --out /Volumes/USB/oasis
```

Produces a self-contained folder that runs with no system Python on Windows (bundled embedded Python) and bootstraps offline from the vendored wheels on Linux/macOS. The build only reaches the internet to fetch the Windows Python runtime; use `--skip-windows` for a 100% offline Linux/macOS build.

---

## ✨ Features

#### 📋 Emergency communications forms
ICS **205 · 213 · 214 · 309** — fill official FEMA PDF templates, import/export CSV, auto-save, and import frequencies straight from CHIRP.

#### 🔎 FCC callsign lookup
Offline binary search over a local copy of the FCC amateur database — name, city, state, Maidenhead grid, lat/lon in sub-millisecond time, with no database engine.

#### 🗺️ Offline maps
OpenStreetMap vector tiles via **MapLibre GL + PMTiles**, served from the host with HTTP range streaming. Multi-region, switchable layers, not a single tile fetched from the internet.

#### 📡 APRS (Raspberry Pi)
**GrayWolf** TNC / iGate / digipeater plus a live station map, packet logs, and tactical messaging. See [the APRS section](#-aprs-on-the-raspberry-pi) below.

#### 🧮 Tools & calculators *(browser-only)*
Antenna calculator · grid/distance/bearing · power & battery budget · gray-line propagation · solar & band conditions · net check-in logger with map and CSV export.

#### 📚 Reference library *(no setup)*
U.S. band plan · Q-codes, NATO phonetics, pro-words, RST, ITU prefixes · per-radio cheatsheets & PDF manuals · CHIRP programming guides · GrayWolf handbook.

#### 💾 Portable USB bundle
`scripts/build-usb.py` packages everything into a self-contained folder — Windows runs from a bundled Python (double-click `start.bat`), Linux/macOS bootstrap from the vendored wheels. [Details](#-portable-usb-bundle).

---

## 📡 APRS on the Raspberry Pi

APRS is the flagship Pi capability. OASIS installs and supervises **[GrayWolf](https://github.com/chrissnell/graywolf)** — a browser-based APRS TNC, iGate, and digipeater — and adds a companion API so the dashboard shows live stations on the offline map.

```bash
python3 scripts/install-graywolf.py     # Debian/Ubuntu/Raspberry Pi OS, picks the right .deb for your arch
```

![GrayWolf APRS live map](docs/images/file18.png)

GrayWolf runs on port **8080**; OASIS reads its history database and serves station positions to the dashboard map. Full configuration walkthrough (audio, PTT, beacons, SmartBeaconing, digipeater rules, iGate) is in **[docs/SETUP.md](docs/SETUP.md)**.

---

## 💻 Platform support

Every cell below is verified to install **fully offline** from the bundled wheels (`python3 scripts/vendor-wheels.py --check`):

| Platform | Python 3.9 | 3.10 | 3.11 | 3.12 | 3.13 | 3.14 |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| **Raspberry Pi (64-bit / aarch64)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Linux x86-64 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| macOS (Apple Silicon) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| macOS (Intel) | ✅ | ✅ | ✅ | — | — | — |
| Windows (amd64) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Raspberry Pi OS **Bookworm** (Python 3.11) and **Bullseye** (3.9) both work out of the box. 32-bit Pi OS (armv7l/armhf) is not yet covered offline.

---

## 🧩 How it works

A single small **Flask** app serves the dashboard, the FCC lookup API, and the map tiles. Everything heavy (forms, calculators, reference) is static HTML that runs in the browser using `localStorage` — the server never writes browser state. Companion services (GrayWolf, Kiwix) run as their own processes; the dashboard discovers them at runtime, so nothing hardcodes a port.

| Port | Service |
|:--:|---|
| **8083** | OASIS — dashboard, FCC lookup, map tile server |
| 8085 | APRS history API (feeds the dashboard map) |
| 8080 | GrayWolf APRS *(optional, Pi)* |
| 8081 | Kiwix offline Wikipedia *(optional, Pi)* |

**Design principles:** fully offline at runtime · no CDN links (all JS/CSS/fonts local) · minimal backend · `localStorage` only · zero client install.

---

## 🔐 Network & security

OASIS is built for a **trusted off-grid LAN or hotspot**. By design it binds to `0.0.0.0` (so other devices can reach it) and has **no authentication** — anyone on the same network can use it. That's the right model for a field net, but:

> ⚠️ **Do not expose OASIS directly to the public internet or an untrusted network.** Keep it on your own hotspot/LAN, or put it behind a firewall/VPN if remote access is needed.

---

## 🛠️ Hardware (reference build)

| Component | Spec |
|---|---|
| Board | Raspberry Pi Zero 2 W (also Pi 3/4/5, any Linux/macOS/Windows host) |
| RAM | 512 MB |
| Storage | 32 GB SD card minimum |
| OS | Raspberry Pi OS Lite 64-bit |
| Network | Local hotspot or LAN — no internet |

---

## 📖 Documentation

Full setup, data pipelines, and deployment guides live in **[docs/SETUP.md](docs/SETUP.md)**:
server + systemd auto-start · FCC database pipeline · building map PMTiles from OSM · GrayWolf APRS · Kiwix/Wikipedia · ICS PDF templates · USB bundle · keeping data fresh.

Maintainers: see **[ASSESSMENT.md](ASSESSMENT.md)** for the offline-install architecture and the wheel-vendoring workflow (`scripts/vendor-wheels.py`, CI matrix).

---

## 🙏 Credits

**OASIS** is designed and maintained by **W4MHI** (Pacific Northwest) for field deployment in Washington state.

It grew out of the **ACK Off-Grid Ham Radio Server** by **Jason, KM4ACK** (Tennessee) — the original concept of a fully offline, browser-accessible amateur-radio toolkit on a Raspberry Pi is his. [KM4ACK on GitHub](https://github.com/km4ack) · APRS by **[GrayWolf](https://github.com/chrissnell/graywolf)** (Chris Snell) · offline Wikipedia by **[Kiwix](https://kiwix.org/)**.

<div align="center">

**73 — built for when the grid goes dark.**

</div>
