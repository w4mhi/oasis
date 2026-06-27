#!/usr/bin/env python3
"""
enable-rtc.py — Witty Pi 3 (DS3231) hardware real-time clock
------------------------------------------------------------
Configure the UUGear **Witty Pi 3** (Rev1/Rev2) hardware RTC so the Pi keeps
accurate time across reboots and total power loss with NO network — the clock
that chrony/GPS then discipline and ride on. The Witty Pi 3's RTC is a
**DS3231SN** on the I²C bus (address 0x68), so this uses the standard Raspberry
Pi `i2c-rtc,ds3231` overlay.

What it does (idempotent · REQUIRES A REBOOT):
  1. enables I²C            (dtparam=i2c_arm=on)
  2. adds the RTC overlay   (dtoverlay=i2c-rtc,ds3231)  → /dev/rtc0 at boot
  3. removes/disables fake-hwclock  (so it can't overwrite the real RTC)
  4. neutralises the `--systz` block in /lib/udev/hwclock-set (the classic
     DS3231 fix that otherwise resets the clock at boot)

After the reboot, once the system clock is correct (from GPS/NTP), write it to
the RTC once:   sudo hwclock -w     (read it back with: sudo hwclock -r)

Usage:
  python3 scripts/enable-rtc.py
  python3 scripts/enable-rtc.py --check    # report status; change nothing

Requires: Linux (Raspberry Pi OS), sudo. A Witty Pi 3 attached on the I²C header.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run

I2C_PARAM   = "dtparam=i2c_arm=on"
OVERLAY     = "dtoverlay=i2c-rtc,ds3231"      # Witty Pi 3 = DS3231SN @ 0x68
HWCLOCK_SET = "/lib/udev/hwclock-set"


def config_path():
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.exists(p):
            return p
    return None


def _line_present(path, needle):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                s = line.strip()
                if not s.startswith("#") and (s == needle or s.startswith(needle)):
                    return True
    except OSError:
        pass
    return False


def _append_line(path, line):
    return _run(["bash", "-c", f'echo "{line}" | sudo tee -a {path} >/dev/null'],
                check=False).returncode == 0


def check_platform():
    if sys.platform != "linux":
        _fail("The Witty Pi 3 RTC is configured on Raspberry Pi OS (Linux) only.")
    if not config_path():
        _fail("No /boot/firmware/config.txt or /boot/config.txt — is this Raspberry Pi OS?")


def enable_i2c(cfg):
    if _line_present(cfg, I2C_PARAM):
        _ok("I²C already enabled.")
    elif _append_line(cfg, I2C_PARAM):
        _ok(f"Enabled I²C in {cfg}.")
    else:
        _fail(f"Could not write to {cfg}.")


def add_overlay(cfg):
    if _line_present(cfg, "dtoverlay=i2c-rtc,ds3231"):
        _ok("DS3231 RTC overlay already present.")
    elif _append_line(cfg, OVERLAY):
        _ok(f"Added '{OVERLAY}' to {cfg}.")
    else:
        _fail(f"Could not write to {cfg}.")


def disable_fake_hwclock():
    _run(["sudo", "apt", "remove", "-y", "fake-hwclock"], check=False)
    _run(["sudo", "systemctl", "disable", "fake-hwclock"], check=False)
    _run(["sudo", "update-rc.d", "-f", "fake-hwclock", "remove"], check=False)
    _ok("fake-hwclock removed/disabled (it can no longer overwrite the hardware RTC).")


def patch_hwclock_set():
    """Comment any active `--systz` line in /lib/udev/hwclock-set (the classic
    DS3231 fix). Idempotent; backs the file up once."""
    if not os.path.exists(HWCLOCK_SET):
        _info(f"{HWCLOCK_SET} not present — skipping.")
        return
    _run(["bash", "-c",
          f"test -f {HWCLOCK_SET}.oasis.bak || sudo cp {HWCLOCK_SET} {HWCLOCK_SET}.oasis.bak"],
         check=False)
    patch = (
        "import sys\n"
        "p = sys.argv[1]\n"
        "lines = open(p).read().splitlines()\n"
        "out, changed = [], False\n"
        "for ln in lines:\n"
        "    if '--systz' in ln and not ln.lstrip().startswith('#'):\n"
        "        out.append('#' + ln); changed = True\n"
        "    else:\n"
        "        out.append(ln)\n"
        "open(p, 'w').write('\\n'.join(out) + '\\n')\n"
        "print('changed' if changed else 'nochange')\n"
    )
    r = _run(["sudo", "python3", "-c", patch, HWCLOCK_SET], check=False, capture_output=True, text=True)
    if "changed" in (getattr(r, "stdout", "") or ""):
        _ok("Neutralised the --systz reset in /lib/udev/hwclock-set.")
    else:
        _ok("/lib/udev/hwclock-set already clean.")


def verify():
    if os.path.exists("/dev/rtc0"):
        r = _run(["sudo", "hwclock", "-r"], check=False, capture_output=True, text=True)
        _ok(f"/dev/rtc0 present — RTC reads: {(getattr(r, 'stdout', '') or '').strip() or '(unreadable)'}")
    else:
        _warn("/dev/rtc0 not present yet — reboot to load the DS3231 overlay.")


def run(check_only=False):
    print("\n  OASIS — enable-rtc  (Witty Pi 3 · DS3231)")
    _hr()
    check_platform()
    cfg = config_path()

    if check_only:
        _info(f"config: {cfg}")
        _info("I²C enabled : " + ("yes" if _line_present(cfg, I2C_PARAM) else "no"))
        _info("RTC overlay : " + ("yes" if _line_present(cfg, 'dtoverlay=i2c-rtc,ds3231') else "no"))
        verify()
        print()
        return

    _info("Configures the Witty Pi 3 DS3231 hardware clock for time across reboots.")
    print()
    _step(1, "Enabling I²C");                       enable_i2c(cfg)
    _step(2, "Adding the DS3231 RTC overlay");      add_overlay(cfg)
    _step(3, "Disabling fake-hwclock");             disable_fake_hwclock()
    _step(4, "Neutralising hwclock-set --systz");   patch_hwclock_set()

    _hr()
    print("\n  Witty Pi 3 RTC configured — REBOOT to load it.")
    _info("After reboot, once the clock is correct (GPS/NTP):  sudo hwclock -w")
    _info("Verify:  sudo hwclock -r   and   i2cdetect -y 1   (UU at 0x68)")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Configure the Witty Pi 3 (DS3231) hardware RTC on Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 scripts/enable-rtc.py            # configure (needs a reboot)\n"
                "  python3 scripts/enable-rtc.py --check     # report status, change nothing\n"),
    )
    ap.add_argument("--check", action="store_true",
                    help="Report RTC/overlay status; change nothing.")
    run(check_only=ap.parse_args().check)


if __name__ == "__main__":
    main()
