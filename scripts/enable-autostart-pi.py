#!/usr/bin/env python3
"""
enable-autostart-pi.py
----------------------
Configure OASIS to start automatically when the Raspberry Pi boots.

  Default (no flags)
    Installs a systemd service (oasis.service) that runs start.sh on boot.
    The OASIS web interface will be available at http://localhost:8083.
    No browser is launched — suitable for headless or multi-user setups.

  --with-browser
    Also installs an LXDE desktop autostart entry that opens Chromium in
    kiosk mode (http://localhost:8083) after the server is ready.
    Requires Raspberry Pi OS with Desktop (a live desktop session).

  --desktop-icon
    Create a clickable OASIS shortcut on the Raspberry Pi desktop
    (~Desktop/OASIS.desktop).  Requires Raspberry Pi OS with Desktop
    (the ~/Desktop folder must exist).  Double-click the icon to open
    the OASIS web interface in the default browser.

  --disable
    Remove the systemd service and (if present) the browser autostart
    entry and the desktop icon.  No OASIS files are deleted.

Usage:
  python3 scripts/enable-autostart-pi.py
  python3 scripts/enable-autostart-pi.py --with-browser
  python3 scripts/enable-autostart-pi.py --desktop-icon
  python3 scripts/enable-autostart-pi.py --disable

Requires: Raspberry Pi OS (Debian/Linux), systemd, sudo.
"""

import argparse
import getpass
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run,
)

SERVICE      = "oasis"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"
BROWSER_BIN  = "/usr/local/bin/oasis-browser-launch"
REPO_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START_SH     = os.path.join(REPO_ROOT, "start.sh")
PORT         = 8083
DESKTOP_ICON_NAME = "OASIS.desktop"


def _sudo_write(path, content):
    """Write *content* to a root-owned *path* via sudo tee."""
    proc = subprocess.run(
        ["sudo", "tee", path],
        input=content, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        _fail(f"Could not write {path}: {proc.stderr.strip()}")


# ── Step 1: Platform check ─────────────────────────────────────────────────────
def check_platform():
    _step(1, "Checking platform")
    if sys.platform != "linux":
        _fail("This script targets Raspberry Pi OS (Linux). "
              "It cannot run on macOS or Windows.")
    if _run(["which", "systemctl"], check=False, capture_output=True).returncode != 0:
        _fail("systemd not found. This script requires a systemd-based OS.")
    if not os.path.isfile(START_SH):
        _fail(f"start.sh not found at:\n     {START_SH}\n"
              "     Run this script from inside the OASIS repo.")
    _ok(f"Platform: Linux / systemd")
    _ok(f"OASIS root: {REPO_ROOT}")


# ── Step 2: systemd service ────────────────────────────────────────────────────
def install_service(user):
    _step(2, "Installing OASIS systemd service")

    unit = (
        "[Unit]\n"
        "Description=OASIS — Off-grid Amateur Station Information Suite\n"
        "After=network.target\n"
        "Wants=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={REPO_ROOT}\n"
        f"ExecStart=/bin/bash {START_SH}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    _info(f"Writing {SERVICE_FILE}")
    _sudo_write(SERVICE_FILE, unit)
    _ok(f"Wrote {SERVICE_FILE}")

    _run(["sudo", "systemctl", "daemon-reload"])
    _ok("systemctl daemon-reload")
    _run(["sudo", "systemctl", "enable", "--now", SERVICE])
    _ok(f"systemctl enable --now {SERVICE}")

    result = _run(
        ["sudo", "systemctl", "is-active", SERVICE],
        check=False, capture_output=True, text=True,
    )
    status = result.stdout.strip()
    if status == "active":
        _ok("Service is active")
    else:
        _warn(f"Service status: {status}")
        _info(f"Check logs with:  journalctl -u {SERVICE} -f")


# ── Step 3: Chromium kiosk autostart ──────────────────────────────────────────
def install_browser(home):
    _step(3, "Installing Chromium kiosk autostart")

    # Detect the Chromium binary name (Pi OS ships either variant).
    chromium_bin = None
    for candidate in ("chromium-browser", "chromium"):
        if _run(["which", candidate], check=False, capture_output=True).returncode == 0:
            chromium_bin = candidate
            break
    if chromium_bin is None:
        _warn("Chromium not found — install it with:")
        _info("  sudo apt install -y chromium-browser")
        _warn("The autostart entry will be written anyway and will work once Chromium is installed.")
        chromium_bin = "chromium-browser"
    else:
        _ok(f"Chromium binary: {chromium_bin}")

    # Launcher script — waits for the OASIS server, then opens Chromium kiosk.
    launcher = (
        "#!/bin/bash\n"
        "# Wait for OASIS server to be ready, then open Chromium in kiosk mode.\n"
        f"URL=\"http://localhost:{PORT}\"\n"
        "for i in $(seq 1 30); do\n"
        "    curl -sf \"$URL\" > /dev/null 2>&1 && break\n"
        "    sleep 2\n"
        "done\n"
        f"exec {chromium_bin} --kiosk --noerrdialogs --disable-infobars \"$URL\"\n"
    )
    _info(f"Writing {BROWSER_BIN}")
    _sudo_write(BROWSER_BIN, launcher)
    _run(["sudo", "chmod", "+x", BROWSER_BIN])
    _ok(f"Wrote {BROWSER_BIN}  (executable)")

    # ~/.config/autostart/ .desktop entry — picked up by LXDE / Pi OS Desktop.
    autostart_dir = os.path.join(home, ".config", "autostart")
    os.makedirs(autostart_dir, exist_ok=True)
    desktop_file = os.path.join(autostart_dir, "oasis-browser.desktop")
    desktop = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=OASIS Browser\n"
        "Comment=Open OASIS in Chromium kiosk mode on desktop login\n"
        f"Exec={BROWSER_BIN}\n"
        "Hidden=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    with open(desktop_file, "w") as f:
        f.write(desktop)
    _ok(f"Wrote {desktop_file}")
    _info("Chromium will open in kiosk mode on next desktop login")
    _info("To exit kiosk at any time: Alt+F4  or  Ctrl+Alt+T")


# ── Step 4: Desktop icon ──────────────────────────────────────────────────────
def install_desktop_icon(home):
    _step(4, "Creating desktop shortcut")

    desktop_dir = os.path.join(home, "Desktop")
    if not os.path.isdir(desktop_dir):
        _warn("~/Desktop not found — Raspberry Pi OS with Desktop is required.")
        _info("Install the desktop environment with:")
        _info("  sudo apt install -y raspberrypi-ui-mods lxde-core")
        _warn("Desktop icon NOT created.")
        return

    icon_path = os.path.join(desktop_dir, DESKTOP_ICON_NAME)
    desktop = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=OASIS\n"
        "Comment=Off-grid Amateur Station Information Suite\n"
        f"Exec=xdg-open http://localhost:{PORT}\n"
        "Icon=web-browser\n"
        "Terminal=false\n"
        "Categories=HamRadio;Network;\n"
    )
    with open(icon_path, "w") as f:
        f.write(desktop)
    os.chmod(icon_path, 0o755)   # LXDE requires executable bit to trust the shortcut
    _ok(f"Wrote {icon_path}")
    _info("Double-click the OASIS icon on the desktop to open the web interface")
    _info(f"URL: http://localhost:{PORT}")


# ── Disable ────────────────────────────────────────────────────────────────────
def cmd_disable(home):
    print()
    print("  OASIS — Removing autostart")
    _hr()

    _step(1, "Disabling systemd service")
    active = _run(
        ["sudo", "systemctl", "is-active", SERVICE],
        check=False, capture_output=True, text=True,
    ).stdout.strip()
    if active != "inactive":
        _run(["sudo", "systemctl", "disable", "--now", SERVICE], check=False)
        _ok(f"systemctl disable --now {SERVICE}")
    if os.path.exists(SERVICE_FILE):
        _run(["sudo", "rm", SERVICE_FILE])
        _ok(f"Removed {SERVICE_FILE}")
        _run(["sudo", "systemctl", "daemon-reload"])
        _ok("systemctl daemon-reload")
    else:
        _info("Service file not found — already removed")

    _step(2, "Removing browser autostart")
    removed_any = False
    if os.path.exists(BROWSER_BIN):
        _run(["sudo", "rm", BROWSER_BIN])
        _ok(f"Removed {BROWSER_BIN}")
        removed_any = True
    desktop_file = os.path.join(home, ".config", "autostart", "oasis-browser.desktop")
    if os.path.exists(desktop_file):
        os.remove(desktop_file)
        _ok(f"Removed {desktop_file}")
        removed_any = True
    if not removed_any:
        _info("Browser autostart not found — nothing to remove")

    _step(3, "Removing desktop icon")
    icon_path = os.path.join(home, "Desktop", DESKTOP_ICON_NAME)
    if os.path.exists(icon_path):
        os.remove(icon_path)
        _ok(f"Removed {icon_path}")
    else:
        _info("Desktop icon not found — nothing to remove")

    print()
    print("  OASIS autostart removed.")
    _hr()
    print()


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Configure OASIS to start automatically on Raspberry Pi boot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/enable-autostart-pi.py                 # server only\n"
            "  python3 scripts/enable-autostart-pi.py --with-browser  # server + Chromium kiosk\n"
            "  python3 scripts/enable-autostart-pi.py --desktop-icon  # add desktop shortcut\n"
            "  python3 scripts/enable-autostart-pi.py --disable       # remove all\n"
        ),
    )
    parser.add_argument(
        "--with-browser", action="store_true",
        help=(
            "Also open Chromium in kiosk mode on desktop login. "
            "Requires Raspberry Pi OS with Desktop. "
            "Chromium waits for the server to be ready before launching."
        ),
    )
    parser.add_argument(
        "--desktop-icon", action="store_true",
        help=(
            "Create a clickable OASIS shortcut on ~/Desktop. "
            "Requires Raspberry Pi OS with Desktop (~/Desktop must exist). "
            "Double-click the icon to open OASIS in the default browser."
        ),
    )
    parser.add_argument(
        "--disable", action="store_true",
        help="Remove the OASIS systemd service, browser autostart, and desktop icon (if present).",
    )
    args = parser.parse_args()

    user = getpass.getuser()
    home = os.path.expanduser("~")

    if args.disable:
        cmd_disable(home)
        return

    print()
    print("  OASIS — Enable autostart on Raspberry Pi")
    _hr()
    _info(f"User: {user}")
    if args.with_browser:
        _info("Mode: server  +  Chromium kiosk on desktop login")
    elif args.desktop_icon:
        _info("Mode: server  +  desktop shortcut icon")
    else:
        _info("Mode: server only (no browser)")
        _info("Tip:  Re-run with --with-browser or --desktop-icon to add browser access later")

    check_platform()
    install_service(user)
    if args.with_browser:
        install_browser(home)
    if args.desktop_icon:
        install_desktop_icon(home)

    print()
    print("  OASIS — Autostart configured.")
    _hr()
    _info(f"Web interface : http://localhost:{PORT}")
    _info(f"Logs          : journalctl -u {SERVICE} -f")
    _info(f"Status        : systemctl status {SERVICE}")
    if args.with_browser:
        _info("Browser       : opens in kiosk mode on next desktop login")
    if args.desktop_icon:
        _info("Desktop icon  : ~/Desktop/OASIS.desktop (double-click to open)")
    print()


if __name__ == "__main__":
    main()
