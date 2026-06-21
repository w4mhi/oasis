<div align="center">

# OASIS — Concept

### Off-grid Amateur Station Information Suite

> *Comms when the network's gone dark.*

</div>

---

## The one-line idea

**OASIS turns a Raspberry Pi (or any laptop) into a self-contained emergency-communications station that every phone, tablet, and laptop on a local network can use through a web browser — with no internet, no cloud account, and nothing to install on the client.**

When the grid is up, it's a convenient ham-radio toolkit. When the grid is down — the moment it was built for — it keeps working exactly the same.

---

## The problem

Modern amateur-radio operating quietly depends on the internet. Callsign lookups hit QRZ. Maps stream from a tile server. Repeater directories, propagation forecasts, grid-square calculators, and ICS form templates all live in the cloud. Net control runs off a Google Sheet.

In the exact scenario amateur radio exists to serve — a disaster, a grid-down event, a remote deployment with no cell coverage — **all of that disappears at once.** The operator is left with a radio and a paper logbook, having lost every digital tool the moment the network did.

EmComm tooling that needs the network has it backwards.

---

## The vision

A station-in-a-box that is **offline-first, not offline-capable.** The difference matters:

- *Offline-capable* means it works online and degrades when the network drops.
- **Offline-first** means it assumes there is no network, ships every byte it needs locally, and treats internet access as a rare convenience for refreshing data — never a runtime dependency.

OASIS holds the second position without compromise. At runtime nothing is fetched: no CDN, no API, no font, no map tile. The entire suite installs from vendored packages, so even *setting it up* can happen with no internet. The only steps that ever touch the network are one-time data downloads (the FCC database, map tiles, Wikipedia) — and those can be done on any machine and copied to the Pi.

The result is an **island of working communications in a desert of failed infrastructure** — which is exactly what the name says.

---

## Who it's for

- **ARES / RACES / SKYWARN operators** running or supporting a served-agency net.
- **Net control stations** who need check-in logging, frequency plans, and ICS forms in one place.
- **Field and POTA/SOTA deployments** with no cell coverage.
- **Grid-down preppers** who want their reference library, maps, and lookup tools to survive the day the internet doesn't.

It assumes the reader holds an amateur license and knows the hobby — it's a tool for operators, not an introduction to radio.

---

## Design principles

1. **Offline at runtime, always.** No CDN links; all JS, CSS, and fonts are local. The server fetches nothing to serve a page.
2. **Zero client install.** The operator's existing browser is the only client. Any device on the Wi-Fi or hotspot reaches it at `http://<host-ip>:8083`.
3. **Truly offline install.** Server dependencies (Flask, gunicorn, psutil) ship as pre-built wheels for every supported platform and Python 3.9–3.14, so the server installs with no internet.
4. **Minimal backend, browser-heavy.** A single small Flask app serves the dashboard, FCC lookup, and map tiles. Everything else — forms, calculators, reference — is static HTML running in the browser against `localStorage`. The server never holds browser state.
5. **Services discovered, not hardcoded.** Companion services (GrayWolf APRS, Kiwix) run as their own processes; the dashboard finds them at runtime, so a missing service just greys out a card instead of breaking the page.
6. **Version-aware, idempotent setup.** Every installer compares versions, upgrades when newer, and never downgrades — so re-running setup or installing from an older USB bundle can't clobber a newer package.
7. **Runs small.** The reference build is a Raspberry Pi Zero 2 W with 512 MB RAM. If it runs there, it runs anywhere.

---

## What it does

| Capability | What you get offline |
|---|---|
| **Emergency forms** | ICS **205 / 213 / 214 / 309** — fill official FEMA PDF templates, import/export CSV, auto-save, import frequencies from CHIRP. |
| **FCC callsign lookup** | Sub-millisecond binary search over a local copy of the FCC amateur database — name, city, state, grid, lat/lon — with no database engine. |
| **Offline maps** | OpenStreetMap vector tiles via MapLibre GL + PMTiles, served from the host with HTTP range streaming. Multi-region, switchable layers, zero tiles from the internet. |
| **Repeater Book** | Load a local CHIRP-format CSV exported from RepeaterBook, browse with instant search/filters, and export the visible set as a ready-to-import frequency plan. |
| **APRS** *(Pi)* | GrayWolf TNC / iGate / digipeater with a live station map, packet logs, and tactical messaging — fed by an RTL-SDR dongle or DRA-Pi sound card. |
| **Winlink** *(Pi)* | Pat Winlink client + web UI for store-and-forward email over radio (Telnet works immediately). |
| **Tools & calculators** | Antenna, grid/distance/bearing, power & battery budget, gray-line propagation, band conditions, and a net check-in logger with map and CSV export. |
| **Reference library** | U.S. band plan, Q-codes, phonetics, pro-words, RST, ITU prefixes, per-radio cheat-sheets, CHIRP guides, GrayWolf handbook — plus your own PDF manuals. |
| **Offline Wikipedia** *(Pi)* | Kiwix serving a ZIM snapshot — an encyclopedia in the field. |
| **Web SSH** *(Pi)* | A browser terminal (ttyd) for headless administration with no separate SSH client. |

All of it served from one host, reachable from any browser on the local network.

---

## How a deployment comes together

1. **Clone the repo** — a fresh clone is just code; the large data is downloaded or generated per deployment, so the repo stays small.
2. **Run the guided installer** (`setup-oasis-offline.py` on a Pi) — tick the features you want and it runs the right install/enable scripts in order, pulling in prerequisites and priming `sudo` once.
3. **Add the data you need** — FCC database, map tiles, repeater CSV, Wikipedia. One-time, online, copy-able.
4. **Start it** — manually with `./start.sh`, or enable the systemd service so it comes up on boot (optionally with a Chromium kiosk).
5. **Connect** — every operator opens `http://<host-ip>:8083` from their own device. No app, no account, no internet.

For a portable deployment, `create-oasis-offline.py` packages the whole suite — code, wheels, GrayWolf, Kiwix, RTL-SDR, FCC data — into a self-contained USB folder that bootstraps with no system Python on Windows and from vendored wheels on Linux/macOS.

---

## Non-goals

- **Not a public-internet service.** OASIS binds to `0.0.0.0` with no authentication by design — the right model for a trusted off-grid LAN or hotspot, the wrong one for the open internet. Keep it behind your own network or a firewall/VPN.
- **Not a logging suite or contest tool.** It's built for emergency and field operations, not chasing DX.
- **Not a beginner's radio tutorial.** It assumes a licensed operator who already knows the craft.

---

## Heritage

OASIS grew out of the **ACK Off-Grid Ham Radio Server** by **Jason, KM4ACK** — the original concept of a fully offline, browser-accessible amateur-radio toolkit on a Raspberry Pi is his. OASIS extends that idea by **Mihai,W4MHI** with a vendored-offline install path, vector offline maps, APRS via GrayWolf, a repeater viewer, and a portable USB bundle.

APRS by [GrayWolf](https://github.com/chrissnell/graywolf) (Chris Snell) · offline Wikipedia by [Kiwix](https://kiwix.org/).

<div align="center">

**73 — comms when the network's gone dark.**

</div>
