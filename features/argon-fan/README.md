# Argon ONE fan control (GPIO4-free)

Temperature-driven fan control for an **Argon ONE** case (v2 / v3 / M.2) that
drives **only** the fan and never touches **GPIO4** — so it coexists with a
stacked GPS / radio HAT that the vendor daemon breaks.

## Why not just use the vendor `argononed`?

The Argon vendor daemon does two jobs:

1. **Fan control** over I²C (`0x1a`) — harmless.
2. **Power-button monitoring on BCM GPIO4** — the problem.

GPIO4 is also where the **Waveshare L76X GPS HAT** routes its **1PPS** wire (see
`features/gps-L76X` — `dtoverlay=pps-gpio,gpiopin=4`). With `argononed` running,
one PPS pulse per second reads as a stream of power-button presses, so the Pi
**reboots and then shuts down** the moment the GPS/DRA HAT is seated.

This feature replaces the fan half and drops the GPIO4 half entirely.

## What the installer does

`install-argon-fan.py` is idempotent and version-aware (safe to re-run):

1. Enables I²C.
2. Installs `python3-smbus`, `i2c-tools` (from your apt cache on an offline box).
3. Adds the service user to the `i2c` group.
4. **Neutralizes `argononed`** — stop, disable, **mask** (so nothing re-arms the
   GPIO4 watcher). It is *masked, not removed*; to fully uninstall the vendor
   package run `sudo /etc/argon/argon-uninstall.sh`.
5. Checks the fan MCU is on the bus (`i2cdetect -y 1` → `1a`) and warns on a
   WM8731 (DRA-Pi) `0x1a` collision.
6. Installs the daemon to `/opt/argon-fan` and enables the `argon-fan.service`.

```bash
python3 features/argon-fan/install-argon-fan.py            # install + enable
python3 features/argon-fan/install-argon-fan.py --check    # status
python3 features/argon-fan/install-argon-fan.py --disable  # remove + unmask argononed
```

## The fan protocol

The Argon fan MCU at I²C `0x1a` takes a **single byte = speed percent (0–100)** —
no register, no command byte:

```bash
sudo i2cset -y 1 0x1a 100    # full
sudo i2cset -y 1 0x1a 0      # off
```

The daemon (`argon-fan.py`) polls `/sys/class/thermal/thermal_zone0/temp` and
applies a proportional curve with downward hysteresis (default `55°C→30%`,
`60°C→55%`, `65°C→100%`, mirroring the Argon defaults). Tune the curve at the top
of the script. On exit it fails the fan to **100%** so a crash can't cook the Pi.

## ⚠ I²C `0x1a` collision with the DRA-Pi

The Wolfson **WM8731** codec on the MastersComm **DRA-Pi** *also* defaults to
`0x1a`. If both are on one bus, fan writes and the codec fight for the address.
Strap the WM8731 **CSB** pin to move it to `0x1b`, or don't run both on one bus.
`--check` warns when the WM8731 overlay is present in `config.txt`.

## Requirements

Linux, systemd, sudo, I²C enabled, an Argon ONE case on `i2c-1`.
