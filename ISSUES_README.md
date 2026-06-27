# OASIS Documentation — User-Experience Issues

This file tracks open usability issues in `README.md` and `docs/SETUP.md`, found via a
simulated first-time-user review. Issues are listed in priority order.

**Last updated:** 2026-06-26  
**Reviewer context:** Technician/General-class ham, comfortable with basic Pi terminal
commands, no Linux/Python/systemd background.

---

## 🔴 Blockers

Issues that will stop a real first-time user from completing setup.

| ID | Location | Issue | Recommended fix |
|----|----------|-------|----------------|
| **A** | SETUP.md — Guided setup + Server setup | `setup-oasis-offline.py` vs `setup-server.py` — the relationship is never stated. Both sections read as standalone procedures. A user will run both and wonder if that causes a problem. | Add one sentence at the top of **Server setup**: *"If you used the guided menu above, skip this section — the menu already runs `setup-server.py` for you."* |
| **E** | SETUP.md — FCC section | `scp -r fcc-offline-database/data pi@oasis.local:...` hardcodes `pi@` even though the "Before you begin" section told users their username doesn't have to be `pi`. Authentication fails silently. | Change to `scp -r fcc-offline-database/data <username>@oasis.local:...` and add a note that `<username>` appears in both the connection string and the path. |
| **C1** | README vs SETUP.md | README says *"The server is pre-checked"* (in the guided menu). SETUP.md says *"tick Server and FCC Database."* One implies it's already ticked; the other implies it must be ticked manually. | Decide which is correct, align the two documents. |
| **M1** | SETUP.md — Guided setup | The guided menu section describes *navigation* (arrow keys, Space, Tab) but never describes *outcome*. What happens after you press OK? Does it print a log? Ask for sudo? Reboot? How do you know it's done? | Add: *"When you press OK, the installer runs each selected script in sequence and prints progress. It will ask for your sudo password once. The whole process takes 5–30 minutes depending on what you selected. When it finishes you'll see 'Setup complete.' — then run `./start.sh` to verify."* |

---

## 🟡 Confusing

Issues that are frustrating but don't completely stop progress.

| ID | Location | Issue | Recommended fix |
|----|----------|-------|----------------|
| **B** | SETUP.md | "Tab to OK" — no explanation of what Tab does or what comes next in the menu UI. | Add a one-line description: *"Tab moves cursor focus to the OK button at the bottom — press Enter on OK to start the install."* |
| **C** | SETUP.md — manual service file | `ExecStart=.../gunicorn ...` — the `...` looks like an editorial mistake. A user following the manual path has no idea what arguments to supply. | Either show the complete `ExecStart` line, or note: *"See the generated service file at `/etc/systemd/system/oasis.service` for the exact command — `enable-autostart-pi.py` writes the correct arguments."* |
| **D** | SETUP.md — GrayWolf step 1 | "Station Callsign — enter your callsign with SSID" — the word SSID was just used for a Wi-Fi network name in step 1. A first-timer may type their Wi-Fi password here. | Change to: *"enter your callsign with APRS station ID suffix (e.g. `W4MHI-5`, where `-5` is a number 0–15 that identifies this particular APRS station — it is not related to Wi-Fi)."* |
| **F** | SETUP.md — FCC section | The `scp` command appears under a note saying "run this on any machine with internet," but gives no guidance on *which directory* to run it from or that the source path is relative to the cloned repo. | Prefix with: *"From a terminal on your laptop, in the `oasis-emcomm` folder:"* |
| **G** | SETUP.md — field hotspot | `http://10.42.0.1:8083` — this IP address appears without explanation. Is it always that address? | Add: *"`10.42.0.1` is the default gateway IP assigned by `nmcli`'s hotspot mode. Verify it by running `hostname -I` on the Pi after starting the hotspot."* |
| **H** | SETUP.md — field hotspot | `nmcli` command given with no confirmation it's installed. Raspberry Pi OS Lite without NetworkManager gives `Error: NetworkManager is not running`. | Add: *"Verify NetworkManager is active: `systemctl is-active NetworkManager`. If inactive, install it: `sudo apt install -y network-manager`."* |
| **I** | SETUP.md — RTL-SDR | *"V3 dongles may work on Bookworm but are not officially supported"* — gives no actionable guidance for a V3 user. | Change to: *"V3 dongles may work on Bookworm, but the OASIS project can't guarantee support. If you have a V3 and Bookworm, try `install-rtl-sdr.py` — if it fails, upgrade to Trixie."* |
| **M2** | SETUP.md (top) | No "what does a working OASIS look like?" orientation. The doc dives straight into flashing SD cards without telling the reader what they're building toward. | Add a one-paragraph intro: *"When OASIS is running, open a browser on any device on the same Wi-Fi and go to `http://oasis.local:8083`. You'll see a dashboard with system stats, service status cards, and links to the FCC lookup, maps, and forms. No internet required."* |
| **M3** | SETUP.md — GrayWolf audio | "Select your sound card for RX and TX" — no guidance on identifying the correct device from a list that may include HDMI audio, built-in audio, and USB adapters. | Add: *"A CM108-based USB cable (AIOC, Digirig, etc.) usually shows as `USB Audio Device`. The built-in 3.5 mm jack shows as `bcm2835 Headphones`. When in doubt, check the OASIS dashboard's **AUDIO** stat — it labels each device as USB or not and shows RX/TX capability."* |
| **M4** | SETUP.md (missing) | No troubleshooting section anywhere in the document. | Add a "Troubleshooting" section with at minimum: *"Server won't start: `journalctl -u oasis -n 50`"*, *"Port already in use: `sudo fuser -k 8083/tcp`"*, *"Can't reach dashboard from phone: check `hostname -I` and that both devices are on the same Wi-Fi."* |
| **M5** | SETUP.md — Kiwix | Wikipedia download requires internet but no timing guidance given: "run this step while the Pi still has internet access." | Add: *"Run this while the Pi is still connected to the internet — it downloads ~316 MB (~5 min on typical broadband). Once downloaded, Kiwix serves it offline forever."* |
| **M6** | SETUP.md — Offline Maps | *"Option B — Download a pre-built PMTiles archive from protomaps.com"* — no URL, no file selection guidance. | Add a direct URL and guidance: *"Go to [https://maps.protomaps.com](https://maps.protomaps.com), select your region, and download the `.pmtiles` file. Typical state-level files are 1–5 GB."* |
| **Q10** | SETUP.md (missing) | If the user runs `./start.sh` in an SSH session and then closes the terminal window, the server stops. This is never addressed. | Add note: *"If you close the SSH window while `./start.sh` is running, the server stops. Use `enable-autostart-pi.py` for a persistent server, or run `./start.sh` inside a `screen` or `tmux` session."* |

---

## 🟢 Minor

| ID | Location | Issue |
|----|----------|-------|
| P1 | README — Port table | Lists optional-feature ports (8085, 8080, 8082, 8081, 7681) with no indication they only appear when those features are installed. |
| P2 | SETUP.md | The `--full-zip` flag description is now accurate but could be even simpler: *"Rarely needed."* |

---

## Fixed in previous pass ✅

The following issues were identified in the first review and have been fixed:

- ✅ No Pi OS flashing / headless setup instructions → added *Before you begin* section
- ✅ `git` not pre-installed → `sudo apt install -y git` added
- ✅ `<host-ip>` — no guidance → `hostname -I` and `arp -a` added
- ✅ `.venv` never explained → now explained as a Python virtual environment created by `setup-server.py`
- ✅ Auto-start: unclear whether to also run `./start.sh` → clarified (auto-start replaces `./start.sh`)
- ✅ `User=pi` hardcoded in service file → changed to `YOUR_USERNAME` with `whoami` instruction
- ✅ GrayWolf "web UI" — no URL → `http://<pi-ip>:8080` now stated; full config steps added
- ✅ RTL-SDR Trixie requirement — no way to check OS → `cat /etc/os-release` command added
- ✅ Wi-Fi hotspot for field use — absent → `nmcli` hotspot command added
- ✅ GrayWolf PMTiles sentence was misleading → rewritten as three clear options (A/B/C)
- ✅ `--full-zip` vs plain invocation confusing → clarified with a note
- ✅ Port-conflict warning: buried solution → reordered ("easiest fix is reboot")
- ✅ "Primes sudo once" jargon → replaced with plain English explanation
