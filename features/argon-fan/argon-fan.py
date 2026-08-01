#!/usr/bin/env python3
"""
Argon ONE fan control — temperature-driven, GPIO4-free.

Drives the fan on an Argon ONE case (v2/v3/M.2) over the 40-pin I2C bus (i2c-1)
WITHOUT the vendor's argononed daemon. That daemon does two jobs: fan control
(harmless) and power-button monitoring on BCM GPIO4 (the problem). GPIO4 is also
where the Waveshare L76X GPS HAT routes its 1PPS wire (see features/gps-L76X),
so with the vendor daemon running, one PPS pulse per second reads as a stream of
power-button presses → phantom reboot/shutdown. This daemon drives ONLY the fan
and never touches GPIO4, so the two can coexist.

── PROTOCOL (Argon fan MCU @ 0x1a) ───────────────────────────────────────────
  A single byte written to I2C address 0x1a IS the fan speed, as a percent 0–100
  (0 = off, 100 = full). No register, no command byte — a bare SMBus write_byte.
  That's the whole protocol; it's all the vendor daemon does for the fan.

    sudo i2cset -y 1 0x1a 100    # full   ·   sudo i2cset -y 1 0x1a 0   # off

Offline-first, like the rest of OASIS: no pip, no venv. The only dependency is
the apt package python3-smbus (+ i2c-tools for the CLI checks).

Enable I2C first (`sudo raspi-config nonint do_i2c 0; sudo reboot`) and confirm
the fan MCU is present — `i2cdetect -y 1` must show 1a. Run headless as a systemd
service (see features/argon-fan/README.md).

CAVEAT — I2C 0x1a is also the default address of the Wolfson WM8731 codec on the
MastersComm DRA-Pi. If that HAT is stacked, the fan MCU and the codec collide on
0x1a: once the codec's driver claims the address at boot, fan writes here return
EBUSY (harmless but the fan is uncontrolled), and a fan write that races the
codec's init can corrupt its TX audio and break Winlink RF. Strap the WM8731 CSB
pin to move it to 0x1b, or don't run both on one bus. Because of this, the
installer leaves this service DISABLED when the WM8731 overlay is present unless
run with --force.
"""

import argparse
import sys
import time
import signal

# NB: `smbus` is a Pi-only apt package, so it's imported lazily inside the two
# functions that actually open the bus (run_daemon / the --set path). That keeps
# this module importable off-Pi — the pure fan_percent() logic is unit-tested.

# ── Config ───────────────────────────────────────────────────────────────────
I2C_BUS   = 1
FAN_ADDR  = 0x1a          # Argon fan MCU (bare write_byte = speed percent)

# Fan curve: (CPU °C threshold, fan %), highest first. Mirrors the Argon default
# config (55=30, 60=55, 65=100) — the fan is proportional, not just on/off.
FAN_CURVE   = [(65.0, 100), (60.0, 55), (55.0, 30)]
HYSTERESIS  = 2.0         # °C: once a band's speed is set, hold it until the temp
                          # falls this far below the band's threshold — stops the
                          # fan chattering at a boundary.
REFRESH_S   = 5.0         # temp poll / fan update cadence


def fan_percent(temp, current):
    """Desired fan % for *temp*, given the *current* commanded % (for hysteresis).

    Ramps up the instant a threshold is crossed; on the way down, holds the
    current band's speed until the temp drops HYSTERESIS below its threshold, so
    a temperature hovering on a boundary doesn't flip the fan every poll."""
    for thr, spd in FAN_CURVE:                 # highest threshold first
        # If we're already running at least this fast, make the band "sticky" by
        # lowering its effective threshold — that's the downward hysteresis.
        eff = thr - HYSTERESIS if current >= spd else thr
        if temp >= eff:
            return spd
    return 0


# ── Stats (local, no subprocess, no network) ──────────────────────────────────
def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except Exception:
        return 0.0


def set_fan(bus, percent):
    """Write the fan speed percent (0–100) to the MCU — a bare SMBus byte."""
    bus.write_byte(FAN_ADDR, max(0, min(100, int(percent))))


# ── Daemon loop ───────────────────────────────────────────────────────────────
def run_daemon():
    import smbus
    bus = smbus.SMBus(I2C_BUS)
    current = None            # unknown until the first reading forces a write

    def shutdown(*_):
        # Fail-safe on exit: leave the fan at full so a crash can't cook the Pi.
        try: set_fan(bus, 100)
        except Exception: pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        t = cpu_temp()
        desired = fan_percent(t, current if current is not None else 0)
        if desired != current:
            try: set_fan(bus, desired)
            except Exception: pass
            current = desired
        time.sleep(REFRESH_S)


def main():
    ap = argparse.ArgumentParser(
        description="Argon ONE fan control (I2C 0x1a), GPIO4-free. With no "
                    "arguments it runs the service loop; --set writes one speed "
                    "and exits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 argon-fan.py               # run the daemon\n"
                "  python3 argon-fan.py --set 100     # fan full, then exit\n"
                "  python3 argon-fan.py --set 0       # fan off, then exit\n"),
    )
    ap.add_argument("--set", type=int, metavar="0-100",
                    help="Write this fan speed percent once, then exit.")
    args = ap.parse_args()

    if args.set is not None:
        import smbus
        bus = smbus.SMBus(I2C_BUS)
        set_fan(bus, args.set)
        print(f"Argon fan set to {max(0, min(100, args.set))}%.")
    else:
        run_daemon()


if __name__ == "__main__":
    main()
