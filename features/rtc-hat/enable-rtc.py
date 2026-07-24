#!/usr/bin/env python3
"""
enable-rtc.py — hardware real-time clock (i2c-rtc overlay)
----------------------------------------------------------
Configure a battery-backed I²C RTC so the Pi keeps accurate time across reboots
and total power loss with NO network — the clock that chrony/GPS then discipline
and ride on. Two boards are supported via --board:

  wittypi          UUGear Witty Pi 3 (Rev1/Rev2) — DS3231SN @ 0x68 on the GPIO
                   ARM I²C bus (i2c-1). This is the default.
                   → dtparam=i2c_arm=on + dtoverlay=i2c-rtc,ds3231

  bigtreetech-7in  BigTreeTech 7" touchscreen — PCF8563 @ 0x51 on the DSI
                   ribbon's I²C bus (i2c-10 / i2c_csi_dsi), NOT the GPIO header,
                   which is why it never shows on `i2cdetect -y 1`.
                   → dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi

What it does (idempotent · REQUIRES A REBOOT):
  1. enables I²C on the GPIO bus  (dtparam=i2c_arm=on) — ARM-bus boards only;
     DSI-bus boards get their bus from the overlay's i2c_csi_dsi flag
  2. adds the RTC overlay         (dtoverlay=i2c-rtc,<chip>[,i2c_csi_dsi])  → /dev/rtc0 at boot
  3. removes/disables fake-hwclock  (so it can't overwrite the real RTC)
  4. neutralises the `--systz` block in /lib/udev/hwclock-set (the classic
     i2c-rtc fix that otherwise resets the clock at boot)
  5. ensures the `hwclock` tool is installed — Debian 13 (Trixie) moved it out of
     `util-linux` into `util-linux-extra`, so `hwclock -w/-r` are otherwise
     “command not found” on a minimal Pi OS image

After the reboot, once the system clock is correct (from GPS/NTP), write it to
the RTC once:   sudo hwclock -w     (read it back with: sudo hwclock -r)

Usage:
  python3 features/rtc-hat/enable-rtc.py                          # Witty Pi 3 (DS3231)
  python3 features/rtc-hat/enable-rtc.py --board bigtreetech-7in  # BTT 7" (PCF8563)
  python3 features/rtc-hat/enable-rtc.py --check                  # report status; change nothing

Requires: Linux (Raspberry Pi OS), sudo. The RTC attached on its I²C bus.
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run

I2C_PARAM   = "dtparam=i2c_arm=on"
HWCLOCK_SET = "/lib/udev/hwclock-set"


def removal_record(repo_root=None):
    """Teardown record for the rtc feature: strip the DS3231 overlay line and
    restore /lib/udev/hwclock-set from the .oasis.bak the installer made. Leaves
    dtparam=i2c_arm=on (shared with other I2C users). Reboot to drop the overlay."""
    return {"config_lines": ["dtoverlay=i2c-rtc,ds3231"],
            "restore": [[HWCLOCK_SET + ".oasis.bak", HWCLOCK_SET]],
            "requires_reboot": True}

# Per-board RTC facts. `i2c_arm` is True when the chip sits on the GPIO ARM bus
# (i2c-1) and so needs dtparam=i2c_arm=on; False when it hangs off the DSI
# ribbon's bus (i2c-10), where the overlay's own i2c_csi_dsi flag brings the bus
# up and enabling the GPIO bus would be pointless. `bus`/`addr` are only used for
# the verify hint (`i2cdetect -y <bus>` shows the chip as UU at <addr>).
BOARDS = {
    "wittypi": {
        "label":   "Witty Pi 3 · DS3231",
        "chip":    "ds3231",
        "addr":    "0x68",
        "bus":     1,
        "overlay": "dtoverlay=i2c-rtc,ds3231",
        "i2c_arm": True,
    },
    "bigtreetech-7in": {
        "label":   'BigTreeTech 7" touchscreen · PCF8563',
        "chip":    "pcf8563",
        "addr":    "0x51",
        "bus":     10,
        "overlay": "dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi",
        "i2c_arm": False,
    },
}
DEFAULT_BOARD = "wittypi"


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
        _fail("The hardware RTC is configured on Raspberry Pi OS (Linux) only.")
    if not config_path():
        _fail("No /boot/firmware/config.txt or /boot/config.txt — is this Raspberry Pi OS?")


def enable_i2c(cfg, board):
    # DSI-bus boards (i2c_csi_dsi) don't use the GPIO ARM I²C bus at all — the
    # overlay's i2c_csi_dsi flag stands up i2c-10 itself, so adding i2c_arm=on
    # would just clutter config.txt with an unused bus.
    if not board["i2c_arm"]:
        _info("RTC is on the DSI i2c_csi_dsi bus — the GPIO ARM I²C bus (i2c_arm) is not needed.")
        return
    if _line_present(cfg, I2C_PARAM):
        _ok("I²C already enabled.")
    elif _append_line(cfg, I2C_PARAM):
        _ok(f"Enabled I²C in {cfg}.")
    else:
        _fail(f"Could not write to {cfg}.")


def add_overlay(cfg, board):
    overlay = board["overlay"]
    if _line_present(cfg, overlay):
        _ok(f"{board['chip'].upper()} RTC overlay already present.")
    elif _append_line(cfg, overlay):
        _ok(f"Added '{overlay}' to {cfg}.")
    else:
        _fail(f"Could not write to {cfg}.")


def disable_fake_hwclock():
    _run(["sudo", "apt", "remove", "-y", "fake-hwclock"], check=False)
    _run(["sudo", "systemctl", "disable", "fake-hwclock"], check=False)
    _run(["sudo", "update-rc.d", "-f", "fake-hwclock", "remove"], check=False)
    _ok("fake-hwclock removed/disabled (it can no longer overwrite the hardware RTC).")


def patch_hwclock_set():
    """Comment any active `--systz` line in /lib/udev/hwclock-set (the classic
    i2c-rtc fix). Idempotent; backs the file up once."""
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


def _hwclock_path():
    """Locate the hwclock binary. Debian 13 (Trixie) moved it into the
    `util-linux-extra` package, and it lives in /usr/sbin (not always on the
    Python process's PATH), so probe the standard sbin locations too."""
    return (shutil.which("hwclock")
            or next((p for p in ("/usr/sbin/hwclock", "/sbin/hwclock")
                     if os.path.exists(p)), None))


def ensure_hwclock():
    """Guarantee the `hwclock` tool exists — the RTC is seeded with `hwclock -w`
    and read back with `hwclock -r`. On Debian Trixie it split out of util-linux
    into `util-linux-extra`, which a minimal Pi OS image omits, so the classic
    commands fail 'command not found'. Best-effort apt install (needs internet,
    like the fake-hwclock removal step); warns with guidance if still missing."""
    if _hwclock_path():
        _ok("hwclock present.")
        return
    _info("hwclock not found — installing util-linux-extra (Trixie split it out) …")
    _run(["sudo", "apt", "install", "-y", "util-linux-extra"], check=False)
    if _hwclock_path():
        _ok("Installed util-linux-extra — hwclock is now available.")
    else:
        _warn("hwclock still missing. When online, run:  "
              "sudo apt update && sudo apt install util-linux-extra   "
              "(the RTC read/write tool lives there on Debian Trixie). "
              "Meanwhile the RTC still reads via sysfs — see below.")


def verify(board):
    if not os.path.exists("/dev/rtc0"):
        _warn(f"/dev/rtc0 not present yet — reboot to load the {board['chip'].upper()} overlay.")
        return
    hw = _hwclock_path()
    if hw:
        r = _run(["sudo", hw, "-r"], check=False, capture_output=True, text=True)
        _ok(f"/dev/rtc0 present — RTC reads: {(getattr(r, 'stdout', '') or '').strip() or '(unreadable)'}")
        return
    # hwclock absent (Trixie util-linux-extra not installed) — read the RTC via
    # the kernel's sysfs interface instead, which needs no extra package.
    try:
        with open("/sys/class/rtc/rtc0/date") as fd, open("/sys/class/rtc/rtc0/time") as ft:
            _ok(f"/dev/rtc0 present — RTC reads (UTC, via sysfs): {fd.read().strip()} {ft.read().strip()}")
    except OSError:
        _ok("/dev/rtc0 present.")
    _warn("hwclock not installed — install it to seed/read the RTC:  "
          "sudo apt install util-linux-extra")


def run(check_only=False, board_id=DEFAULT_BOARD):
    board = BOARDS[board_id]
    print(f"\n  OASIS — enable-rtc  ({board['label']})")
    _hr()
    check_platform()
    cfg = config_path()

    if check_only:
        _info(f"board  : {board_id}  ({board['label']})")
        _info(f"config : {cfg}")
        if board["i2c_arm"]:
            _info("I²C (arm)   : " + ("yes" if _line_present(cfg, I2C_PARAM) else "no"))
        _info("RTC overlay : " + ("yes" if _line_present(cfg, board["overlay"]) else "no"))
        verify(board)
        print()
        return

    _info(f"Configures the {board['label']} hardware clock for time across reboots.")
    print()
    _step(1, "Enabling I²C");                            enable_i2c(cfg, board)
    _step(2, f"Adding the {board['chip'].upper()} RTC overlay");  add_overlay(cfg, board)
    _step(3, "Disabling fake-hwclock");                  disable_fake_hwclock()
    _step(4, "Neutralising hwclock-set --systz");        patch_hwclock_set()
    _step(5, "Ensuring hwclock is installed");           ensure_hwclock()

    _hr()
    print(f"\n  {board['label']} RTC configured — REBOOT to load it.")
    _info("After reboot, once the clock is correct (GPS/NTP):  sudo hwclock -w")
    _info(f"Verify:  sudo hwclock -r   and   i2cdetect -y {board['bus']}   "
          f"(the chip shows as UU at {board['addr']})")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Configure a hardware RTC (i2c-rtc overlay) on Raspberry Pi OS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 features/rtc-hat/enable-rtc.py                          # Witty Pi 3 (DS3231)\n"
                "  python3 features/rtc-hat/enable-rtc.py --board bigtreetech-7in  # BTT 7\" (PCF8563)\n"
                "  python3 features/rtc-hat/enable-rtc.py --check                  # report status, change nothing\n"),
    )
    ap.add_argument("--board", choices=sorted(BOARDS), default=DEFAULT_BOARD,
                    help=f"RTC board preset (default: {DEFAULT_BOARD}).")
    ap.add_argument("--check", action="store_true",
                    help="Report RTC/overlay status; change nothing.")
    args = ap.parse_args()
    run(check_only=args.check, board_id=args.board)


if __name__ == "__main__":
    main()
