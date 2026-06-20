#!/usr/bin/env python3
"""
oasis-setup.py
--------------
Interactive OASIS feature installer / orchestrator. Lists every OASIS feature,
lets you pick what you want, then runs the matching install-*/enable-* scripts
in the right order. It does not reimplement anything — it delegates to the
existing scripts, so each remains the single source of truth.

Privilege model — run as your NORMAL user, not with sudo:

    python3 scripts/oasis-setup.py

The orchestrator primes sudo once up front (a single password prompt) and keeps
the credential warm, so the sub-scripts' internal `sudo` calls don't re-prompt.
Each step keeps its correct context — e.g. setup-server.py builds the .venv as
you (not root), install-winlink.py writes your ~/.config/pat. Running the whole
thing under `sudo` would break those, so this script refuses to run as root.

Usage:
  python3 scripts/oasis-setup.py                 # interactive menu
  python3 scripts/oasis-setup.py --list          # list features and exit
  python3 scripts/oasis-setup.py --all           # run everything (incl. data)
  python3 scripts/oasis-setup.py --features graywolf,rtl-sdr,winlink
  python3 scripts/oasis-setup.py --features kiwix --yes   # non-interactive

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
                 default=False, needs=(), internet=False, data=False, args=()):
        self.key      = key
        self.name     = name
        self.script   = script
        self.desc     = desc
        self.category = category
        self.default  = default          # pre-checked in the menu?
        self.needs    = tuple(needs)      # prerequisite feature keys
        self.internet = internet          # warn if offline
        self.data     = data              # large/optional content download
        self.args     = tuple(args)

    @property
    def path(self):
        return os.path.join(SCRIPTS_DIR, self.script)


# Order here is the run order. Software/services default-checked; data opt-in;
# boot-changing and hardware-gated steps left opt-in even though they're services.
FEATURES = [
    Feature("server", "Server (.venv + deps)", "setup-server.py",
            "Create the Python .venv and install Flask/gunicorn/psutil (offline). Foundation for the web UI and the APRS API.",
            "Core", default=True),
    Feature("autostart", "Auto-start on boot", "enable-autostart-pi.py",
            "Install the systemd unit so the OASIS server starts at boot. Add --with-browser later for a Chromium kiosk.",
            "Core", default=False, needs=["server"]),
    Feature("graywolf", "GrayWolf APRS (+ history API)", "install-graywolf.py",
            "APRS TNC/iGate/digipeater on :8080, plus the history API on :8085.",
            "Radio", default=True, needs=["server"], internet=True),
    Feature("rtl-sdr", "RTL-SDR tools", "install-rtl-sdr.py",
            "rtl_test/rtl_fm + socat/tcpdump and the DVB-driver blacklist.",
            "Radio", default=True),
    Feature("rtl-feed", "RTL-SDR → GrayWolf APRS feed", "enable-rtl-sdr.py",
            "Stream demodulated APRS audio into GrayWolf (sdr_udp). Needs the dongle plugged in.",
            "Radio", default=False, needs=["rtl-sdr"]),
    Feature("winlink", "Winlink (Pat)", "install-winlink.py",
            "Pat Winlink client + web UI on :8082 (Telnet works immediately).",
            "Radio", default=True, internet=True),
    Feature("webssh", "Web SSH (ttyd)", "install-webssh.py",
            "Browser terminal on :7681 (logs in via ssh to localhost).",
            "Connectivity", default=True),
    Feature("kiwix", "Kiwix (offline content server)", "install-kiwix.py",
            "kiwix-serve on :8081 to browse ZIM content. (Add content below.)",
            "Content", default=True, internet=True),
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


def interactive_select():
    print_menu()
    while True:
        raw = input("\n  Selection — Enter=defaults · numbers (e.g. 1,3,5) · 'all' · 'none' · 'q': ")
        keys = parse_selection(raw)
        if keys is None:
            _info("Aborted — nothing was changed.")
            sys.exit(0)
        return keys


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
    icons = {"ok": "✓", "failed": "✗", "skipped": "–"}
    for state, f in results:
        print(f"   {icons.get(state, '?')}  {f.name}")
    failed = [f for s, f in results if s == "failed"]
    print()
    if failed:
        _warn(f"{len(failed)} step(s) failed — see the output above and re-run to retry.")
    else:
        _ok("All selected steps completed.")
    _info(f"Verify everything at  http://{_guess_host()}:8083/system/setup.html")


# ── Entry point ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Interactive OASIS feature installer (delegates to install-*/enable-* scripts).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 scripts/oasis-setup.py\n"
                "  python3 scripts/oasis-setup.py --list\n"
                "  python3 scripts/oasis-setup.py --all\n"
                "  python3 scripts/oasis-setup.py --features graywolf,rtl-sdr,winlink --yes\n"),
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
              "       e.g.  python3 scripts/oasis-setup.py")

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
