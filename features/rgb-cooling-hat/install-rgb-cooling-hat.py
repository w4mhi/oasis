#!/usr/bin/env python3
"""
install-rgb-cooling-hat.py
--------------------------
Install the Yahboom RGB Cooling HAT fan + status-OLED daemon as a systemd
service (Linux / Raspberry Pi only). The daemon itself lives in the repo at
rgb-cooling-hat/rgb-cooling-hat.py.

Idempotent and version-aware — safe to re-run. Steps:
  1. Enable I2C (if not already on).
  2. Install the apt dependencies (python3-pil, python3-smbus, i2c-tools).
  3. Add the service user to the 'i2c' group.
  4. Check the HAT is on the bus (i2cdetect → 0x0d fan/RGB MCU + 0x3c OLED).
  5. Install the daemon to /opt and enable the service.

Offline-first: the daemon needs no internet (it has an inlined SSD1306 driver).
The three apt deps are common base packages; on a fully offline box, install
them from your apt cache / bundled .debs before running this.

Usage:
  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py            # install + enable
  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --user pi  # service runs as 'pi'
  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --no-enable
  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --check    # report status
  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --disable  # remove service + daemon

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
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run, sudo_apt_cmd

SCRIPT_SRC   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rgb-cooling-hat.py")
INSTALL_DIR  = "/opt/rgb-cooling-hat"
SCRIPT_DEST  = os.path.join(INSTALL_DIR, "rgb-cooling-hat.py")
SERVICE_NAME = "rgb-cooling-hat.service"


def removal_record(repo_root=None):
    """Teardown record for the rgb-cooling-hat feature: the service and its
    /opt install dir (see common/removal.py). SERVICE_NAME carries the .service
    suffix; the runner adds it, so strip it here."""
    return {"services": [SERVICE_NAME.removesuffix(".service")],
            "dirs": [INSTALL_DIR]}
SERVICE_PATH = "/etc/systemd/system/" + SERVICE_NAME
APT_DEPS     = ["python3-pil", "python3-smbus", "i2c-tools"]
I2C_BUS      = 1
HAT_ADDR     = "0d"     # fan + RGB MCU
OLED_ADDR    = "3c"     # SSD1306 OLED


def target_user(explicit):
    """User the service runs as: explicit > $SUDO_USER > current user."""
    return explicit or os.environ.get("SUDO_USER") or getpass.getuser()


def _py_importable(mod):
    return subprocess.run([sys.executable, "-c", f"import {mod}"],
                          capture_output=True).returncode == 0


# ── Step 1: I2C ───────────────────────────────────────────────────────────────
def enable_i2c():
    """Enable I2C. Returns True if a reboot is now required (i2c was just enabled
    in config but /dev/i2c-N isn't live yet), else False."""
    _step(1, "Enabling I2C")
    if os.path.exists(f"/dev/i2c-{I2C_BUS}"):
        _ok(f"/dev/i2c-{I2C_BUS} present — I2C already enabled.")
        return False
    raspi = shutil.which("raspi-config")
    if not raspi:
        _warn("raspi-config not found — enable I2C manually (dtparam=i2c_arm=on "
              "in /boot/firmware/config.txt) and reboot.")
        return True
    _info("Running: sudo raspi-config nonint do_i2c 0")
    _run(["sudo", "raspi-config", "nonint", "do_i2c", "0"], check=False)
    if os.path.exists(f"/dev/i2c-{I2C_BUS}"):
        _ok("I2C enabled.")
        return False
    _warn("I2C enabled in config but /dev/i2c-1 not present yet — a reboot is required.")
    return True


# ── Step 2: Dependencies ──────────────────────────────────────────────────────
def install_deps():
    _step(2, "Installing dependencies (apt)")
    have_pil   = _py_importable("PIL")
    have_smbus = _py_importable("smbus")
    have_tools = shutil.which("i2cdetect") is not None
    if have_pil and have_smbus and have_tools:
        _ok("python3-pil, python3-smbus, i2c-tools already present.")
        return
    _info("Missing: " + ", ".join(
        n for n, ok in (("python3-pil", have_pil), ("python3-smbus", have_smbus),
                        ("i2c-tools", have_tools)) if not ok))
    _run(sudo_apt_cmd("apt", "update", "-qq"), check=False)
    if _run(sudo_apt_cmd("apt", "install", "-y", *APT_DEPS), check=False).returncode != 0:
        _warn("apt could not install the dependencies. On an offline box, install "
              "them from your apt cache first:")
        _warn("  sudo apt install -y " + " ".join(APT_DEPS))
        return
    _ok("Dependencies installed.")


# ── Step 3: I2C group ─────────────────────────────────────────────────────────
def add_i2c_group(user):
    _step(3, f"Granting '{user}' access to the I2C bus")
    r = subprocess.run(["id", "-nG", user], capture_output=True, text=True)
    if "i2c" in (r.stdout or "").split():
        _ok(f"'{user}' already in the i2c group.")
        return
    if _run(["sudo", "adduser", user, "i2c"], check=False).returncode == 0:
        _ok(f"Added '{user}' to i2c (re-login or reboot for it to take effect).")
    else:
        _warn(f"Could not add '{user}' to the i2c group — the service may hit "
              f"permission errors on /dev/i2c-{I2C_BUS}.")


# ── Step 4: HAT detection ─────────────────────────────────────────────────────
def detect_hat():
    _step(4, "Checking the HAT on I2C")
    if not shutil.which("i2cdetect"):
        _warn("i2cdetect not available — skipping bus check.")
        return
    out = subprocess.run(["sudo", "i2cdetect", "-y", str(I2C_BUS)],
                         capture_output=True, text=True).stdout or ""
    found_hat, found_oled = HAT_ADDR in out, OLED_ADDR in out

    # The fan/RGB MCU (0x0d) is the part that matters — without it the fan can't
    # be driven. The OLED (0x3c) is optional: the daemon runs fan + RGB without a
    # display fitted, so its absence is a note, not a warning.
    if found_hat:
        _ok(f"Found fan/RGB MCU (0x{HAT_ADDR}) on i2c-{I2C_BUS}.")
    else:
        _warn(f"Fan/RGB MCU (0x{HAT_ADDR}) not detected on i2c-{I2C_BUS} — the fan "
              f"won't be controllable.")
        _info("If the HAT's LEDs are lit but the MCU is invisible, a boot "
              "under-voltage can wedge it — full power-off cold boot on a 5V/2.5A+ "
              "supply, then re-run.")

    if found_oled:
        _ok(f"Found status OLED (0x{OLED_ADDR}).")
    else:
        _info(f"No OLED (0x{OLED_ADDR}) on the bus — that's fine, it's optional. "
              f"The daemon runs fan + RGB without a display.")


# ── Step 5: Daemon + service ──────────────────────────────────────────────────
def build_unit(user):
    return (
        "[Unit]\n"
        "Description=RGB Cooling HAT - fan + OLED\n"
        "After=multi-user.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"ExecStart=/usr/bin/python3 {SCRIPT_DEST}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def install_service(user, enable):
    _step(5, "Installing the daemon + service")
    if not os.path.isfile(SCRIPT_SRC):
        _fail(f"Daemon script not found: {SCRIPT_SRC}")
    if _run(["sudo", "install", "-D", "-m", "0755", SCRIPT_SRC, SCRIPT_DEST],
            check=False).returncode != 0:
        _fail(f"Could not install {SCRIPT_DEST}")
    _ok(f"Installed {SCRIPT_DEST}")

    r = subprocess.run(["sudo", "tee", SERVICE_PATH], input=build_unit(user),
                       text=True, capture_output=True)
    if r.returncode != 0:
        _fail(f"Could not write {SERVICE_PATH}: {r.stderr.strip()}")
    _ok(f"{SERVICE_NAME} written (runs as '{user}')")
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
    _step(1, "Removing the RGB Cooling HAT service")
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
    _info("Left the apt deps and i2c-group membership in place (harmless).")


def status():
    _info(f"daemon script: {'present' if os.path.isfile(SCRIPT_DEST) else 'absent'}  ({SCRIPT_DEST})")
    _info(f"service file:  {'present' if os.path.exists(SERVICE_PATH) else 'absent'}  ({SERVICE_PATH})")
    for q in ("is-active", "is-enabled"):
        r = subprocess.run(["systemctl", q, SERVICE_NAME], capture_output=True, text=True)
        _info(f"{q}: {r.stdout.strip() or 'unknown'}")
    if shutil.which("i2cdetect"):
        out = subprocess.run(["sudo", "i2cdetect", "-y", str(I2C_BUS)],
                            capture_output=True, text=True).stdout or ""
        _info(f"i2c-{I2C_BUS}: 0x{HAT_ADDR} {'detected' if HAT_ADDR in out else 'MISSING'}  ·  "
              f"0x{OLED_ADDR} {'detected' if OLED_ADDR in out else 'MISSING'}")


def run(args):
    print("\n  OASIS — install-rgb-cooling-hat")
    _hr()
    if sys.platform != "linux":
        _fail("The RGB Cooling HAT service uses systemd + I2C — Linux only.")

    if args.check:
        status()
        print()
        return
    if args.disable:
        disable()
        print()
        return

    user = target_user(args.user)
    needs_reboot = enable_i2c()
    install_deps()
    add_i2c_group(user)
    detect_hat()
    install_service(user, enable=not args.no_enable)

    _hr()
    print(f"\n  RGB Cooling HAT installed (service '{SERVICE_NAME}', user '{user}').")
    _info(f"Tune fan thresholds / RGB at the top of {SCRIPT_DEST}")
    _info("Undo with: python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --disable")
    print()
    if needs_reboot:
        # Exit 10 = "done, reboot required" (setup_registry._REBOOT_EXIT_CODE) so
        # the setup page prompts for the reboot i2c needs to bring up /dev/i2c-1.
        _warn("Reboot required — I2C was just enabled; /dev/i2c-1 appears after a reboot.")
        sys.exit(10)


def main():
    ap = argparse.ArgumentParser(
        description="Install the Yahboom RGB Cooling HAT fan + OLED daemon as a "
                    "systemd service (idempotent, Linux only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py\n"
                "  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --user pi\n"
                "  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --no-enable\n"
                "  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --check\n"
                "  python3 features/rgb-cooling-hat/install-rgb-cooling-hat.py --disable\n"),
    )
    ap.add_argument("--user", help="User the service runs as (default: $SUDO_USER or current user).")
    ap.add_argument("--no-enable", action="store_true", help="Install but don't enable/start the service.")
    ap.add_argument("--check", action="store_true", help="Report install status.")
    ap.add_argument("--disable", action="store_true", help="Stop, disable, and remove the service + daemon.")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
