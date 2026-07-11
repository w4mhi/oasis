#!/usr/bin/env python3
"""
install-geek-pi-case.py
-----------------------
Install the GeekPi ZP-0129 case OLED + UPS + WS281x-LED daemon as a systemd
service (Linux / Raspberry Pi 4/5 only). The daemon lives at
features/geek-pi-case/geek-pi-case.py.

The case fan is hardwired (no software control). The WS281x LED strip on GPIO18
needs PWM/DMA, so the service runs as root, and its PWM use conflicts with
onboard audio (this installer blacklists snd_bcm2835).

Idempotent and version-aware — safe to re-run. Steps:
  1. Enable I2C (if not already on).
  2. Install apt deps (python3-pil, python3-smbus, i2c-tools, python3-pip).
  3. Install the vendored rpi_ws281x wheel (offline, --no-index).
  4. Blacklist onboard PWM audio (snd_bcm2835) so WS281x works.
  5. Add your user to the 'i2c' group (for --check convenience).
  6. Check the devices are on the bus (i2cdetect → 0x3c OLED + 0x17 UPS).
  7. Install the daemon to /opt and enable the service (as root).

Offline-first: on a fully offline box, install the apt deps from your apt cache
/ bundled .debs first. The rpi_ws281x wheel lives in this feature's own wheels/
directory (features/geek-pi-case/wheels/) and is installed with pip --no-index
(no internet). Keeping it feature-local means removing the feature dir removes
everything it added.

Usage:
  python3 features/geek-pi-case/install-geek-pi-case.py            # install + enable
  python3 features/geek-pi-case/install-geek-pi-case.py --user pi  # service runs as 'pi'
  python3 features/geek-pi-case/install-geek-pi-case.py --no-enable
  python3 features/geek-pi-case/install-geek-pi-case.py --check    # report status
  python3 features/geek-pi-case/install-geek-pi-case.py --disable  # remove service + daemon

Requires: Linux, systemd, sudo.
"""

import argparse
import getpass
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run

SCRIPT_SRC   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geek-pi-case.py")
INSTALL_DIR  = "/opt/geek-pi-case"
SCRIPT_DEST  = os.path.join(INSTALL_DIR, "geek-pi-case.py")
MODULE_SRC   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geek_pi_case.py")
MODULE_DEST  = os.path.join(INSTALL_DIR, "geek_pi_case.py")
SERVICE_NAME = "geek-pi-case.service"
SERVICE_PATH = "/etc/systemd/system/" + SERVICE_NAME
APT_DEPS     = ["python3-pil", "python3-smbus", "i2c-tools", "python3-pip"]
WHEEL_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wheels")  # feature-local vendored wheel(s)
WS281X_PKG   = "rpi_ws281x"
BLACKLIST_PATH = "/etc/modprobe.d/snd-blacklist.conf"   # WS281x PWM vs onboard audio
I2C_BUS      = 1
OLED_ADDR    = "3c"     # SSD1306 OLED
UPS_ADDR     = "17"     # UPS Plus (EP-0136)


def target_user(explicit):
    """User the service runs as: explicit > $SUDO_USER > current user."""
    return explicit or os.environ.get("SUDO_USER") or getpass.getuser()


def _py_importable(mod):
    return subprocess.run([sys.executable, "-c", f"import {mod}"],
                          capture_output=True).returncode == 0


# ── Step 1: I2C ───────────────────────────────────────────────────────────────
def enable_i2c():
    _step(1, "Enabling I2C")
    if os.path.exists(f"/dev/i2c-{I2C_BUS}"):
        _ok(f"/dev/i2c-{I2C_BUS} present — I2C already enabled.")
        return
    raspi = shutil.which("raspi-config")
    if not raspi:
        _warn("raspi-config not found — enable I2C manually (dtparam=i2c_arm=on "
              "in /boot/firmware/config.txt) and reboot.")
        return
    _info("Running: sudo raspi-config nonint do_i2c 0")
    _run(["sudo", "raspi-config", "nonint", "do_i2c", "0"], check=False)
    if os.path.exists(f"/dev/i2c-{I2C_BUS}"):
        _ok("I2C enabled.")
    else:
        _warn("I2C enabled in config but /dev/i2c-1 not present yet — reboot, "
              "then re-run this script.")


# ── Step 2: Dependencies ──────────────────────────────────────────────────────
def install_deps():
    _step(2, "Installing dependencies (apt)")
    have_pil    = _py_importable("PIL")
    have_smbus  = _py_importable("smbus")
    have_pip    = _py_importable("pip")
    have_tools  = shutil.which("i2cdetect") is not None
    if have_pil and have_smbus and have_pip and have_tools:
        _ok("python3-pil, python3-smbus, python3-pip, i2c-tools already present.")
        return
    _info("Missing: " + ", ".join(
        n for n, ok in (("python3-pil", have_pil), ("python3-smbus", have_smbus),
                        ("python3-pip", have_pip), ("i2c-tools", have_tools)) if not ok))
    _run(["sudo", "apt", "update", "-qq"], check=False)
    if _run(["sudo", "apt", "install", "-y", *APT_DEPS], check=False).returncode != 0:
        _warn("apt could not install the dependencies. On an offline box, install "
              "them from your apt cache first:")
        _warn("  sudo apt install -y " + " ".join(APT_DEPS))
        return
    _ok("Dependencies installed.")


# ── Step 3: WS281x Python lib (vendored wheel, offline) ───────────────────────
def install_ws281x():
    _step(3, "Installing the WS281x LED library (rpi_ws281x)")
    if _py_importable(WS281X_PKG):
        _ok(f"{WS281X_PKG} already importable.")
        return
    if not os.path.isdir(WHEEL_DIR) or not any(f.endswith(".whl") for f in os.listdir(WHEEL_DIR)):
        _warn(f"No vendored wheel found in {WHEEL_DIR}.")
        _warn("Add the rpi_ws281x wheel for your Pi's arch+python there (built once), "
              "or install online: sudo pip install rpi_ws281x --break-system-packages")
        _info("The daemon still runs (OLED + UPS); LEDs stay dark until the lib is present.")
        return
    # PEP 668 (Bookworm+): system pip needs --break-system-packages. Offline via --no-index.
    cmd = ["sudo", "pip", "install", "--no-index", "--find-links", WHEEL_DIR,
           "--break-system-packages", WS281X_PKG]
    if _run(cmd, check=False).returncode != 0:
        _warn("Could not install rpi_ws281x from the vendored wheel. LEDs will be dark; "
              "OLED + UPS still work. Check the wheel matches your python/arch.")
        return
    _ok(f"{WS281X_PKG} installed from vendored wheel.")


# ── Step 4: Blacklist onboard PWM audio (conflicts with WS281x) ───────────────
def blacklist_audio():
    _step(4, "Blacklisting onboard PWM audio (snd_bcm2835)")
    line = "blacklist snd_bcm2835\n"
    try:
        existing = open(BLACKLIST_PATH).read() if os.path.exists(BLACKLIST_PATH) else ""
    except Exception:
        existing = ""
    if "snd_bcm2835" in existing:
        _ok(f"snd_bcm2835 already blacklisted ({BLACKLIST_PATH}).")
        return
    r = subprocess.run(["sudo", "tee", "-a", BLACKLIST_PATH], input=line,
                       text=True, capture_output=True)
    if r.returncode == 0:
        _ok(f"Blacklisted snd_bcm2835 in {BLACKLIST_PATH} (reboot for it to take effect).")
    else:
        _warn(f"Could not write {BLACKLIST_PATH}: {r.stderr.strip()} — WS281x may be "
              "unreliable until onboard audio (snd_bcm2835) is disabled.")


# ── Step 5: I2C group (for --check convenience; the service itself runs as root)
def add_groups(user):
    _step(5, f"Granting '{user}' access to the I2C bus")
    have = set(subprocess.run(["id", "-nG", user], capture_output=True, text=True).stdout.split())
    if "i2c" in have:
        _ok(f"'{user}' already in the i2c group.")
        return
    if _run(["sudo", "adduser", user, "i2c"], check=False).returncode == 0:
        _ok(f"Added '{user}' to i2c (re-login or reboot for it to take effect).")
    else:
        _warn(f"Could not add '{user}' to 'i2c' — --check may hit permission errors on "
              f"/dev/i2c-{I2C_BUS} (the root service is unaffected).")


# ── Step 6: Device detection ──────────────────────────────────────────────────
def detect_devices():
    _step(6, "Checking the case devices on I2C")
    if not shutil.which("i2cdetect"):
        _warn("i2cdetect not available — skipping bus check.")
        return
    out = subprocess.run(["sudo", "i2cdetect", "-y", str(I2C_BUS)],
                         capture_output=True, text=True).stdout or ""
    found_oled, found_ups = OLED_ADDR in out, UPS_ADDR in out
    if found_oled and found_ups:
        _ok(f"Found OLED (0x{OLED_ADDR}) and UPS (0x{UPS_ADDR}) on i2c-{I2C_BUS}.")
        return
    missing = ([f"0x{OLED_ADDR} (OLED)"] if not found_oled else []) \
            + ([f"0x{UPS_ADDR} (UPS)"]  if not found_ups else [])
    _warn(f"Not detected on i2c-{I2C_BUS}: " + ", ".join(missing))
    _info("The service is still installed below; re-check wiring/power and re-run "
          "--check. (The WS281x LED strip on GPIO18 is not visible to i2cdetect.)")


# ── Step 7: Daemon + service ──────────────────────────────────────────────────
def build_unit():
    # Runs as root: WS281x on GPIO18 needs PWM/DMA (/dev/mem) access.
    return (
        "[Unit]\n"
        "Description=GeekPi ZP-0129 Case - OLED + UPS + WS281x LEDs\n"
        "After=multi-user.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "User=root\n"
        f"ExecStart=/usr/bin/python3 {SCRIPT_DEST}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def install_service(enable):
    _step(7, "Installing the daemon + service")
    if not os.path.isfile(SCRIPT_SRC):
        _fail(f"Daemon script not found: {SCRIPT_SRC}")
    if not os.path.isfile(MODULE_SRC):
        _fail(f"Daemon logic module not found: {MODULE_SRC}")
    if _run(["sudo", "install", "-D", "-m", "0755", SCRIPT_SRC, SCRIPT_DEST],
            check=False).returncode != 0:
        _fail(f"Could not install {SCRIPT_DEST}")
    _ok(f"Installed {SCRIPT_DEST}")

    if _run(["sudo", "install", "-D", "-m", "0644", MODULE_SRC, MODULE_DEST],
            check=False).returncode != 0:
        _fail(f"Could not install {MODULE_DEST}")
    _ok(f"Installed {MODULE_DEST}")

    r = subprocess.run(["sudo", "tee", SERVICE_PATH], input=build_unit(),
                       text=True, capture_output=True)
    if r.returncode != 0:
        _fail(f"Could not write {SERVICE_PATH}: {r.stderr.strip()}")
    _ok(f"{SERVICE_NAME} written (runs as root)")
    _run(["sudo", "systemctl", "daemon-reload"], check=False)

    if not enable:
        _info(f"--no-enable: not starting. Later: "
              f"sudo systemctl enable --now {SERVICE_NAME}")
        return
    _run(["sudo", "systemctl", "enable", "--now", SERVICE_NAME], check=False)
    time.sleep(2)
    active = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                           capture_output=True, text=True).stdout.strip()
    if active == "active":
        _ok(f"{SERVICE_NAME} is active — the OLED should be live.")
    else:
        _warn(f"{SERVICE_NAME} is '{active}'. Recent log:")
        log = subprocess.run(["journalctl", "-u", SERVICE_NAME, "-n", "12",
                             "--no-pager", "--no-hostname"],
                            capture_output=True, text=True)
        for line in (log.stdout or log.stderr).strip().splitlines():
            _info(line)


# ── Disable / status ──────────────────────────────────────────────────────────
def disable():
    _step(1, "Removing the GeekPi case service")
    _run(["sudo", "systemctl", "disable", "--now", SERVICE_NAME], check=False)
    if os.path.exists(SERVICE_PATH):
        _run(["sudo", "rm", "-f", SERVICE_PATH], check=False)
        _run(["sudo", "systemctl", "daemon-reload"], check=False)
        _ok(f"Removed {SERVICE_PATH}")
    else:
        _ok("Service file already absent.")
    if os.path.isdir(INSTALL_DIR):
        _run(["sudo", "rm", "-rf", INSTALL_DIR], check=False)
        _ok(f"Removed {INSTALL_DIR}")
    _info("Left the apt deps and group membership in place (harmless).")


def status():
    _info(f"daemon script: {'present' if os.path.isfile(SCRIPT_DEST) else 'absent'}  ({SCRIPT_DEST})")
    _info(f"service file:  {'present' if os.path.exists(SERVICE_PATH) else 'absent'}  ({SERVICE_PATH})")
    for q in ("is-active", "is-enabled"):
        r = subprocess.run(["systemctl", q, SERVICE_NAME], capture_output=True, text=True)
        _info(f"{q}: {r.stdout.strip() or 'unknown'}")
    if shutil.which("i2cdetect"):
        out = subprocess.run(["sudo", "i2cdetect", "-y", str(I2C_BUS)],
                            capture_output=True, text=True).stdout or ""
        _info(f"i2c-{I2C_BUS}: 0x{OLED_ADDR} {'detected' if OLED_ADDR in out else 'MISSING'}  ·  "
              f"0x{UPS_ADDR} {'detected' if UPS_ADDR in out else 'MISSING'}")


def run(args):
    print("\n  OASIS — install-geek-pi-case")
    _hr()
    if sys.platform != "linux":
        _fail("The GeekPi case service uses systemd + I2C + GPIO — Linux only.")

    if args.check:
        status()
        print()
        return
    if args.disable:
        disable()
        print()
        return

    user = target_user(args.user)
    enable_i2c()
    install_deps()
    install_ws281x()
    blacklist_audio()
    add_groups(user)
    detect_devices()
    install_service(enable=not args.no_enable)

    _hr()
    print(f"\n  GeekPi ZP-0129 case installed (service '{SERVICE_NAME}', runs as root).")
    _info(f"Set LED_COUNT / thermal thresholds / UPS shutdown at the top of {SCRIPT_DEST}")
    _info("Reboot once so the snd_bcm2835 blacklist takes effect (WS281x needs the PWM).")
    _info("Undo with: python3 features/geek-pi-case/install-geek-pi-case.py --disable")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Install the GeekPi ZP-0129 case OLED + UPS + WS281x-LED daemon "
                    "as a systemd service (idempotent, Linux only; runs as root).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/geek-pi-case/install-geek-pi-case.py\n"
                "  python3 features/geek-pi-case/install-geek-pi-case.py --user pi\n"
                "  python3 features/geek-pi-case/install-geek-pi-case.py --no-enable\n"
                "  python3 features/geek-pi-case/install-geek-pi-case.py --check\n"
                "  python3 features/geek-pi-case/install-geek-pi-case.py --disable\n"),
    )
    ap.add_argument("--user", help="User the service runs as (default: $SUDO_USER or current user).")
    ap.add_argument("--no-enable", action="store_true", help="Install but don't enable/start the service.")
    ap.add_argument("--check", action="store_true", help="Report install status.")
    ap.add_argument("--disable", action="store_true", help="Stop, disable, and remove the service + daemon.")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
