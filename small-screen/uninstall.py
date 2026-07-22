#!/usr/bin/env python3
"""
small-screen/uninstall.py
-------------------------
Thoroughly remove the 7" small-screen kiosk (the `pi-small-screen-7` feature that
`scripts/enable-autostart-pi.py --7inch` installs).

Why this exists: editing `configuration/installed-services.json` does NOT undo the
kiosk. That file is only a cosmetic list the dashboard reads. The Pi keeps booting
to `/small-screen/index7.html` because the Chromium kiosk AUTOSTART — and the URL
baked into its launcher — live entirely outside that file. This script removes
every artifact the installer created:

  1. Chromium kiosk launcher      /usr/local/bin/oasis-browser-launch   (root-owned)
  2. LXDE/X11 autostart entry     ~/.config/autostart/oasis-browser.desktop
  3. labwc/Wayland autostart line ~/.config/labwc/autostart             (the launcher line)
  4. wayfire autostart line       ~/.config/wayfire.ini                 (if present)
  5. Desktop icon                 ~/Desktop/OASIS.desktop
  6. Deregisters `pi-small-screen-7` from configuration/installed-services.json
  7. (opt --remove-server)        the server autostart unit  oasis.service
  8. (opt --clear-browser-layout) Chromium's stored 7" layout override
     (localStorage `oasis_layout=7inch`), so opening `/` no longer redirects to index7

By default the OASIS *server* autostart (`oasis.service`) is KEPT — only the 7"
kiosk browser is removed — so the Pi boots to a normal desktop with the server
still running. Pass --remove-server to also stop auto-starting the server (the
same effect as `enable-autostart-pi.py --disable`, but scoped and documented).

Idempotent and safe to re-run. Needs sudo for the root-owned bits.
Raspberry Pi OS with Desktop.

Usage:
  python3 small-screen/uninstall.py
  python3 small-screen/uninstall.py --remove-server
  python3 small-screen/uninstall.py --clear-browser-layout
  python3 small-screen/uninstall.py --dry-run
"""
import argparse
import getpass
import glob
import json
import os
import pwd
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common.oasis_lib import _hr, _info, _ok, _step, _warn  # noqa: E402

SERVICE = "oasis"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"
BROWSER_BIN = "/usr/local/bin/oasis-browser-launch"
DESKTOP_ICON_NAME = "OASIS.desktop"
FEATURE_KEY = "pi-small-screen-7"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRY = False


def _sh(cmd):
    """Run a command (skipped under --dry-run). Returns the exit code (0 on dry-run)."""
    _info("$ " + " ".join(cmd))
    if DRY:
        return 0
    return subprocess.run(cmd).returncode


def _rm_file(path, sudo=False):
    """Remove a single file if present. Uses sudo for root-owned paths."""
    if not (os.path.exists(path) or os.path.islink(path)):
        _info(f"absent:  {path}")
        return False
    _ok(f"remove:  {path}")
    if DRY:
        return True
    if sudo:
        subprocess.run(["sudo", "rm", "-f", path])
    else:
        try:
            os.remove(path)
        except OSError as exc:
            _warn(f"  could not remove: {exc}")
    return True


def _strip_lines(path, needle):
    """Drop every line containing *needle* from a shell-style autostart file."""
    if not os.path.exists(path):
        _info(f"absent:  {path}")
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    kept = [ln for ln in lines if needle not in ln]
    if len(kept) == len(lines):
        _info(f"no '{needle}' line in {path}")
        return
    _ok(f"strip {len(lines) - len(kept)} line(s) from {path}")
    if DRY:
        return
    with open(path, "w") as fh:
        fh.writelines(kept)


def resolve_desktop_user():
    """(user, home) of the real desktop/login user — correct even under sudo/root.
    Mirrors scripts/enable-autostart-pi.py so we clean the same home it wrote to."""
    su = os.environ.get("SUDO_USER")
    if su and su != "root":
        try:
            pw = pwd.getpwnam(su)
            return pw.pw_name, pw.pw_dir
        except KeyError:
            pass
    if os.geteuid() != 0:
        return getpass.getuser(), os.path.expanduser("~")
    try:
        pw = pwd.getpwuid(1000)          # Pi OS first-boot user
        return pw.pw_name, pw.pw_dir
    except KeyError:
        pass
    homes = [d for d in sorted(glob.glob("/home/*")) if os.path.isdir(d)]
    if len(homes) == 1:
        try:
            pw = pwd.getpwuid(os.stat(homes[0]).st_uid)
            return pw.pw_name, pw.pw_dir
        except (KeyError, OSError):
            pass
    return getpass.getuser(), os.path.expanduser("~")


def remove_browser_autostart(home):
    _step(1, "Removing the Chromium kiosk autostart")
    _rm_file(BROWSER_BIN, sudo=True)                                              # launcher (root)
    _rm_file(os.path.join(home, ".config", "autostart", "oasis-browser.desktop"))  # LXDE/X11
    _strip_lines(os.path.join(home, ".config", "labwc", "autostart"), BROWSER_BIN)  # labwc/Wayland
    _strip_lines(os.path.join(home, ".config", "wayfire.ini"), BROWSER_BIN)         # wayfire (if used)
    _rm_file(os.path.join(home, "Desktop", DESKTOP_ICON_NAME))                     # desktop icon


def remove_server_autostart():
    _step(2, "Removing the server autostart (oasis.service)")
    if subprocess.run(["which", "systemctl"], capture_output=True).returncode != 0:
        _info("systemctl not present — skipping")
        return
    active = subprocess.run(["systemctl", "is-active", SERVICE],
                            capture_output=True, text=True).stdout.strip()
    if active != "inactive":
        _sh(["sudo", "systemctl", "disable", "--now", SERVICE])
    if _rm_file(SERVICE_FILE, sudo=True):
        _sh(["sudo", "systemctl", "daemon-reload"])


def clear_browser_layout(home):
    _step(3, 'Clearing Chromium\'s stored 7" layout (localStorage oasis_layout)')
    # Chromium keeps localStorage in a leveldb that can't be edited surgically from
    # the CLI, so remove the profile's "Local Storage" folder (localStorage only —
    # not history or cookies). Harmless on a single-purpose OASIS kiosk.
    roots = [
        os.path.join(home, ".config", "chromium"),
        os.path.join(home, ".config", "chromium-browser"),
        os.path.join(home, "snap", "chromium", "common", "chromium"),
    ]
    found = False
    for root in roots:
        for ls in glob.glob(os.path.join(root, "*", "Local Storage")):
            found = True
            _ok(f"remove:  {ls}")
            if not DRY:
                try:
                    shutil.rmtree(ls)
                except OSError as exc:
                    _warn(f"  could not remove: {exc}")
    if not found:
        _info("no Chromium Local Storage found — nothing to clear")


def deregister():
    _step(4, f"Deregistering {FEATURE_KEY} from installed-services.json")
    path = os.path.join(REPO_ROOT, "configuration", "installed-services.json")
    if not os.path.exists(path):
        _info("installed-services.json not present")
        return
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        _warn(f"could not read {path}: {exc}")
        return
    feats = data.get("features", [])
    if FEATURE_KEY not in feats:
        _info(f"{FEATURE_KEY} not listed")
        return
    _ok(f"remove {FEATURE_KEY} from features")
    if DRY:
        return
    data["features"] = [f for f in feats if f != FEATURE_KEY]
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def main():
    global DRY
    ap = argparse.ArgumentParser(description='Uninstall the 7" small-screen kiosk.')
    ap.add_argument("--remove-server", action="store_true",
                    help="also stop auto-starting the OASIS server (removes oasis.service)")
    ap.add_argument("--clear-browser-layout", action="store_true",
                    help='clear Chromium\'s stored 7" layout so opening / stops redirecting to index7')
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change, make no changes")
    args = ap.parse_args()
    DRY = args.dry_run

    _hr()
    print('  OASIS — Uninstall small-screen (7" kiosk)')
    _hr()
    if DRY:
        _warn("DRY RUN — no changes will be made")

    user, home = resolve_desktop_user()
    _info(f"desktop user: {user}   home: {home}")

    remove_browser_autostart(home)
    if args.remove_server:
        remove_server_autostart()
    else:
        _info("keeping oasis.service (server still auto-starts). Use --remove-server to remove it.")
    if args.clear_browser_layout:
        clear_browser_layout(home)
    else:
        _info("keeping browser data. If opening / still redirects to index7, re-run with --clear-browser-layout.")
    deregister()

    _hr()
    _ok("Done. Reboot to confirm the Pi no longer opens the 7\" layout.")
    _hr()


if __name__ == "__main__":
    main()
