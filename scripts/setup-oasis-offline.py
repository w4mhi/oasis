#!/usr/bin/env python3
"""
setup-oasis-offline.py
----------------------
Interactive OASIS feature installer / orchestrator. Lists every OASIS feature,
lets you pick what you want, then runs the matching install-*/enable-* scripts
in the right order. It does not reimplement anything — it delegates to the
existing scripts, so each remains the single source of truth.

Privilege model — run as your NORMAL user, not with sudo:

    python3 scripts/setup-oasis-offline.py

The orchestrator primes sudo once up front (a single password prompt) and keeps
the credential warm, so the sub-scripts' internal `sudo` calls don't re-prompt.
Each step keeps its correct context — e.g. setup-server.py builds the .venv as
you (not root), install-winlink.py writes your ~/.config/pat. Running the whole
thing under `sudo` would break those, so this script refuses to run as root.

Usage:
  python3 scripts/setup-oasis-offline.py                 # interactive menu
  python3 scripts/setup-oasis-offline.py --list          # list features and exit
  python3 scripts/setup-oasis-offline.py --all           # run everything (incl. data)
  python3 scripts/setup-oasis-offline.py --features graywolf,rtl-sdr,winlink
  python3 scripts/setup-oasis-offline.py --features kiwix --yes   # non-interactive

Re-running is safe: every sub-script is version-aware/idempotent, so you can run
this again later to add a feature.
"""

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import _hr, _ok, _info, _warn, _fail, has_internet

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Feature registry ────────────────────────────────────────────────────────────
class Feature:
    """One selectable feature that delegates to a script in scripts/."""
    def __init__(self, key, name, script, desc, category,
                 default=False, needs=(), internet=False, data=False,
                 reboot=False, args=()):
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

    @property
    def path(self):
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
            "Server", default=False, needs=["server"]),
    Feature("graywolf", "GrayWolf APRS (+ history API)", "install-graywolf.py",
            "APRS TNC/iGate/digipeater on :8080, plus the history API on :8085.",
            "Server", default=True, needs=["server"], internet=True),
    Feature("winlink", "Winlink (Pat)", "install-winlink.py",
            "Pat Winlink client + web UI on :8082 (Telnet works immediately).",
            "Server", default=True, internet=True),
    Feature("kiwix", "Kiwix (offline content server)", "install-kiwix.py",
            "kiwix-serve on :8081 to browse ZIM content. (Add content below.)",
            "Server", default=True, internet=True),
    Feature("webssh", "Web SSH (ttyd)", "install-webssh.py",
            "Browser terminal on :7681 (logs in via ssh to localhost).",
            "Server", default=True),

    # ── Audio: audio paths into GrayWolf (SDR dongle + DRA sound card) ─────────
    Feature("rtl-sdr", "RTL-SDR tools", "install-rtl-sdr.py",
            "rtl_test/rtl_fm + socat/tcpdump and the DVB-driver blacklist.",
            "Audio", default=True),
    Feature("rtl-feed", "RTL-SDR → GrayWolf APRS feed", "enable-rtl-sdr.py",
            "Stream demodulated APRS audio into GrayWolf (sdr_udp). Needs the dongle plugged in.",
            "Audio", default=False, needs=["rtl-sdr"]),
    Feature("dra-pi", "DRA-Pi-Zero sound card", "enable-dra-pi.py",
            "Configure the MastersCommunications DRA-Pi-Zero (WM8731 I²S codec) for GrayWolf — edits /boot/firmware/config.txt. REQUIRES A REBOOT.",
            "Audio", default=False, reboot=True),

    # ── Content / Data: large optional downloads ──────────────────────────────
    Feature("fcc", "FCC callsign database", "setup-fcc-database.py",
            "Download + index the FCC amateur license DB (~160 MB).",
            "Content / Data", default=False, internet=True, data=True),
    Feature("wikipedia", "Wikipedia content (ZIM)", "download-wikipedia.py",
            "Download Wikipedia ZIM files for Kiwix (1 GB to ~100 GB).",
            "Content / Data", default=False, internet=True, data=True),
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


def summarize(results):
    print()
    _hr()
    print("  Setup summary")
    _hr()
    icons = {"ok": "✓", "failed": "✗", "skipped": "–", "reboot": "⟳"}
    for state, f in results:
        print(f"   {icons.get(state, '?')}  {f.name}"
              + ("   (reboot required)" if state == "reboot" else ""))
    failed = [f for s, f in results if s == "failed"]
    reboot = [f for s, f in results if s == "reboot"]
    print()
    if failed:
        _warn(f"{len(failed)} step(s) failed — see the output above and re-run to retry.")
    else:
        _ok("All selected steps completed.")
    _info(f"Verify everything at  http://{_guess_host()}:8083/system/setup.html")

    if reboot:
        names = ", ".join(f.name for f in reboot)
        _warn(f"A reboot is required to finish: {names}")
        _info("After rebooting, re-run this setup to complete any post-reboot steps "
              "(e.g. DRA-Pi audio mixer).")
        if sys.stdin.isatty() and input("\n  Reboot now? [y/N]: ").strip().lower() in ("y", "yes"):
            subprocess.run(["sudo", "reboot"])


# ── Entry point ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Interactive OASIS feature installer (delegates to install-*/enable-* scripts).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 scripts/setup-oasis-offline.py\n"
                "  python3 scripts/setup-oasis-offline.py --list\n"
                "  python3 scripts/setup-oasis-offline.py --all\n"
                "  python3 scripts/setup-oasis-offline.py --features graywolf,rtl-sdr,winlink --yes\n"),
    )
    parser.add_argument("--list", action="store_true", help="List features and exit.")
    parser.add_argument("--all", action="store_true", help="Select every feature (including data downloads).")
    parser.add_argument("--features", metavar="A,B,C",
                        help="Comma-separated feature keys to run (see --list).")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Don't prompt to confirm the run.")
    parser.add_argument("--no-sudo-prime", action="store_true",
                        help="Skip priming sudo (each step will prompt as needed).")
    args = parser.parse_args()

    if args.list:
        print_menu()
        return

    # This orchestrator must run as the normal user (see module docstring).
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        _fail("Run as your normal user, NOT with sudo — I'll request sudo when needed.\n"
              "       e.g.  python3 scripts/setup-oasis-offline.py")

    # Resolve the selection.
    if args.all:
        keys = [f.key for f in FEATURES]
    elif args.features:
        keys = []
        for k in args.features.replace(",", " ").split():
            if k in BY_KEY:
                keys.append(k)
            else:
                _warn(f"Unknown feature key: {k}  (see --list)")
        if not keys:
            _fail("No valid feature keys given.")
    else:
        keys = interactive_select()

    if not keys:
        _info("Nothing selected — exiting.")
        return

    print("\n  Will run, in order:")
    plan = expand_with_prereqs(keys)
    for f in plan:
        print(f"   • {f.name}")

    if not args.yes:
        if input("\n  Proceed? [Y/n]: ").strip().lower() in ("n", "no"):
            _info("Aborted.")
            return

    if not args.no_sudo_prime:
        prime_sudo()

    results = []
    try:
        for f in plan:
            results.append(run_feature(f))
    finally:
        _sudo_stop.set()

    summarize(results)


if __name__ == "__main__":
    main()
