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
| Host | Raspberry Pi 4 / Pi 5 (service runs as **root** — WS281x needs PWM/DMA) |
| LEDs | **WS281x** strip on **GPIO18** (PWM0), colour by CPU temp |
| OLED | **SSD1306** 128×64 @ I2C **`0x3c`** |
| UPS | **UPS Plus (EP-0136)** @ I2C **`0x17`** |
| Fan | Hardwired (always-on) — **no software control** |

## Install (on the Pi) — the easy way

```bash
python3 features/geek-pi-case/install-geek-pi-case.py            # install + enable
python3 features/geek-pi-case/install-geek-pi-case.py --check    # status
python3 features/geek-pi-case/install-geek-pi-case.py --disable  # remove
```

Test in the foreground first (`Ctrl-C` to stop):

```bash
sudo python3 /opt/geek-pi-case/geek-pi-case.py            # run the daemon (root for the LEDs)
python3 /opt/geek-pi-case/geek-pi-case.py --check         # one-shot: temp, LED colour, UPS snapshot
sudo python3 /opt/geek-pi-case/geek-pi-case.py --color 00FF00   # all LEDs green (test)
sudo python3 /opt/geek-pi-case/geek-pi-case.py --off      # LEDs off
```

## What the installer does (manual equivalent)

```bash
sudo raspi-config nonint do_i2c 0 && sudo reboot     # 1. enable I2C
i2cdetect -y 1                                        # 2. confirm 3c + 17
sudo apt install -y python3-pil python3-smbus i2c-tools python3-pip   # 3. apt deps
sudo pip install --no-index --find-links features/geek-pi-case/wheels --break-system-packages rpi_ws281x  # WS281x lib (feature-local wheel)
echo "blacklist snd_bcm2835" | sudo tee -a /etc/modprobe.d/snd-blacklist.conf   # 4. free PWM for WS281x (reboot)
sudo install -D -m0755 geek-pi-case.py /opt/geek-pi-case/geek-pi-case.py
sudo install -D -m0644 geek_pi_case.py /opt/geek-pi-case/geek_pi_case.py
```

It writes `/etc/systemd/system/geek-pi-case.service` (`User=root` — WS281x needs
PWM/DMA), then `daemon-reload` + `enable --now`.

## Tuning (top of `geek-pi-case.py`)

| Constant | Default | Notes |
|---|---|---|
| `LED_COUNT` | 8 | Number of WS281x LEDs on the strip — **set to your real count**. |
| `LED_BRIGHTNESS` | 64 | 0–255 master brightness. |
| `COOL_MAX` / `WARM_MAX` | 55 / 68 °C | Thermal bands (in `geek_pi_case.py`): below cool → cool colour, below warm → amber, above → red. |
| `SHUTDOWN_PCT` | 15 | On-battery capacity % that triggers a safe poweroff. |
| `SHUTDOWN_SAMPLES` | 3 | Consecutive on-battery low reads required before shutdown fires. |
| `REFRESH_S` | 2.0 s | OLED / LED / UPS update cadence. |

The OLED shows host stats, IP, and a UPS battery line; the LED strip shows a
thermal colour (cool → amber → red) from the CPU temperature. On exit (service
stop) the daemon blanks the OLED and turns the LEDs off. The fan is hardwired
and always runs.

## Notes / gotchas

- **Auto-shutdown** fires only after `SHUTDOWN_SAMPLES` consecutive reads that are
  both on-battery *and* at/below `SHUTDOWN_PCT` — a brief mains sag or a single
  I2C misread won't trigger it. The service issues `sudo systemctl poweroff`, so
  the service user needs passwordless sudo for that (or run the service as root).
- **LEDs need root + a reboot.** WS281x on GPIO18 uses PWM/DMA (root) and conflicts
  with onboard audio — the installer blacklists `snd_bcm2835`; **reboot once** after
  install. If the strip stays dark, check `rpi_ws281x` imported (`--check`) and that
  the reboot applied the blacklist. `--check`/`--color`/`--off` need `sudo`.
- **Empty `i2cdetect`?** If `3c`/`17` don't show, re-seat the HAT and cold-boot on
  a solid 5 V supply, then re-run `--check`. The WS281x strip on GPIO18 is not
  visible to `i2cdetect`.
- **Offline install:** the `rpi_ws281x` wheel lives in this feature's own
  [`wheels/`](wheels/) directory and installs with `pip --no-index` — no internet.
  Build/obtain the wheel once for your Pi's arch + Python (`pip download rpi_ws281x
  -d features/geek-pi-case/wheels/` on an online Pi of the same OS) before building
  the offline bundle. Feature-local means deleting `features/geek-pi-case/` removes
  everything the feature added — nothing orphaned in a shared `offline-packages/`.
- **RGB is now in this daemon**, not a separate feature — the earlier "RGB follow-up"
  note is superseded: WS281x thermal LEDs live here.
