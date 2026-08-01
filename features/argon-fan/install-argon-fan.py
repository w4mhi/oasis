#!/usr/bin/env python3
"""
install-argon-fan.py
--------------------
Install OASIS's Argon ONE fan daemon as a systemd service (Linux / Raspberry Pi
only). The daemon itself lives in the repo at features/argon-fan/argon-fan.py.

Why this exists: the Argon vendor daemon (argononed) monitors BCM GPIO4 for the
case's soft power button. GPIO4 is also where the Waveshare L76X GPS HAT routes
its 1PPS wire, so with argononed running, PPS pulses read as power-button presses
→ phantom reboot/shutdown. This feature drives ONLY the fan (I2C 0x1a) and never
touches GPIO4, so it must first neutralize argononed so nothing watches GPIO4 or
fights over the fan MCU.

Idempotent and version-aware — safe to re-run. Steps:
  1. Enable I2C (if not already on).
  2. Install the apt dependencies (python3-smbus, i2c-tools).
  3. Add the service user to the 'i2c' group.
  4. Neutralize the vendor argononed daemon (stop, disable, mask) if present.
  5. Check the fan MCU is on the bus (i2cdetect → 0x1a) and detect a WM8731
     (DRA-Pi) 0x1a collision.
  6. Install the daemon to /opt and enable the service — UNLESS the WM8731 codec
     shares 0x1a, in which case the service is installed DISABLED: the fan can't
     be software-controlled on a shared bus (the codec's driver claims 0x1a and
     userspace writes return EBUSY), and a boot-time write racing the codec's
     init can corrupt its TX audio path and kill Winlink RF. Override with --force
     (only after moving the codec off 0x1a — strap WM8731 CSB to 0x1b).

Offline-first: the daemon needs no internet. The two apt deps are common base
packages; on a fully offline box, install them from your apt cache first.

Usage:
  python3 features/argon-fan/install-argon-fan.py            # install + enable
  python3 features/argon-fan/install-argon-fan.py --user pi  # service runs as 'pi'
  python3 features/argon-fan/install-argon-fan.py --no-enable
  python3 features/argon-fan/install-argon-fan.py --check    # report status
  python3 features/argon-fan/install-argon-fan.py --disable  # remove + unmask argononed
  python3 features/argon-fan/install-argon-fan.py --force     # enable despite a WM8731 0x1a collision

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

SCRIPT_SRC   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "argon-fan.py")
INSTALL_DIR  = "/opt/argon-fan"
SCRIPT_DEST  = os.path.join(INSTALL_DIR, "argon-fan.py")
SERVICE_NAME = "argon-fan.service"
SERVICE_PATH = "/etc/systemd/system/" + SERVICE_NAME
VENDOR_SVC   = "argononed.service"     # the GPIO4 power-button daemon we replace
APT_DEPS     = ["python3-smbus", "i2c-tools"]
I2C_BUS      = 1
FAN_ADDR     = "1a"    # Argon fan MCU — ALSO the WM8731 (DRA-Pi) default address
CONFIG_TXT_PATHS = ("/boot/firmware/config.txt", "/boot/config.txt")


def removal_record(repo_root=None):
    """Teardown record for the argon-fan feature: the service and its /opt install
    dir (see common/removal.py). SERVICE_NAME carries the .service suffix; the
    runner adds it, so strip it here. Note: the vendor argononed daemon is left
    masked — teardown via --disable unmasks it; the factory-reset runner does not
    (masking it is the whole point of this feature)."""
    return {"services": [SERVICE_NAME.removesuffix(".service")],
            "dirs": [INSTALL_DIR],
            "notes": ["The vendor argononed daemon is left masked. To restore the "
                      "Argon power button, run: sudo systemctl unmask argononed; "
                      "or fully remove it with the Argon uninstaller "
                      "(/etc/argon/argon-uninstall.sh)."]}


def target_user(explicit):
    """User the service runs as: explicit > $SUDO_USER > current user."""
    return explicit or os.environ.get("SUDO_USER") or getpass.getuser()


def _py_importable(mod):
    return subprocess.run([sys.executable, "-c", f"import {mod}"],
                          capture_output=True).returncode == 0


def _unit_exists(name):
    """True if systemd knows a unit by this name (installed or masked)."""
    r = subprocess.run(["systemctl", "list-unit-files", name],
                       capture_output=True, text=True)
    return name in (r.stdout or "")


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
    have_smbus = _py_importable("smbus")
    have_tools = shutil.which("i2cdetect") is not None
    if have_smbus and have_tools:
        _ok("python3-smbus, i2c-tools already present.")
        return
    _info("Missing: " + ", ".join(
        n for n, ok in (("python3-smbus", have_smbus), ("i2c-tools", have_tools)) if not ok))
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


# ── Step 4: Neutralize the vendor daemon (the GPIO4 watcher) ──────────────────
def neutralize_vendor():
    _step(4, "Neutralizing the vendor argononed daemon (frees GPIO4)")
    if not _unit_exists(VENDOR_SVC):
        _ok("argononed not installed — nothing to neutralize.")
        return
    _run(["sudo", "systemctl", "disable", "--now", VENDOR_SVC], check=False)
    # Mask so nothing (a re-run of the Argon installer, a dependency) can bring
    # the GPIO4 watcher back while argon-fan owns the fan.
    _run(["sudo", "systemctl", "mask", VENDOR_SVC], check=False)
    _ok("argononed stopped, disabled, and masked.")
    _info("It's masked, not removed. To fully uninstall the vendor package: "
          "sudo /etc/argon/argon-uninstall.sh")


# ── Step 5: Fan MCU detection ─────────────────────────────────────────────────
def detect_fan():
    _step(5, "Checking the fan MCU on I2C")
    if not shutil.which("i2cdetect"):
        _warn("i2cdetect not available — skipping bus check.")
        return
    out = subprocess.run(["sudo", "i2cdetect", "-y", str(I2C_BUS)],
                         capture_output=True, text=True).stdout or ""
    if FAN_ADDR in out:
        _ok(f"Found the Argon fan MCU (0x{FAN_ADDR}) on i2c-{I2C_BUS}.")
    else:
        _warn(f"0x{FAN_ADDR} not detected on i2c-{I2C_BUS} — the fan won't respond. "
              f"Reseat the case's I2C header; a boot under-voltage can also wedge "
              f"the MCU (cold-boot on a 5V/3A+ supply). The service still installs.")
    _warn_wm8731_collision()


def _wm8731_overlay_present(config_paths=CONFIG_TXT_PATHS):
    """True if a DRA-Pi WM8731 codec overlay is in the first readable boot config.

    The WM8731 answers at I2C 0x1a — the same address as the Argon fan MCU — so
    its presence means the two collide on the bus. Pure/injectable (config_paths)
    so it's unit-testable off-Pi. Only the first readable file decides, matching
    how the boot firmware reads a single config.txt."""
    for cfg in config_paths:
        try:
            with open(cfg) as f:
                text = f.read()
        except OSError:
            continue
        return "wm8731" in text.lower()
    return False


def _warn_wm8731_collision():
    """Warn when the WM8731 codec shares 0x1a with the fan MCU (see the predicate)."""
    if _wm8731_overlay_present():
        _warn("DRA-Pi WM8731 overlay found in config.txt — the codec and the Argon "
              "fan MCU both use I2C 0x1a. The codec's driver claims 0x1a at boot, so "
              "the fan cannot be software-controlled while the DRA-Pi is seated "
              "(writes return EBUSY), and a boot-race write can corrupt the codec's "
              "TX audio. Strap the WM8731 CSB pin to 0x1b, or don't run both on one bus.")


# ── Step 6: Daemon + service ──────────────────────────────────────────────────
def enable_decision(no_enable, force, collision):
    """Whether to enable+start the service after install.

    Returns (enable, blocked_by_collision). When the WM8731 codec shares 0x1a
    (collision) we install the unit but leave it DISABLED unless --force, because
    the fan can't be controlled on a shared bus and a boot-time write can corrupt
    the codec's TX audio (breaks Winlink RF). An explicit --no-enable always wins."""
    if no_enable:
        return False, False
    if collision and not force:
        return False, True
    return True, False


def build_unit(user):
    return (
        "[Unit]\n"
        "Description=Argon ONE fan control (I2C 0x1a, GPIO4-free)\n"
        f"Conflicts={VENDOR_SVC}\n"
        # Order after the sound subsystem: if a WM8731 codec shares 0x1a (a
        # --force install), starting only once its driver has claimed the address
        # and alsa-restore has run keeps our first fan write from racing — and
        # corrupting — the codec's init. Harmless ordering on a fan-only Pi.
        "After=multi-user.target sound.target alsa-restore.service\n"
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
    _step(6, "Installing the daemon + service")
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
        _info(f"Service installed but not enabled. Enable later with: "
              f"sudo systemctl enable --now {SERVICE_NAME}")
        return
    _run(["sudo", "systemctl", "enable", "--now", SERVICE_NAME], check=False)
    time.sleep(2)
    active = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                           capture_output=True, text=True).stdout.strip()
    if active == "active":
        _ok(f"{SERVICE_NAME} is active — the fan is now under OASIS control.")
    else:
        _warn(f"{SERVICE_NAME} is '{active}'. Recent log:")
        log = subprocess.run(["journalctl", "-u", SERVICE_NAME, "-n", "12",
                             "--no-pager", "--no-hostname"],
                            capture_output=True, text=True)
        for line in (log.stdout or log.stderr).strip().splitlines():
            _info(line)


# ── Disable / status ──────────────────────────────────────────────────────────
def disable():
    _step(1, "Removing the Argon fan service")
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
    # Undo the mask so the vendor daemon can be brought back if the operator wants
    # the case's power button (accepting the GPIO4 conflict) again.
    if _unit_exists(VENDOR_SVC):
        _run(["sudo", "systemctl", "unmask", VENDOR_SVC], check=False)
        _info(f"Unmasked {VENDOR_SVC} (still disabled). Re-enable with: "
              f"sudo systemctl enable --now {VENDOR_SVC} — note this re-arms the "
              f"GPIO4 power-button watcher.")
    _info("Left the apt deps and i2c-group membership in place (harmless).")


def status():
    _info(f"daemon script: {'present' if os.path.isfile(SCRIPT_DEST) else 'absent'}  ({SCRIPT_DEST})")
    _info(f"service file:  {'present' if os.path.exists(SERVICE_PATH) else 'absent'}  ({SERVICE_PATH})")
    for q in ("is-active", "is-enabled"):
        r = subprocess.run(["systemctl", q, SERVICE_NAME], capture_output=True, text=True)
        _info(f"{q}: {r.stdout.strip() or 'unknown'}")
    va = subprocess.run(["systemctl", "is-enabled", VENDOR_SVC],
                        capture_output=True, text=True).stdout.strip()
    _info(f"vendor {VENDOR_SVC}: {va or 'not-installed'}"
          + ("  (good — masked)" if va == "masked" else ""))
    if shutil.which("i2cdetect"):
        out = subprocess.run(["sudo", "i2cdetect", "-y", str(I2C_BUS)],
                            capture_output=True, text=True).stdout or ""
        _info(f"i2c-{I2C_BUS}: 0x{FAN_ADDR} {'detected' if FAN_ADDR in out else 'MISSING'}")
    _warn_wm8731_collision()


def run(args):
    print("\n  OASIS — install-argon-fan")
    _hr()
    if sys.platform != "linux":
        _fail("The Argon fan service uses systemd + I2C — Linux only.")

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
    neutralize_vendor()
    detect_fan()
    collision = _wm8731_overlay_present()
    enable, blocked = enable_decision(args.no_enable, args.force, collision)
    if blocked:
        _warn("Refusing to auto-start the fan service: the DRA-Pi WM8731 codec owns "
              "I2C 0x1a on this Pi. A boot-time fan write can land on the codec and "
              "kill your Winlink RF TX audio, and the fan can't be controlled while "
              "the codec holds the address anyway. Installing the service DISABLED.")
        _info("The fan runs on its hardware default (on) meanwhile — thermally safe.")
        _info("To software-control the fan, move the codec off 0x1a (strap WM8731 CSB "
              "to 0x1b), then re-run. To override now: "
              "python3 features/argon-fan/install-argon-fan.py --force")
    install_service(user, enable=enable)

    _hr()
    print(f"\n  Argon fan installed (service '{SERVICE_NAME}', user '{user}').")
    _info(f"Tune the fan curve at the top of {SCRIPT_DEST}")
    _info("Undo with: python3 features/argon-fan/install-argon-fan.py --disable")
    print()
    if needs_reboot:
        # Exit 10 = "done, reboot required" (setup_registry._REBOOT_EXIT_CODE) so
        # the setup page prompts for the reboot i2c needs to bring up /dev/i2c-1.
        _warn("Reboot required — I2C was just enabled; /dev/i2c-1 appears after a reboot.")
        sys.exit(10)


def main():
    ap = argparse.ArgumentParser(
        description="Install the OASIS Argon ONE fan daemon as a systemd service "
                    "(idempotent, Linux only). Neutralizes the vendor argononed "
                    "daemon so nothing watches GPIO4.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/argon-fan/install-argon-fan.py\n"
                "  python3 features/argon-fan/install-argon-fan.py --user pi\n"
                "  python3 features/argon-fan/install-argon-fan.py --no-enable\n"
                "  python3 features/argon-fan/install-argon-fan.py --check\n"
                "  python3 features/argon-fan/install-argon-fan.py --disable\n"
                "  python3 features/argon-fan/install-argon-fan.py --force\n"),
    )
    ap.add_argument("--user", help="User the service runs as (default: $SUDO_USER or current user).")
    ap.add_argument("--no-enable", action="store_true", help="Install but don't enable/start the service.")
    ap.add_argument("--check", action="store_true", help="Report install status.")
    ap.add_argument("--disable", action="store_true", help="Stop, disable, and remove the service + daemon; unmask argononed.")
    ap.add_argument("--force", action="store_true",
                    help="Enable the service even if the DRA-Pi WM8731 codec shares I2C 0x1a "
                         "(only after moving the codec off 0x1a — strap WM8731 CSB to 0x1b).")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
