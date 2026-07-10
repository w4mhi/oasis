# GeekPi ZP-0129 Case — fan + OLED + UPS daemon

Temperature-driven fan control, a status OLED, and UPS Plus battery monitoring
(with safe auto-shutdown) for the **GeekPi ABS Mini Tower Kit (ZP-0129)** on a
Raspberry Pi 4/5.

Offline-first: apt-only deps, no pip/venv. The SSD1306 driver is inlined in
[`geek-pi-case.py`](geek-pi-case.py); the pure decision logic lives in
[`geek_pi_case.py`](geek_pi_case.py) and is
unit-tested (`python3 tests/test_geek_pi_case.py`).

The ZP-0129's WS281x RGB fan-light / mood-light is **not** driven here (planned
as a separate feature) — that frees GPIO18 for the fan.

## Hardware

| Item | Detail |
|---|---|
| Case | GeekPi ABS Mini Tower Kit ([wiki](https://wiki.52pi.com/index.php?title=ZP-0129)) |
| Host | Raspberry Pi 4 / Pi 5 (Pi 5 uses the `lgpio` backend, not `RPi.GPIO`) |
| Fan | 2-wire, switched via **GPIO18** (BCM), on/off |
| OLED | **SSD1306** 128×64 @ I2C **`0x3c`** |
| UPS | **UPS Plus (EP-0136)** @ I2C **`0x17`** |

## Install (on the Pi) — the easy way

```bash
python3 features/geek-pi-case/install-geek-pi-case.py            # install + enable
python3 features/geek-pi-case/install-geek-pi-case.py --check    # status
python3 features/geek-pi-case/install-geek-pi-case.py --disable  # remove
```

Test in the foreground first (`Ctrl-C` to stop):

```bash
python3 /opt/geek-pi-case/geek-pi-case.py            # run the daemon
python3 /opt/geek-pi-case/geek-pi-case.py --check    # one-shot: temp, fan intent, UPS snapshot
python3 /opt/geek-pi-case/geek-pi-case.py --fan on   # manually kick the fan (testing)
```

## What the installer does (manual equivalent)

```bash
sudo raspi-config nonint do_i2c 0 && sudo reboot     # 1. enable I2C
i2cdetect -y 1                                        # 2. confirm 3c + 17
sudo apt install -y python3-pil python3-smbus i2c-tools python3-gpiozero python3-lgpio   # 3. deps
sudo adduser "$USER" i2c                              # 4. I2C bus access (re-login)
sudo adduser "$USER" gpio                             #    GPIO18 fan access (Pi 5 lgpio)
sudo install -D -m0755 geek-pi-case.py /opt/geek-pi-case/geek-pi-case.py
```

It writes `/etc/systemd/system/geek-pi-case.service` (`User=` is your login),
then `daemon-reload` + `enable --now`.

## Tuning (top of `geek-pi-case.py`)

| Constant | Default | Notes |
|---|---|---|
| `FAN_ON` / `FAN_OFF` | 55 / 48 °C | Hysteresis. Widen the gap if the fan chatters. |
| `FAN_GPIO` | 18 | BCM pin driving the fan transistor. |
| `SHUTDOWN_PCT` | 15 | On-battery capacity % that triggers a safe poweroff. |
| `SHUTDOWN_SAMPLES` | 3 | Consecutive on-battery low reads required before shutdown fires. |
| `REFRESH_S` | 2.0 s | OLED / fan / UPS update cadence. |

The OLED shows four lines: CPU% · temp · RAM, hostname, IP, and the UPS line
(`BAT nn% CHG/BATT v.vvV`). On exit (service stop) the daemon **leaves the fan
running** as a fail-safe and blanks the OLED.

## Notes / gotchas

- **Auto-shutdown** fires only after `SHUTDOWN_SAMPLES` consecutive reads that are
  both on-battery *and* at/below `SHUTDOWN_PCT` — a brief mains sag or a single
  I2C misread won't trigger it. The service issues `sudo systemctl poweroff`, so
  the service user needs passwordless sudo for that (or run the service as root).
- **Empty `i2cdetect`?** If `3c`/`17` don't show, re-seat the HAT and cold-boot on
  a solid 5 V supply, then re-run `--check`. The fan on GPIO18 is not visible to
  `i2cdetect`.
- **Pi 5:** GPIO is driven through `lgpio` (`python3-lgpio`); the old `RPi.GPIO`
  does not work on the Pi 5's RP1 I/O chip.
