#!/usr/bin/env python3
"""
install-e-ink.py
---------------
Install the OASIS e-ink monitor on a Raspberry Pi driving a Waveshare 2.7"
e-Paper HAT (264x176 B/W, 4 buttons on GPIO 5/6/13/19).

What it does (all idempotent, safe to re-run):
  1. Prerequisites — Linux + systemd.
  2. Enable the SPI bus the panel needs (via raspi-config, else config.txt).
     If SPI was just turned on, a reboot is required before the panel works.
  3. Install Python runtime deps (Pillow, spidev, GPIO) via apt.
  4. Check the Waveshare `waveshare_epd` driver is importable; guide if not.
  5. Install + enable the oasis-e-ink.service systemd unit (runs --service).

This installer is self-contained — it deliberately does NOT import OASIS's
scripts/common helpers, so the displays/e-ink/ folder stays standalone and the
sidecar's only tie to OASIS is the HTTP API it reads at runtime.

Usage:
  python3 install-e-ink.py                # full install
  python3 install-e-ink.py --dry-run      # preview SPI/config changes
  python3 install-e-ink.py --deps-only    # SPI + apt deps, skip the service
  python3 install-e-ink.py --service-only # only (re)install the systemd service
  python3 install-e-ink.py --no-enable    # write the unit, don't start it

Exit codes: 0 = done · 10 = reboot required · 1 = error.
Requires: Linux (Raspberry Pi OS), sudo.
"""

import argparse
import getpass
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# ── Constants ────────────────────────────────────────────────────────────────

REBOOT_EXIT = 10

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "oasis-e-ink.py")


def _driver_module():
    """`waveshare_epd.<model>` from config.json (default epd2in7).

    NOTE: the 2.7" HAT's board silkscreen (e.g. "V2.1") does NOT map to the
    driver revision — a board labelled v2.x can still be a V1 panel that needs
    the `epd2in7` (not `epd2in7_V2`) driver. Trust which driver actually draws.
    """
    model = "epd2in7"
    try:
        with open(os.path.join(HERE, "config.json")) as f:
            model = json.load(f).get("display", {}).get("model") or model
    except (OSError, ValueError):
        pass
    return f"waveshare_epd.{model}"


DRIVER_MODULE = _driver_module()

SERVICE_NAME = "oasis-e-ink"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE_NAME}.service"

# Waveshare's Python driver: repo + the subpath holding the waveshare_epd package.
DRIVER_REPO = "https://github.com/waveshareteam/e-Paper"
DRIVER_SUBPATH = "RaspberryPi_JetsonNano/python/lib/waveshare_epd"

CONFIG_CANDIDATES = ("/boot/firmware/config.txt", "/boot/config.txt")

# apt packages for the runtime. The Waveshare 2.7" V2 driver drives GPIO via
# gpiozero, whose default backend on Bookworm/Trixie is lgpio — so both must be
# present (don't rely on them being preinstalled). Build lg from source only as
# a last resort, and run `sudo ldconfig` after if you do.
APT_DEPS = ["python3-pil", "python3-spidev", "python3-gpiozero", "python3-lgpio"]
# GPIO shim for reading the 4 HAT buttons; package name differs across releases
# (Trixie ships the lgpio-backed shim), so candidates are tried in order.
APT_GPIO_CANDIDATES = ["python3-rpi-lgpio", "python3-rpi.gpio"]

# ── Tiny console helpers (self-contained, no OASIS imports) ──────────────────

_C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[34m",
      "d": "\033[2m", "x": "\033[0m"}
if not sys.stdout.isatty():
    _C = {k: "" for k in _C}


def _hr():
    print("  " + "─" * 58)


def _step(n, msg):
    print(f"\n  {_C['b']}[{n}]{_C['x']} {msg}")


def _ok(msg):
    print(f"      {_C['g']}✓{_C['x']} {msg}")


def _info(msg):
    print(f"      {_C['d']}·{_C['x']} {msg}")


def _warn(msg):
    print(f"      {_C['y']}!{_C['x']} {msg}")


def _fail(msg):
    print(f"      {_C['r']}✗ {msg}{_C['x']}")
    sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────────────

def running_user():
    """Real (non-root) username even under sudo; falls back to current user."""
    for var in ("SUDO_USER", "USER", "LOGNAME"):
        u = os.environ.get(var)
        if u and u != "root":
            return u
    return getpass.getuser()


def find_config_txt():
    for p in CONFIG_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except (PermissionError, OSError):
        r = subprocess.run(["sudo", "cat", path], capture_output=True, text=True)
        if r.returncode != 0:
            _fail(f"Cannot read {path}: {r.stderr.strip()}")
        return r.stdout


def write_text(path, content, backup_suffix=".oasis-bak"):
    bak = path + backup_suffix
    if not os.path.exists(bak):
        subprocess.run(["sudo", "cp", path, bak])
    proc = subprocess.Popen(["sudo", "tee", path],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(content.encode())
    return proc.returncode == 0


def spi_enabled():
    """True when an SPI device node is present (bus is live)."""
    return bool(glob.glob("/dev/spidev*"))


# ── Steps ────────────────────────────────────────────────────────────────────

def step_prereqs():
    _step(1, "Checking prerequisites")
    if sys.platform != "linux":
        _fail("This installs a Raspberry Pi service — Linux only.")
    if not shutil.which("systemctl"):
        _fail("systemctl not found. This installer targets systemd systems.")
    if not os.path.exists(APP):
        _fail(f"App not found at {APP} — run this from the displays/e-ink/ folder.")
    _ok(f"App: {APP}")


def step_enable_spi(dry_run):
    """Enable SPI. Returns True if a reboot is required (bus just turned on)."""
    _step(2, "Enabling SPI bus")
    if spi_enabled():
        _ok("SPI already enabled (/dev/spidev* present).")
        return False

    if dry_run:
        _info("--dry-run: would enable SPI (raspi-config or dtparam=spi=on).")
        return False

    # Preferred: raspi-config non-interactive.
    if shutil.which("raspi-config"):
        r = subprocess.run(["sudo", "raspi-config", "nonint", "do_spi", "0"])
        if r.returncode == 0:
            _ok("SPI enabled via raspi-config.")
            return True
        _warn("raspi-config failed — falling back to editing config.txt.")

    # Fallback: ensure dtparam=spi=on in config.txt.
    path = find_config_txt()
    if not path:
        _fail("config.txt not found (looked in /boot/firmware and /boot). "
              "Is this a Raspberry Pi?")
    text = read_text(path)
    if re.search(r"^\s*dtparam\s*=\s*spi\s*=\s*on", text, re.MULTILINE):
        _ok("dtparam=spi=on already in config.txt (reboot pending).")
        return True
    new_text = text.rstrip("\n") + "\n\n# Enabled by OASIS install-e-ink.py\ndtparam=spi=on\n"
    if not write_text(path, new_text):
        _fail("Could not write config.txt to enable SPI.")
    _ok(f"Added dtparam=spi=on to {path}.")
    return True


def _apt_install(pkg):
    r = subprocess.run(["dpkg-query", "-W", "-f=${Status}", pkg],
                       capture_output=True, text=True)
    if "install ok installed" in r.stdout:
        _ok(f"{pkg}: already installed")
        return True
    _info(f"{pkg}: installing via apt...")
    r = subprocess.run(["sudo", "apt-get", "install", "-y", pkg])
    if r.returncode == 0:
        _ok(f"{pkg}: installed.")
        return True
    return False


def step_deps(dry_run):
    _step(3, "Installing Python runtime dependencies")
    if dry_run:
        _info(f"--dry-run: would apt-install {', '.join(APT_DEPS)} + a GPIO package.")
        return
    for pkg in APT_DEPS:
        if not _apt_install(pkg):
            _warn(f"{pkg}: apt install failed — install manually: "
                  f"sudo apt-get install -y {pkg}")
    # GPIO: first candidate that installs wins.
    if not any(_apt_install(p) for p in APT_GPIO_CANDIDATES):
        _warn("No GPIO package installed — buttons won't work. Try: "
              f"sudo apt-get install -y {APT_GPIO_CANDIDATES[0]}")


def _driver_imports():
    """True if `import waveshare_epd.epd2in7_V2` works with systemd's view.

    Probes with the same interpreter and cwd systemd uses (sys.path[0] is the
    app dir), so a waveshare_epd/ folder vendored into displays/e-ink/ resolves.
    """
    r = subprocess.run([sys.executable, "-c", f"import {DRIVER_MODULE}"],
                       cwd=HERE, capture_output=True, text=True)
    return r.returncode == 0


def _clone_driver(tmp):
    """Clone just the driver into tmp/repo; return the clone dir, or None.

    Prefers a partial + sparse checkout (small download — the full e-Paper repo
    is hundreds of MB of example art), falling back to a plain shallow clone for
    older gits that lack --filter/--sparse.
    """
    dest = os.path.join(tmp, "repo")

    def _has_subpath():
        return os.path.isdir(os.path.join(dest, DRIVER_SUBPATH))

    # Minimal: partial + sparse.
    r = subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
         DRIVER_REPO, dest],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        sc = subprocess.run(["git", "-C", dest, "sparse-checkout", "set",
                             DRIVER_SUBPATH], capture_output=True, text=True)
        if sc.returncode == 0 and _has_subpath():
            return dest

    # Fallback: plain shallow clone.
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    r = subprocess.run(["git", "clone", "--depth", "1", DRIVER_REPO, dest],
                       capture_output=True, text=True)
    if r.returncode == 0 and _has_subpath():
        return dest

    if r.returncode != 0:
        _warn(f"git clone failed: {r.stderr.strip().splitlines()[-1] if r.stderr else 'unknown error'}")
    return None


def fetch_driver():
    """Fetch Waveshare's driver from GitHub into displays/e-ink/waveshare_epd."""
    if not shutil.which("git"):
        _warn("git not found — install it, or vendor the driver manually.")
        return False
    _info(f"Fetching driver from {DRIVER_REPO} (shallow/sparse)...")
    with tempfile.TemporaryDirectory() as tmp:
        clone = _clone_driver(tmp)
        if not clone:
            _warn("Could not fetch the driver (no network, or git error).")
            return False
        src = os.path.join(clone, DRIVER_SUBPATH)
        dst = os.path.join(HERE, "waveshare_epd")
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    _ok(f"Driver installed → {dst}")
    return True


def _driver_manual_guidance():
    _info("Vendor Waveshare's Python driver so `import waveshare_epd` resolves:")
    _info(f"  git clone {DRIVER_REPO}")
    _info(f"  cp -r e-Paper/{DRIVER_SUBPATH} \\")
    _info(f"        {HERE}/")


def step_driver(no_fetch):
    _step(4, "Checking Waveshare panel driver")
    if _driver_imports():
        _ok(f"{DRIVER_MODULE} imports OK.")
        return

    vendored = os.path.join(HERE, "waveshare_epd")
    if os.path.isdir(vendored):
        _warn(f"{vendored} is present but not importable — likely the GPIO/spidev "
              "deps aren't in yet. It should work once step 3 finishes / on reboot.")
        return

    if no_fetch:
        _warn("Driver not present and --no-fetch given.")
        _driver_manual_guidance()
        return

    if fetch_driver():
        if _driver_imports():
            _ok(f"{DRIVER_MODULE} imports OK.")
        else:
            _info("Driver files installed; import still failing (GPIO/spidev deps "
                  "or SPI not live yet) — it will render once those are ready.")
        return

    _warn("The service will still install; it just won't render until the driver "
          "is present.")
    _driver_manual_guidance()


def step_check_station():
    _step(5, "Checking shared station identity")
    path = os.path.normpath(os.path.join(HERE, "..", "station.json"))
    if os.path.exists(path):
        _ok(f"Using {path} for callsign/grid/lat/lon.")
    else:
        _info(f"{path} not found — falling back to the station block in config.json.")


def step_install_service(user, no_enable):
    _step(6, "Installing oasis-e-ink systemd service")
    unit = f"""\
[Unit]
Description=OASIS e-ink monitor (Waveshare 2.7" panel)
After=multi-user.target network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {APP} --service
WorkingDirectory={HERE}
Restart=always
RestartSec=5
User={user}
SupplementaryGroups=gpio spi
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""
    _info(f"Writing {SERVICE_FILE} ...")
    proc = subprocess.Popen(["sudo", "tee", SERVICE_FILE],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(unit.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {SERVICE_FILE}.")
    _ok(f"Unit written: {SERVICE_FILE}")

    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)

    if no_enable:
        _info("--no-enable: unit written but not started.")
        _info(f"  Start later: sudo systemctl enable --now {SERVICE_NAME}")
        return

    r = subprocess.run(["sudo", "systemctl", "enable", "--now", SERVICE_NAME],
                       capture_output=True, text=True)
    if r.returncode == 0:
        _ok(f"{SERVICE_NAME} enabled and started.")
    else:
        _warn(f"systemctl enable --now failed:\n{r.stderr.strip()}")
        _info(f"  Try manually: sudo systemctl enable --now {SERVICE_NAME}")


def print_health_check():
    _hr()
    print("  Health check:")
    print()
    for label, cmd in (
        ("SPI device present", "ls /dev/spidev*"),
        ("Driver imports", f"python3 -c 'import {DRIVER_MODULE}'"),
        ("Service status", f"systemctl status {SERVICE_NAME}"),
        ("Live logs", f"journalctl -u {SERVICE_NAME} -f"),
    ):
        print(f"  # {label}")
        print(f"    {cmd}")
        print()
    _hr()


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Install the OASIS e-ink monitor service on a Raspberry Pi.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview SPI/config changes without writing.")
    ap.add_argument("--deps-only", action="store_true",
                    help="Only SPI + apt deps; skip the service.")
    ap.add_argument("--service-only", action="store_true",
                    help="Only (re)install the systemd service.")
    ap.add_argument("--no-enable", action="store_true",
                    help="Write the service unit but do not start it.")
    ap.add_argument("--no-fetch", action="store_true",
                    help="Don't fetch the Waveshare driver from GitHub if missing.")
    args = ap.parse_args()

    print()
    print("  OASIS — install-e-ink")
    _hr()

    if sys.platform != "linux":
        _fail("This installs a Raspberry Pi service — Linux only.")
    if args.deps_only and args.service_only:
        _fail("--deps-only and --service-only are mutually exclusive.")

    if args.service_only:
        step_prereqs()
        step_install_service(running_user(), args.no_enable)
        print()
        print_health_check()
        return 0

    step_prereqs()
    reboot = step_enable_spi(args.dry_run)
    step_deps(args.dry_run)
    step_driver(no_fetch=args.no_fetch or args.dry_run)
    step_check_station()

    if args.deps_only:
        print()
        _ok("Deps stage complete (--deps-only).")
        return REBOOT_EXIT if reboot else 0

    if args.dry_run:
        print()
        _info("Dry run complete — re-run without --dry-run to apply.")
        return 0

    step_install_service(running_user(), args.no_enable)
    print()
    print_health_check()

    if reboot:
        _warn("Reboot required — SPI was just enabled; the panel appears after reboot.")
        return REBOOT_EXIT
    _ok("OASIS e-ink monitor installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
