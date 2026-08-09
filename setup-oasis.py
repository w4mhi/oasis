#!/usr/bin/env python3
"""
setup-oasis.py
--------------
Interactive OASIS feature installer / orchestrator. Lists every OASIS feature,
lets you pick what you want, then runs the matching install-*/enable-* scripts
in the right order. It does not reimplement anything — it delegates to the
existing scripts, so each remains the single source of truth.

Privilege model — run as your NORMAL user, not with sudo:

    python3 setup-oasis.py

The orchestrator primes sudo once up front (a single password prompt) and keeps
the credential warm, so the sub-scripts' internal `sudo` calls don't re-prompt.
Each step keeps its correct context — e.g. setup-server.py builds the .venv as
you (not root), services/winlink/install.py writes your ~/.config/pat. Running the whole
thing under `sudo` would break those, so this script refuses to run as root.

Usage:
  python3 setup-oasis.py                 # interactive menu
  python3 setup-oasis.py --list          # list features and exit
  python3 setup-oasis.py --all           # run everything (incl. data)
  python3 setup-oasis.py --features graywolf,rtl-sdr,winlink
  python3 setup-oasis.py --features kiwix --yes   # non-interactive

Re-running is safe: every sub-script is version-aware/idempotent, so you can run
this again later to add a feature.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import _hr, _ok, _info, _warn, _fail, has_internet, ensure_scripts_executable
from common import manifest as M
from common import maidenhead as MH
from common import config_paths
from common import hardware_detect


# ── Feature registry ────────────────────────────────────────────────────────────
class Feature:
    """One selectable feature that delegates to a script in scripts/."""
    def __init__(self, key, name, script, desc, category,
                 default=False, needs=(), internet=False, data=False,
                 reboot=False, args=(), recommend="", record_only=False):
        self.key      = key
        self.name     = name
        self.script   = script
        self.desc     = desc
        self.category = category
        self.default  = default          # pre-checked in the menu?
        self.needs    = tuple(needs)      # prerequisite feature keys
        self.internet = internet          # warn if offline
        self.data     = data              # large/optional content download
        self.reboot   = reboot            # may require a reboot to take effect
        self.args     = tuple(args)
        self.recommend = recommend        # short next-step surfaced in the final report
        self.record_only = record_only    # no script — just record in the manifest
                                          # (toggles a dashboard card/link's visibility)

    @property
    def path(self):
        if not self.script:
            return None
        if "/" in self.script:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), self.script)
        return os.path.join(SCRIPTS_DIR, self.script)


# Order here is the run order. Software/services default-checked; data opt-in;
# boot-changing and hardware-gated steps left opt-in even though they're services.
FEATURES = [
    # ── Server: the web server and everything it fronts ────────────────────────
    Feature("server", "Server (.venv + deps)", "setup-server.py",
            "Create the Python .venv and install Flask/gunicorn/psutil (offline). Foundation for the web UI and the APRS API.",
            "Server", default=True),
    Feature("autostart", "Auto-start on boot", "enable-autostart-pi.py",
            "Install the systemd unit so the OASIS server starts at boot. Add --with-browser later for a Chromium kiosk.",
            "Server", default=True, needs=["server"],
            recommend="OASIS now starts on boot. Stop any manual ./scripts/start-server.sh first to free port 8083."),
    Feature("graywolf", "GrayWolf APRS (+ history API)", "services/graywolf/install.py",
            "APRS TNC/iGate/digipeater on :8080, plus the history API on :8085.",
            "Server", default=True, needs=["server"], internet=True,
            recommend="Open GrayWolf at :8080, add your audio device + an AFSK channel, then RESTART it: sudo systemctl restart graywolf"),
    Feature("winlink", "Winlink (Pat)", "services/winlink/install.py",
            "Pat Winlink client + web UI on :8082 (Telnet works immediately).",
            "Server", default=False, internet=True,
            recommend="Set your callsign and Winlink password in Pat at :8082."),
    Feature("winlink-digirig", "Winlink RF via DigiRig", "services/winlink/install.py",
            "Point the Winlink RF modem (Direwolf) at a DigiRig Mobile (USB sound card + "
            "CP210x serial RTS PTT) instead of the DRA-Pi. Both modem configs are written; "
            "the pat-direwolf service uses the DigiRig (no GPIO / no reboot). Auto-detects "
            "the DigiRig — plug it in before running.",
            "Server", default=False, needs=["winlink"],
            args=["--modem-interface", "digirig", "--modem-only"],
            recommend="pat-direwolf now uses the DigiRig. Verify PTT keys the radio: "
                      "direwolf -c ~/.config/direwolf/oasis-digirig-winlink.conf -x"),
    Feature("kiwix", "Kiwix (offline content server)", "services/kiwix/install.py",
            "kiwix-serve on :8081 to browse ZIM content. (Add content below.)",
            "Server", default=False, internet=True,
            recommend="Add ZIM content (e.g. the Wikipedia feature below) — Kiwix serves it at :8081."),
    Feature("webssh", "Web SSH (ttyd)", "services/webssh/install.py",
            "Browser terminal on :7681 (logs in via ssh to localhost).",
            "Server", default=True,
            recommend="Browser terminal at :7681 (logs in via ssh to localhost)."),
    Feature("openwebrx", "OpenWebRX (SDR monitor) — experimental", "services/openwebrx/install.py",
            "Browser SDR receiver/decoder on :8073 (RX only: voice/CW/RTTY/FT8/…). Installed OFF by default — start it from the dashboard. Needs internet (3rd-party apt repo).",
            "Server", default=False, needs=["server"], internet=True,
            recommend="OpenWebRX is OFF by default — start it from the dashboard (it stops GrayWolf + the SDR feed). Open :8073."),
    Feature("adsb", "ADS-B Aircraft", "services/adsb/install.py",
            "Aircraft tracking via dump1090-fa (RTL-SDR) + a local recorder/API on :8086, with a live layer on the offline map. Installed OFF by default — start it from the dashboard. Needs internet (apt).",
            "Server", default=False, needs=["server"], internet=True,
            recommend="ADS-B is OFF by default — start it from the dashboard (it stops GrayWolf + OpenWebRX + the SDR feed). Aircraft appear on the offline map."),
    Feature("service-controls", "Dashboard service controls", "enable-service-controls.py",
            "Grant a scoped sudoers rule so the dashboard start/stop buttons work (no passwords in the browser). Needed to start OpenWebRX (and stop GrayWolf/Kiwix/…) from the dashboard.",
            "Server", default=True, needs=["server"],
            recommend="Dashboard power buttons enabled (start/stop GrayWolf · Kiwix · OpenWebRX · APRS feed)."),
    Feature("ap-fallback", "Wi-Fi AP fallback", "enable-ap-fallback.py",
            "Host the OASIS Wi-Fi access point (SSID OASIS, WPA2, 10.42.0.1) whenever no known network is in range, so the dashboard is always reachable in the field. Adds avahi (oasis.local) and the dashboard Wi-Fi picker (scan/join/forget). Prefers known Wi-Fi; falls back to the AP. Asks for your Wi-Fi country (needed for the AP to broadcast) if it isn't already set.",
            "Server", default=True, needs=["server"],
            recommend="AP fallback armed — the Pi hosts the OASIS Wi-Fi (password oasis-emcomm) when no known network is in range."),

    Feature("satellites-piper", "Natural pass-alert voice (Piper)", "services/satellites/install-piper.py",
            "Swap the robotic espeak pass-alert voice for Piper, a neural voice trained on recorded speech. Optional — without it alerts still speak, using espeak's female variant. Needs the engine and voice model in your offline bundle: build it on a connected machine with scripts/create-oasis-offline.py, which downloads ~60 MB from upstream. Runs on any Pi (32- and 64-bit); a Pi 3 takes a few seconds per sentence, which is fine for an alert that fires ten minutes ahead.",
            "Display", default=False, needs=["satellites"],
            recommend="Piper voice installed — restart the browser, then the next pass alert speaks in the neural voice."),

    # ── Display: local screen — Chromium kiosk / desktop launcher ──────────────
    Feature("kiosk", "Kiosk mode (Chromium fullscreen)", "enable-autostart-pi.py",
            "Auto-start the server and launch a fullscreen Chromium kiosk on the Pi's screen. Raspberry Pi OS with Desktop.",
            "Display", default=False, needs=["server"], args=["--with-browser"],
            recommend="Kiosk set up — reboot to see OASIS fullscreen on the local screen."),
    Feature("kiosk-dashboard", "Kiosk mode — OASIS Dashboard, 7″ touchscreen (800×480)", "enable-autostart-pi.py",
            "Auto-start the server and launch the OASIS Dashboard layout (/oasis-dashboard/dashboard.html, 800×480) in Chromium with touch enabled. For the BigTreeTech 7″ panel (the reference display) or any 800×480 touchscreen. Alternative to the fullscreen kiosk above. Raspberry Pi OS with Desktop. Tip: the BigTreeTech panel also has an onboard RTC — see the 'BigTreeTech 7″ RTC (PCF8563)' item.",
            "Display", default=False, needs=["server"], args=["--resolution", "800x480"],
            recommend="Dashboard kiosk (800×480) set up — reboot to see the touchscreen layout on the local screen."),
    Feature("kiosk-dashboard-wide", "Kiosk mode — OASIS Dashboard, 10″ wide panel (1920×1200)", "enable-autostart-pi.py",
            "Auto-start the server and launch the OASIS Dashboard layout (/oasis-dashboard/dashboard.html, 1920×1200) in Chromium with touch enabled. For a 10″ 1920×1200 wide panel. Alternative to the 800×480 dashboard above — pick one. Raspberry Pi OS with Desktop.",
            "Display", default=False, needs=["server"], args=["--resolution", "1920x1200"],
            recommend="Dashboard kiosk (1920×1200) set up — reboot to see the wide layout on the local screen."),
    Feature("desktop-icon", "Desktop launcher icon", "enable-autostart-pi.py",
            "Auto-start the server and add a clickable OASIS desktop shortcut. Raspberry Pi OS with Desktop.",
            "Display", default=False, needs=["server"], args=["--desktop-icon"],
            recommend="Desktop icon added — double-click it to open OASIS."),
    Feature("cm4stack", "CM4Stack panel (ST7789 + touch)", "features/cm4stack/install-cm4stack.py",
            "Set up a Raspberry Pi CM4 / M5Stack CM4Stack as a standalone OASIS panel: "
            "ST7789V2 SPI display + GT911 touch + GPIO fan (headless boot + oasis-panel.service). "
            "REQUIRES A REBOOT (a second reboot may be needed for the GT911 touch fix).",
            "Display", default=False, needs=["server"], internet=True, reboot=True,
            recommend="CM4Stack panel configured — reboot to bring up the display, then re-run to apply the touch fix."),

    # ── Audio: audio paths into GrayWolf (SDR dongle + DRA sound card) ─────────
    Feature("rtl-sdr", "RTL-SDR tools", "features/rtl-sdr/install-rtl-sdr.py",
            "rtl_test/rtl_fm + socat/tcpdump and the DVB-driver blacklist.",
            "Audio", default=False,
            recommend="RTL-SDR Blog V4 needs Pi OS Trixie (librtlsdr >= 2.0). Verify the dongle: rtl_test -t"),
    Feature("rtl-feed", "RTL-SDR → GrayWolf APRS feed", "services/rtl-feed/install.py",
            "Stream demodulated APRS audio into GrayWolf (sdr_udp). Needs the dongle plugged in.",
            "Audio", default=False, needs=["rtl-sdr"],
            recommend="In GrayWolf add the sdr_udp device + an AFSK1200 channel, then RESTART it: sudo systemctl restart graywolf"),
    Feature("dra-pi", "DRA-Pi-Zero sound card", "features/dra-audio-interface/enable-dra-pi.py",
            "Configure the MastersCommunications DRA-Pi-Zero (WM8731 I²S codec) for GrayWolf — edits /boot/firmware/config.txt. REQUIRES A REBOOT.",
            "Audio", default=False, reboot=True,
            recommend="Reboot, then re-run this setup to apply the DRA-Pi ALSA mixer."),
    Feature("dra-rx-led", "DRA-Pi green RX LED", "features/dra-audio-interface/enable-dra-rx-led.py",
            "Pulse the DRA-Pi-Zero's green RX LED (GPIO 16) on GrayWolf receive activity (polls the history DB). The red TX LED (GPIO 12) is keyed by GrayWolf itself — nothing to install.",
            "Audio", default=False, needs=["dra-pi", "graywolf"],
            recommend="Green RX LED follows GrayWolf RX. Verify wiring: python3 features/dra-audio-interface/enable-dra-rx-led.py --self-test"),

    # ── GPS / Time: GPS-disciplined clock (gpsd + chrony) ──────────────────────
    Feature("gps", "GPS time (gpsd + chrony)", "features/gps/install-gps.py",
            "Discipline the system clock from a GPS receiver (gpsd + chrony + RTC) for accurate time with no internet — needed for FT8/WSPR/SSTV decode timing. Auto-detects the device; needs internet to apt-install.",
            "GPS", default=False, internet=True,
            recommend="GPS time set up. Give the antenna sky view, then check: cgps -s  and  chronyc tracking."),

    # ── RTC: battery-backed hardware clocks (standalone) ───────────────────────
    Feature("rtc", "Witty Pi 3 RTC (DS3231)", "features/rtc-hat/enable-rtc.py",
            "Configure the Witty Pi 3's DS3231 hardware clock (i2c-rtc overlay + remove fake-hwclock) so the Pi keeps time across reboots / power loss with no network. REQUIRES A REBOOT.",
            "RTC", default=False, reboot=True, args=["--board", "wittypi"],
            recommend="Reboot to load the RTC, then once the clock is correct: sudo hwclock -w"),
    # Key matches the web Setup feature key (and features/rtc-raspad/) so both
    # front ends record the same thing in installed-services.json. Boxes still
    # carrying the old `rtc-7inch` key are mapped by SETUP_FEATURE_ALIASES.
    Feature("rtc-raspad", "BigTreeTech 7″ RTC (PCF8563)", "features/rtc-raspad/enable-rtc.py",
            "Configure the BigTreeTech 7″ touchscreen's onboard PCF8563 hardware clock (i2c-rtc overlay on the DSI ribbon's i2c_csi_dsi bus + remove fake-hwclock) so the Pi keeps time across reboots / power loss with no network. The RTC is on the DSI bus, not the GPIO header — that's why it never appears on `i2cdetect -y 1`. REQUIRES A REBOOT.",
            "RTC", default=False, reboot=True,
            recommend="Reboot to load the RTC, then once the clock is correct: sudo hwclock -w"),

    # ── Content / Data: large optional downloads ──────────────────────────────
    Feature("fcc", "FCC callsign database", "services/fcc_database/install.py",
            "Download + index the FCC amateur license DB (~160 MB).",
            "Content / Data", default=True, internet=True, data=True,
            recommend="FCC callsign lookup is ready on the dashboard."),
    Feature("wikipedia", "Wikipedia content (ZIM)", "services/kiwix/download-wikipedia.py",
            "Download Wikipedia ZIM files for Kiwix (1 GB to ~100 GB).",
            "Content / Data", default=False, internet=True, data=True,
            recommend="ZIM downloaded — Kiwix serves it at :8081 (install Kiwix if you haven't)."),
    Feature("satellites-tle", "Satellite list (SatNOGS + CelesTrak)", "services/satellites/build-roster.py",
            "Build the satellite list from SatNOGS (freqs/modes) + CelesTrak (TLEs) "
            "so pass prediction works (~5 MB fetch). The list + TLEs go stale in a few "
            "days — click the age pill on the Satellites page to refresh, or re-run "
            "this script when you next have internet.",
            "Content / Data", default=True, internet=True,
            recommend="Satellite list is ready — refresh via the age pill (or re-run build-roster.py) every few days."),
    Feature("satellites-voice", "Satellite pass-alert voice", "services/satellites/install-voice.py",
            "Text-to-speech for satellite pass alerts (speech-dispatcher + espeak-ng, ~5 MB apt). "
            "After the T-10 chime the Satellites page speaks which bird is coming + the pass "
            "elevation; without it, alerts still chime. Needs internet (apt).",
            "Content / Data", default=True, internet=True,
            recommend="Pass-alert voice ready — arm a satellite's bell (🔔) on the Satellites page."),
    Feature("repeaterbook", "Repeater Book listing", None,
            "Show the Repeater Book link on the dashboard. Needs static/repeaterbook/repeaterbook.csv "
            "(copyright — export your own from repeaterbook.com and drop it in).",
            "Content / Data", default=True, record_only=True,
            recommend="Repeater Book link is on the dashboard — drop your repeaterbook.csv into static/repeaterbook/."),
    Feature("forms", "ICS Forms (dashboard section)", None,
            "Show the ICS-205/213/214/309 forms section on the dashboard. The forms ship with "
            "OASIS — this only toggles whether the section is shown.",
            "Content / Data", default=True, record_only=True,
            recommend="ICS Forms section is shown on the dashboard."),
]

BY_KEY = {f.key: f for f in FEATURES}


# ── sudo priming ────────────────────────────────────────────────────────────────
_sudo_stop = threading.Event()


def prime_sudo():
    """Validate sudo once and keep the credential warm in the background, so the
    delegated scripts' internal sudo calls don't prompt again."""
    if shutil.which("sudo") is None:
        _warn("sudo not found — system steps (apt/systemctl) may fail.")
        return False
    _info("Some steps need administrator rights — you'll be asked once for your password.")
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        _warn("Could not validate sudo. Individual steps may prompt or fail.")
        return False

    def _keepalive():
        # Refresh the sudo timestamp until we're done (well under the default 5m).
        while not _sudo_stop.wait(50):
            subprocess.run(["sudo", "-n", "true"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    threading.Thread(target=_keepalive, daemon=True, name="sudo-keepalive").start()
    return True


# ── Selection ───────────────────────────────────────────────────────────────────
def expand_with_prereqs(keys):
    """Return Features for *keys* plus any prerequisites, in run order."""
    sel = set(keys)
    changed = True
    while changed:
        changed = False
        for f in FEATURES:
            if f.key in sel:
                for need in f.needs:
                    if need not in sel:
                        sel.add(need)
                        _info(f"  + {BY_KEY[need].name}  (required by {f.name})")
                        changed = True
    return [f for f in FEATURES if f.key in sel]


def print_menu():
    print("\n  OASIS Setup — choose features")
    _hr()
    cat = None
    for i, f in enumerate(FEATURES, 1):
        if f.category != cat:
            cat = f.category
            print(f"\n  {cat}")
        mark = "x" if f.default else " "
        tags = []
        if f.data:     tags.append("data")
        if f.internet: tags.append("internet")
        if f.needs:    tags.append("needs " + ",".join(f.needs))
        tag = f"  ({', '.join(tags)})" if tags else ""
        print(f"   [{mark}] {i:>2}. {f.name}{tag}")
        print(f"          {f.desc}")
    print()
    _info("Defaults [x] = software/services. Data downloads are opt-in.")


def parse_selection(raw):
    """Map a menu response to a list of feature keys, or None to abort."""
    raw = raw.strip().lower()
    if raw in ("q", "quit", "exit"):
        return None
    if raw == "":
        return [f.key for f in FEATURES if f.default]
    if raw == "all":
        return [f.key for f in FEATURES]
    if raw == "none":
        return []
    keys = []
    for tok in raw.replace(",", " ").split():
        if not tok.isdigit() or not (1 <= int(tok) <= len(FEATURES)):
            _warn(f"Ignoring invalid choice: {tok}")
            continue
        keys.append(FEATURES[int(tok) - 1].key)
    return keys


def _text_select():
    """Numbered fallback menu (no TTY / curses unavailable)."""
    print_menu()
    while True:
        raw = input("\n  Selection — Enter=defaults · numbers (e.g. 1,3,5) · 'all' · 'none' · 'q': ")
        keys = parse_selection(raw)
        if keys is None:
            _info("Aborted — nothing was changed.")
            sys.exit(0)
        return keys


def _can_use_curses():
    """curses needs a real interactive terminal on both ends."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import curses  # noqa: F401
        return True
    except Exception:
        return False


def _curses_select(stdscr):
    """Arrow-key checkbox UI with OK / Cancel buttons.

    Focus moves through the feature rows and the two buttons. ENTER only runs
    when focus is on OK — pressing ENTER on a feature just toggles it, so a
    stray ENTER can't start the install by accident. Returns selected keys, or
    None if cancelled.
    """
    import curses
    import textwrap

    curses.curs_set(0)
    stdscr.keypad(True)
    try:
        curses.use_default_colors()
    except Exception:
        pass

    checked = {f.key: f.default for f in FEATURES}
    n   = len(FEATURES)
    OK, CANCEL = n, n + 1          # two extra focusable "rows" after the features
    total = n + 2
    idx = 0

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        def put(y, x, s, attr=0):
            if 0 <= y < max_y and 0 <= x < max_x:
                stdscr.addnstr(y, x, s, max(0, max_x - x - 1), attr)

        put(0, 2, "OASIS Setup — select features", curses.A_BOLD)
        put(1, 2, "─" * max(0, max_x - 4))

        # Flat rows: category headers (non-selectable) + feature rows.
        rows, last_cat = [], None
        for i, f in enumerate(FEATURES):
            if f.category != last_cat:
                last_cat = f.category
                rows.append(("cat", f.category))
            rows.append(("feat", i))

        # Size the list to its content, but cap it so the description (sep + 2),
        # the button row and the footer (sep + hint) — 6 rows — always stay on
        # screen. Footer hint lands on the last visible row (list_top+list_h+5).
        list_top = 3
        list_h   = max(3, min(len(rows), max_y - 9))
        desc_sep = list_top + list_h
        btn_y    = desc_sep + 3
        foot_sep = btn_y + 1

        cur_feat = idx if idx < n else n - 1
        cur_row  = next(r for r, (k, p) in enumerate(rows) if k == "feat" and p == cur_feat)
        start = 0
        if len(rows) > list_h:
            start = max(0, min(cur_row - list_h // 2, len(rows) - list_h))

        y = list_top
        for r in range(start, min(start + list_h, len(rows))):
            kind, payload = rows[r]
            if kind == "cat":
                put(y, 2, payload, curses.A_DIM | curses.A_UNDERLINE)
            else:
                f = FEATURES[payload]
                box = "[X]" if checked[f.key] else "[ ]"
                tag = "  (data)" if f.data else (" (reboot)" if f.reboot else "")
                line = f"  {box} {f.name}{tag}"
                put(y, 2, line.ljust(max(0, max_x - 4)),
                    curses.A_REVERSE if (idx < n and payload == idx) else 0)
            y += 1

        # Description pane: highlighted feature, or button help when on a button.
        put(desc_sep, 2, "─" * max(0, max_x - 4))
        if idx < n:
            f = FEATURES[idx]
            prereq = (" · needs " + ", ".join(f.needs)) if f.needs else ""
            for j, wl in enumerate(textwrap.wrap(f.desc + prereq, max(10, max_x - 6))[:2]):
                put(desc_sep + 1 + j, 3, wl, curses.A_DIM)
        else:
            put(desc_sep + 1, 3,
                "OK = run the checked features · Cancel = quit without running",
                curses.A_DIM)

        # Buttons.
        ok_lbl, cn_lbl = "[ OK — run ]", "[ Cancel ]"
        put(btn_y, 4, ok_lbl,
            curses.A_BOLD | (curses.A_REVERSE if idx == OK else 0))
        put(btn_y, 4 + len(ok_lbl) + 3, cn_lbl,
            curses.A_BOLD | (curses.A_REVERSE if idx == CANCEL else 0))

        put(foot_sep, 2, "─" * max(0, max_x - 4))
        put(foot_sep + 1, 2,
            "Use SPACE to select/deselect features · ↑↓ move · A/N all/none · "
            "TAB→OK · OK runs · Q quit")

        stdscr.refresh()

        c = stdscr.getch()
        if c in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % total
        elif c in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % total
        elif c in (curses.KEY_LEFT, ord("h")) and idx == CANCEL:
            idx = OK
        elif c in (curses.KEY_RIGHT, ord("l")) and idx == OK:
            idx = CANCEL
        elif c == 9:                       # Tab → jump to OK
            idx = OK
        elif c == ord(" "):
            if idx < n:
                checked[FEATURES[idx].key] = not checked[FEATURES[idx].key]
        elif c in (ord("a"), ord("A")):
            for k in checked:
                checked[k] = True
        elif c in (ord("n"), ord("N")):
            for k in checked:
                checked[k] = False
        elif c in (curses.KEY_ENTER, 10, 13):
            if idx == OK:
                return [f.key for f in FEATURES if checked[f.key]]
            if idx == CANCEL:
                return None
            checked[FEATURES[idx].key] = not checked[FEATURES[idx].key]   # toggle, never start
        elif c in (ord("q"), ord("Q"), 27):   # q / Esc
            return None


def interactive_select():
    """Curses checkbox UI when possible, else the numbered text menu."""
    if _can_use_curses():
        import curses
        try:
            keys = curses.wrapper(_curses_select)
        except Exception as e:
            _warn(f"Interactive UI unavailable ({e}) — using text menu.")
        else:
            if keys is None:
                _info("Aborted — nothing was changed.")
                sys.exit(0)
            return keys
    return _text_select()


# ── Run ─────────────────────────────────────────────────────────────────────────
def run_feature(f):
    # Record-only features have no script — they just flip a dashboard card/link
    # on (recorded in the manifest). Nothing to execute.
    if f.record_only:
        print()
        _hr()
        print(f"  ▶  {f.name}   (dashboard visibility)")
        _hr()
        _ok(f"{f.name}: enabled")
        return ("ok", f)
    if not os.path.exists(f.path):
        _warn(f"{f.script} not found — skipping {f.name}.")
        return ("skipped", f)
    print()
    _hr()
    print(f"  ▶  {f.name}   ({f.script})")
    _hr()
    if f.internet and not has_internet():
        _warn("No internet detected — this step may fail or fall back to bundled assets.")
    # Inherit stdio so prompts (e.g. Winlink password) and coloured output pass through.
    rc = subprocess.run([sys.executable, f.path, *f.args]).returncode
    if rc == 0:
        _ok(f"{f.name}: done")
        return ("ok", f)
    if rc == 10:                      # convention: success, but a reboot is needed
        _ok(f"{f.name}: done — a reboot is required to take effect")
        return ("reboot", f)
    _warn(f"{f.name}: FAILED (exit {rc})")
    return ("failed", f)


def _guess_host():
    out = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    return out[0] if out else "<pi-ip>"


# Records which features are installed so the dashboard can hide cards for
# services the user chose NOT to install. Lives at the suite root (next to this
# script) so the server serves it via /api/installed-services. Cumulative: a
# re-run merges newly-installed keys into the existing set (never shrinks it,
# matching the idempotent “re-run to add a feature” model) — EXCEPT the
# authoritative gate features below, which honor tick/untick when offered.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
INSTALLED_MANIFEST = config_paths.installed_services_json(REPO_ROOT)

# Operator identity (callsign + Maidenhead grid) shown on the dashboard pill.
# Seeded here so the suite ships personalised; the dashboard still lets each
# browser override it (localStorage). Lives under configuration/, served at
# /station.json via an explicit Flask route (server/app.py).
STATION_MANIFEST = config_paths.station_json(REPO_ROOT)

# Visibility-toggle features for static content/data. Unlike service installs
# (which only accumulate), these reflect the operator's choice each run: ticking
# shows the dashboard card/link, unticking hides it. Only acted on when the
# feature was actually offered this run (full menu / --all, or named in
# --features), so a targeted run never silently hides an unrelated card.
GATE_AUTHORITATIVE = {"fcc", "repeaterbook", "wikipedia", "forms"}


def configure_station(callsign=None, grid=None, lat=None, lon=None, interactive=True):
    """Seed station.json with the operator's callsign + Maidenhead grid (and, for
    the APRS map's "home" node, latitude/longitude) so the dashboard ships
    personalised. Idempotent: pre-fills from any existing file; pressing Enter
    keeps the current value. Coordinates can be typed directly or derived from
    the grid square. Non-interactive runs only write when values are passed in.
    Browsers can still override callsign/grid via the dashboard pill."""
    cur = {"callsign": "", "grid": "", "lat": None, "lon": None}
    try:
        with open(STATION_MANIFEST) as fh:
            prev = json.load(fh)
            cur["callsign"] = str(prev.get("callsign", "") or "")
            cur["grid"] = str(prev.get("grid", "") or "")
            cur["lat"] = prev.get("lat", None)
            cur["lon"] = prev.get("lon", None)
    except (FileNotFoundError, ValueError, OSError):
        pass

    if interactive and sys.stdin.isatty():
        try:
            ans = input(f"\n  Your callsign [{cur['callsign'] or 'N0CALL'}]: ").strip().upper()
            if ans:
                callsign = ans
            ans = input(f"  Your grid square (e.g. CN87) [{cur['grid'] or '—'}]: ").strip().upper()
            if ans:
                grid = ans
            # Coordinates anchor the operator's own "home" node on the APRS map.
            # Enter lat,lon directly, or press Enter to derive them from the grid.
            eff_grid = (grid or cur["grid"]).strip().upper()
            cur_ll = (f"{cur['lat']},{cur['lon']}"
                      if cur["lat"] is not None and cur["lon"] is not None else "—")
            ans = input(
                f"  Your lat,lon — blank to derive from grid {eff_grid or 'n/a'} "
                f"[{cur_ll}]: ").strip()
            if ans:
                parsed = _parse_latlon(ans)
                if parsed:
                    lat, lon = parsed
                else:
                    _warn("Could not parse coordinates — expected 'lat,lon' "
                          "(e.g. 47.55,-122.02). Leaving them unchanged.")
            elif eff_grid:
                derived = grid_to_latlon(eff_grid)
                if derived:
                    lat, lon = derived
                    _info(f"Derived {lat}, {lon} from grid {eff_grid}.")
                else:
                    _warn(f"Grid {eff_grid} is not a valid Maidenhead locator — "
                          "leaving coordinates unset.")
        except (EOFError, KeyboardInterrupt):
            print()

    callsign = (callsign or cur["callsign"]).strip().upper()
    grid = (grid or cur["grid"]).strip().upper()
    if lat is None:
        lat = cur["lat"]
    if lon is None:
        lon = cur["lon"]
    if not callsign and not grid and lat is None and lon is None:
        return                                   # nothing supplied → leave default N0CALL

    payload = {"callsign": callsign or "N0CALL", "grid": grid or "",
               "lat": lat, "lon": lon,
               "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if (cur["callsign"], cur["grid"], cur["lat"], cur["lon"]) == \
       (payload["callsign"], payload["grid"], payload["lat"], payload["lon"]):
        return
    try:
        os.makedirs(config_paths.config_dir(REPO_ROOT), exist_ok=True)
        with open(STATION_MANIFEST, "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
        _ok(f"Saved station identity → {os.path.basename(STATION_MANIFEST)}")
    except OSError as e:
        _warn(f"Could not write {os.path.basename(STATION_MANIFEST)}: {e}")


def _parse_latlon(text):
    """Parse 'lat,lon' (comma- or space-separated) into (lat, lon) floats, or
    None if it isn't two sane, in-range decimal numbers."""
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (round(lat, 4), round(lon, 4))


def grid_to_latlon(grid):
    """Maidenhead locator (e.g. CN87 or CN87XN) → (lat, lon) at the square /
    subsquare centre, or None if the grid isn't valid. Mirrors the map's
    client-side gridToLonLat() so seeded and derived coordinates agree."""
    return MH.grid_to_latlon(grid)



def record_installed(results, offered_gate=frozenset()):
    """Update installed-services.json from this run.

    Service installs accumulate (union). Authoritative gate features that were
    *offered* this run are set to exactly the operator's choice (ticked = shown,
    unticked = hidden).
    """
    selected_keys = {f.key for _state, f in results}                 # everything in the plan
    ok_keys       = {f.key for state, f in results if state in ("ok", "reboot")}
    existing = set()
    try:
        with open(INSTALLED_MANIFEST) as fh:
            prev = json.load(fh)
        if isinstance(prev.get("features"), list):
            existing = {str(k) for k in prev["features"]}
    except (FileNotFoundError, ValueError, OSError):
        pass

    merged = existing | ok_keys
    # Honor tick/untick for the gate features the operator was actually shown.
    for k in GATE_AUTHORITATIVE & offered_gate:
        if k in selected_keys:
            merged.add(k)        # ticked → show (offline pill if its data is absent)
        else:
            merged.discard(k)    # unticked → hide

    if merged == existing:
        return
    # Delegate the write to the schema module (single owner of the manifest).
    # Removal records are attached by the installer worker / web path (Plan 1
    # Task 7); the CLI relies on backfill to regenerate them on demand.
    from common import installed_services
    try:
        installed_services.add_installed(REPO_ROOT, merged - existing, {})
        for k in existing - merged:          # gate untick → hide
            installed_services.remove_installed(REPO_ROOT, {k})
        _ok(f"Recorded installed features → {os.path.basename(INSTALLED_MANIFEST)}")
    except OSError as e:
        _warn(f"Could not write {os.path.basename(INSTALLED_MANIFEST)}: {e}")


def summarize(results):
    """Print a clean, clearly-separated final report: what was installed, then the
    next-step recommendations for exactly those features — so they aren't buried in
    the scrolling install log."""
    icons  = {"ok": "✓", "failed": "✗", "skipped": "–", "reboot": "⟳"}
    failed = [f for s, f in results if s == "failed"]
    reboot = [f for s, f in results if s == "reboot"]
    bar    = "═" * 60

    # ── A hard separation from the install log, then the report ─────────────────
    print("\n\n  " + bar)
    print("  INSTALL REPORT")
    print("  " + bar)

    print("\n  Installed / attempted:")
    for state, f in results:
        tag = "   (reboot required)" if state == "reboot" else ""
        print(f"     {icons.get(state, '?')}  {f.name}{tag}")

    # Next steps only for what actually installed (ok/reboot) and that has advice.
    recs = [(f.name, f.recommend) for s, f in results
            if s in ("ok", "reboot") and getattr(f, "recommend", "")]
    if recs:
        print("\n  Next steps:")
        for name, rec in recs:
            print(f"     • {name}")
            print(f"         {rec}")

    print("\n  " + "─" * 60)
    if failed:
        _warn(f"{len(failed)} step(s) FAILED: {', '.join(f.name for f in failed)} "
              "— see the log above and re-run to retry.")
    else:
        _ok("All selected steps completed.")
    _info(f"Verify everything at  http://{_guess_host()}:8083/server/system/setup.html")
    print("  " + bar + "\n")

    if reboot:
        names = ", ".join(f.name for f in reboot)
        _warn(f"A reboot is required to finish: {names}")
        _info("After rebooting, run the matching script to enable each one:")
        for f in reboot:
            script_path = f.script if "/" in f.script else f"scripts/{f.script}"
            _info(f"  python3 {script_path}   # {f.name}")
        _info("(Re-running this setup does the same thing.)")
        if sys.stdin.isatty() and input("\n  Reboot now? [y/N]: ").strip().lower() in ("y", "yes"):
            subprocess.run(["sudo", "reboot"])


# ── Environment pre-flight (internet + suite + capability gates) ─────────────────
# Colour only when writing to a real terminal — never dump escape codes into logs.
_COLOR  = sys.stdout.isatty()
_GREEN  = "\033[32m" if _COLOR else ""
_YELLOW = "\033[33m" if _COLOR else ""
_RESET  = "\033[0m"  if _COLOR else ""

# Map this menu's feature keys to manifest feature names (only those with gates
# matter here). The installers themselves do the full source resolution.
_KEY_TO_MANIFEST = {"rtl-sdr": "rtl-sdr"}


def _host_suite():
    """Host Debian suite codename (Ubuntu LTS mapped to its Debian base)."""
    try:
        s = subprocess.run(["lsb_release", "-cs"], capture_output=True,
                           text=True).stdout.strip().lower()
    except Exception:
        s = ""
    return {"jammy": "bookworm", "focal": "bullseye",
            "noble": "bookworm"}.get(s, s) or "unknown"


def environment_preflight(plan):
    """Show internet + suite up front, then flag capability gates before installing.

    Green when online (newest packages come from the internet); yellow when offline
    (we install from the bundle, which must match the host suite). Warns if a
    selected feature has a min_version gate the host may not satisfy — e.g. the
    RTL-SDR Blog V4 needs librtlsdr >= 2.0, which bookworm cannot provide.
    """
    online = has_internet()
    suite  = _host_suite()

    print()
    _hr()
    print("  Environment")
    _hr()
    if online:
        print(f"  {_GREEN}● Internet detected — packages will be downloaded from the "
              f"internet (newest available).{_RESET}")
    else:
        print(f"  {_YELLOW}● No internet — installing from the offline bundle, which "
              f"must match this host's suite ({suite}).{_RESET}")
    _info(f"Host suite: {suite}")

    for f in plan:
        feat = _KEY_TO_MANIFEST.get(f.key)
        if not feat:
            continue
        try:
            gate = M.min_version(feat)
        except Exception:
            gate = None
        if not gate:
            continue
        for pkg, spec in gate.items():
            req    = spec.get("min") if isinstance(spec, dict) else spec
            reason = spec.get("reason") if isinstance(spec, dict) else None
            print(f"  {_YELLOW}⚠ {f.name}: needs {pkg} >= {req} to work fully.{_RESET}")
            if reason:
                _info(f"    {reason}")
            if online:
                _info("    Online: apt is used if it offers a newer version than the bundle.")
            else:
                _info(f"    Offline: make sure the '{suite}' bundle ships {pkg} >= {req}.")


# ── Entry point ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Interactive OASIS feature installer (delegates to install-*/enable-* scripts).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 setup-oasis.py\n"
                "  python3 setup-oasis.py --list\n"
                "  python3 setup-oasis.py --all\n"
                "  python3 setup-oasis.py --features graywolf,rtl-sdr,winlink --yes\n"),
    )
    parser.add_argument("--list", action="store_true", help="List features and exit.")
    parser.add_argument("--all", action="store_true", help="Select every feature (including data downloads).")
    parser.add_argument("--features", metavar="A,B,C",
                        help="Comma-separated feature keys to run (see --list).")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Don't prompt to confirm the run.")
    parser.add_argument("--no-sudo-prime", action="store_true",
                        help="Skip priming sudo (each step will prompt as needed).")
    parser.add_argument("--callsign", metavar="CALL", help="Set the dashboard callsign (station.json).")
    parser.add_argument("--grid", metavar="GRID", help="Set the dashboard Maidenhead grid (station.json).")
    parser.add_argument("--lat", metavar="LAT", type=float, help="Set the station latitude (station.json).")
    parser.add_argument("--lon", metavar="LON", type=float, help="Set the station longitude (station.json).")
    args = parser.parse_args()

    if args.list:
        print_menu()
        return

    # This orchestrator must run as the normal user (see module docstring).
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        _fail("Run as your normal user, NOT with sudo — I'll request sudo when needed.\n"
              "       e.g.  python3 setup-oasis.py")

    # Make sure every helper script is executable (a fresh clone/zip can drop +x).
    ensure_scripts_executable(os.path.dirname(os.path.abspath(__file__)))

    # Seed the dashboard pill identity (callsign + grid). Skips silently if
    # already set and nothing new is given.
    configure_station(args.callsign, args.grid, args.lat, args.lon, interactive=not args.yes)

    # Resolve the selection.
    if args.all:
        keys = [f.key for f in FEATURES]
        offered_gate = GATE_AUTHORITATIVE
    elif args.features:
        keys = []
        for k in args.features.replace(",", " ").split():
            if k in BY_KEY:
                keys.append(k)
            else:
                _warn(f"Unknown feature key: {k}  (see --list)")
        if not keys:
            _fail("No valid feature keys given.")
        # Only the named gate features were “offered” — never hide unrelated cards.
        offered_gate = GATE_AUTHORITATIVE & set(keys)
    else:
        keys = interactive_select()
        offered_gate = GATE_AUTHORITATIVE   # full menu shown → all gate features offered

    if not keys:
        _info("Nothing selected — exiting.")
        return

    print("\n  Will run, in order:")
    plan = expand_with_prereqs(keys)
    for f in plan:
        print(f"   • {f.name}")

    environment_preflight(plan)

    if not args.yes:
        if input("\n  Proceed? [Y/n]: ").strip().lower() in ("n", "no"):
            _info("Aborted.")
            return

    if not args.no_sudo_prime:
        prime_sudo()

    results = []
    try:
        for f in plan:
            # RTL-SDR feature: refuse to install blind without hardware detected
            if f.key == "rtl-sdr":
                result = hardware_detect.scan()
                if not result["rtl_sdr"]:
                    _warn("No RTL-SDR dongle detected — skipping RTL-SDR install.")
                    _warn("Plug in a dongle and re-run setup, or install this feature later "
                          "from the dashboard once a dongle is connected.")
                    results.append(("skipped", f))
                    continue
            results.append(run_feature(f))
    finally:
        _sudo_stop.set()

    record_installed(results, offered_gate)
    summarize(results)


if __name__ == "__main__":
    main()
