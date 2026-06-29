# RGB Cooling HAT — fan + OLED daemon

Temperature-driven fan control and a status OLED for the Yahboom *Raspberry Pi
RGB Cooling HAT*, for a headless node (in use on **`pi3-aprs`**, a Pi 3B running
the RTL-SDR APRS feed).

Offline-first: apt-only deps, no pip/venv. The SSD1306 driver is inlined in
[`rgb-cooling-hat.py`](rgb-cooling-hat.py) so nothing is pulled from a CDN.

## Hardware

| Item | Detail |
|---|---|
| HAT | Yahboom RGB Cooling HAT ([repo](https://github.com/YahboomTechnology/Raspberry-Pi-RGB-Cooling-HAT)) |
| Bus | 40-pin I2C (`i2c-1`, GPIO2/3) |
| Fan + RGB MCU | I2C **`0x0d`** — fan reg `0x08` (on/off, not PWM); RGB regs `0x00`–`0x03` |
| OLED | **SSD1306** 128×32 @ I2C **`0x3c`** |

## Install (on the Pi) — the easy way

The installer does everything below (enable I2C, apt deps, i2c group, HAT
detection, daemon + service) idempotently:

```bash
python3 scripts/install-rgb-cooling-hat.py            # install + enable
python3 scripts/install-rgb-cooling-hat.py --check    # status
python3 scripts/install-rgb-cooling-hat.py --disable  # remove
```

Want to test in the foreground first (`Ctrl-C` to stop)? The OLED shows two lines
(CPU% · temp · RAM, then host:ip), the fan kicks in once CPU temp crosses
`FAN_ON`, and the LEDs show a thermal colour (green → amber → red):

```bash
python3 /opt/rgb-cooling-hat/rgb-cooling-hat.py
```

## What the installer does (manual equivalent)

```bash
sudo raspi-config nonint do_i2c 0 && sudo reboot     # 1. enable I2C
i2cdetect -y 1                                        # 2. confirm 0d + 3c
sudo apt install -y python3-pil python3-smbus i2c-tools   # 3. deps (apt only)
sudo adduser "$USER" i2c                              # 4. bus access (re-login)
sudo install -D -m0755 rgb-cooling-hat.py /opt/rgb-cooling-hat/rgb-cooling-hat.py
```

Service unit it writes to `/etc/systemd/system/rgb-cooling-hat.service` (`User=`
is your login), then `daemon-reload` + `enable --now`:

```ini
[Unit]
Description=RGB Cooling HAT - fan + OLED
After=multi-user.target

[Service]
Type=simple
User=mihaim
ExecStart=/usr/bin/python3 /opt/rgb-cooling-hat/rgb-cooling-hat.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Tuning (top of `rgb-cooling-hat.py`)

| Constant | Default | Notes |
|---|---|---|
| `FAN_ON` / `FAN_OFF` | 55 / 48 °C | Hysteresis. Widen the gap if the fan chatters. |
| `ENABLE_RGB` | `True` | Drive the RGB LEDs at all. |
| `RGB_MODE` | `"thermal"` | `"thermal"` = colour by temp (green→amber→red); `"fan"` = amber while the fan runs, `DEFAULT_COLOR` when off; `"static"` = fixed `STATIC_COLOR`. |
| `DEFAULT_COLOR` | `(0,255,0)` | `(R,G,B)` when fan is OFF (`RGB_MODE="fan"`). |
| `FAN_ON_COLOR` | `(255,110,0)` | `(R,G,B)` when fan is ON, amber (`RGB_MODE="fan"`). |
| `STATIC_COLOR` | `(0,80,255)` | `(R,G,B)` used when `RGB_MODE="static"`. |
| `BRIGHTNESS` | `25` % | Scales all RGB output (no brightness register — dimming = scaling R/G/B). |
| `REFRESH_S` | 2.0 s | OLED/fan update cadence. |

On exit (service stop) the daemon **leaves the fan running** as a fail-safe and
blanks the OLED.

## Adjusting the LEDs by hand

The same script doubles as a one-shot LED tool (no args = daemon; `--color`/`--off`
sets the LEDs once and exits):

```bash
python3 /opt/rgb-cooling-hat/rgb-cooling-hat.py --color FF8800            # all LEDs orange
python3 /opt/rgb-cooling-hat/rgb-cooling-hat.py --color 00FF00 --brightness 60
python3 /opt/rgb-cooling-hat/rgb-cooling-hat.py --color 0000FF --led 1    # just LED 1 (0..2)
python3 /opt/rgb-cooling-hat/rgb-cooling-hat.py --off
```

If the service is running with `RGB_MODE="thermal"` (or `"fan"`), it overwrites a manual colour
on the next refresh — for a colour that sticks, set `RGB_MODE="static"` +
`STATIC_COLOR` (+ `BRIGHTNESS`) and restart the service, or stop it.

## Notes / gotchas

- **Empty `i2cdetect`?** If `0d`/`3c` don't show even though the HAT's LEDs are
  lit, it's usually a boot under-voltage wedging the controller — full power-off
  cold boot on a solid 5 V/2.5 A+ supply, then re-scan. (That was the fix here.)
- **Possible OASIS-aware upgrade:** like `cm4stack/oasis-panel.py`, the OLED
  could pull `/api/system` + `/api/aprs/stations` (and the new
  `/api/health/feed-flow` pkt/s) from the local Flask app to show live APRS
  status instead of just host stats. Not wired up yet — local stats only.
