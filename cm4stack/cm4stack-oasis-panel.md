# OASIS — CM4Stack Panel Reference

Hardware notes and the known-good setup for the small SPI panel on the
M5Stack CM4Stack. Use this as the starting point for a clean build.

**Status:** display up, touch bound and reporting, fan controlled. Audio amp is
present but deferred (no arm64 driver — see §7). OS is **Raspberry Pi OS Trixie
(64-bit, kernel 6.12)**; `config.txt` lives at `/boot/firmware/config.txt`.

---

## 1. Hardware

| Item | Detail |
|---|---|
| Carrier | **M5Stack CM4Stack** (M5 EdgeCM4 carrier board) |
| Compute | Raspberry Pi **CM4** |
| Display | **2.0" IPS, ST7789V2**, 240×320, SPI |
| Touch | **GT911** capacitive (I2C) — **enabled** (§4) |
| Fan | GPIO-driven, on **GPIO13** (§5) |
| Audio amp | **Awinic AW882xx** smart-PA on I2C `0x34` — **not driven** (§7) |
| OS | Raspberry Pi OS **Trixie** 64-bit, kernel 6.12; `config.txt` at `/boot/firmware/config.txt` |

Source of truth for wiring: the carrier schematic, sheet **A8 (RPI_GPIO)**.

---

## 2. Pinout (verified from schematic)

On Pi SPI0 the clock/data/CS pins are **fixed by hardware**; only DC, RST and
backlight are "free" GPIOs.

| Function | Board net | CM4 pin | **BCM GPIO** |
|---|---|---|---|
| SPI clock | LCD_SPI_SCK | 38 | GPIO11 |
| SPI data (MOSI) | LCD_SPI_MOSI | 44 | GPIO10 |
| SPI MISO | LCD_SPI_MISO | 40 | GPIO9 |
| SPI chip-select | LCD_SPI_CS | 39 | **GPIO8 (CE0 → device 0)** |
| LCD **DC** | LCD_SPI_DC | 47 | **GPIO23** |
| LCD **RESET** | LCD_SPI_RES | 41 | **GPIO25** |
| LCD **backlight** | LCD_BL_PWM | 31 | **GPIO12** |
| Touch INT | LCD_TOUCH_INT | 46 | **GPIO22** |
| Touch RST | LCD_TOUCH_RST | 45 | **GPIO24** |
| Touch I2C SCL | SCL0 | 80 | GPIO1 |
| Touch I2C SDA | SDA0 | 82 | GPIO0 |
| Fan | — | — | **GPIO13** |

Notes:
- The GT911 and every onboard I2C device (touch `0x5d`, amp `0x34`, etc.) sit on
  **BSC0** (`i2c@7e205000`). On the 6.12 kernel that controller is behind a
  pinctrl **mux**, and the bus enumerates as **`i2c-10`** (mux channel 1), *not*
  `i2c-0`. This bus number — not the raw GPIO label — is what matters when
  attaching a driver (§4).
- Touch INT = **GPIO22**, RST = **GPIO24** (confirmed: driver binds with these).

---

## 3. Display + board — known-good setup (`m5stack-cm4` overlay)

**Decision:** use the M5Stack device-tree overlay `m5stack-cm4`. It drives the
panel with the in-kernel **`sitronix,st7789v`** driver and, in the same overlay,
sets up the backlight and the GT911 touch node and board config. One overlay,
no userspace display library, no hand-built `panel.bin`.

> This supersedes the earlier hand-rolled `mipi-dbi-spi` + `panel.bin` approach.
> That path also produces a framebuffer and is a valid fallback, but the overlay
> is preferred because it bundles panel + backlight + touch + board in one unit.

### 3.1 Install the overlay

The overlay binaries come from M5Stack's repo
(`github.com/m5stack/m5stack-linux-dtoverlays`). After building/copying, the
`.dtbo` files must land in **`/boot/firmware/overlays/`** (Trixie path — the
repo's Makefile targets the old `/boot/overlays`, so copy them yourself if
needed):

```bash
ls /boot/firmware/overlays | grep -E 'm5stack|aw88'
# expect: m5stack-cm4.dtbo  aw88xx.dtbo
```

### 3.2 `/boot/firmware/config.txt`

```ini
[all]
dtoverlay=m5stack-cm4        # panel (st7789v) + backlight + GT911 node + board
dtoverlay=gt911-cm4          # touch fix — see §4 (corrects the i2c mux target)
dtoverlay=gpio-fan,gpiopin=13,temp=60000   # fan — see §5
```

- `[all]` applies the overlay unconditionally.
- **Do not** also add `dtoverlay=aw88xx` — that node has no working driver on
  arm64 and only adds boot noise (§7).

### 3.3 Run headless (required for the panel to stay on)

The desktop/X compositor reclaims the panel and blanks it (DRM/DPMS) when the
GUI loads. This box is an appliance — run it on the console:

```bash
sudo systemctl set-default multi-user.target
sudo reboot
```

(The desktop also doesn't fit a 240×320 panel anyway.)

### 3.4 Verify

```bash
dmesg | grep -i -E 'st7789|fb'      # panel driver probed, framebuffer created
ls /dev/fb*                          # a framebuffer node for the panel should exist
ls /sys/class/backlight/             # note the backlight device name (see §6)
```

---

## 4. Touch (GT911) — the i2c0 mux fix

**Symptom:** with only the `m5stack-cm4` overlay, the GT911 driver loads but its
probe dies:

```
Goodix-TS 0-005d: Error reading 1 bytes from 0x8140: -5
Goodix-TS 0-005d: I2C communication failure: -5
Goodix-TS 0-005d: probe with driver Goodix-TS failed with error -5
```

**Root cause:** the overlay attaches the `gt911@5d` node with `target = <&i2c0>`.
On the 6.12 kernel, `&i2c0` resolves to BSC0 **mux channel 0 → `i2c-0`**, but the
chip physically lives on **mux channel 1 → `i2c-10`** (where `i2cdetect -y 10`
shows `0x5d` answering). The driver was probing an empty bus, hence `-5`.

**Fix:** a small second overlay that attaches a GT911 node to the bus the chip
actually answers on. Leave `m5stack-cm4` untouched (rebuilding it from the repo
source risks the display — the repo `.dts` has drifted from the shipped binary in
the panel node).

### 4.1 Build + install the corrected overlay

This script resolves the right device-tree path on the box, builds the overlay,
installs it, and enables it (idempotent):

```bash
#!/usr/bin/env bash
set -euo pipefail

# GT911 answers on i2c-10 — resolve that bus's device-tree path
OF=$(readlink -f /sys/bus/i2c/devices/i2c-10/of_node)
DTPATH=${OF#/sys/firmware/devicetree/base}
echo "GT911 bus DT path: $DTPATH"

cat > /tmp/gt911-cm4.dts <<EOF
/dts-v1/;
/plugin/;
/ {
    compatible = "brcm,bcm2711";
    fragment@0 {
        target-path = "$DTPATH";
        __overlay__ {
            #address-cells = <1>;
            #size-cells = <0>;
            gt911@5d {
                compatible = "goodix,gt911";
                reg = <0x5d>;
                interrupt-parent = <&gpio>;
                irq-gpios = <&gpio 22 0>;     // INT = GPIO22
                reset-gpios = <&gpio 24 0>;   // RST = GPIO24
                status = "okay";
            };
        };
    };
};
EOF

dtc -@ -I dts -O dtb -o /tmp/gt911-cm4.dtbo /tmp/gt911-cm4.dts
sudo cp /tmp/gt911-cm4.dtbo /boot/firmware/overlays/

CFG=/boot/firmware/config.txt
sudo cp "$CFG" "$CFG.bak.$(date +%s)"
grep -q '^dtoverlay=gt911-cm4' "$CFG" || echo 'dtoverlay=gt911-cm4' | sudo tee -a "$CFG"
echo "Installed. Reboot to apply."
```

### 4.2 Verify (after reboot)

```bash
ls /sys/bus/i2c/devices/ | grep 005d   # want 10-005d bound
dmesg | grep -iE 'goodix|gt911'        # want: 'ID 911, version: ...' and an input device
i2cdetect -y 10                        # 5d shows as UU (driver-claimed)
sudo apt install -y evtest
sudo evtest /dev/input/eventN          # N from dmesg; touch screen → ABS_MT_POSITION_X/Y events
```

A clean bind looks like:

```
Goodix-TS 10-005d: ID 911, version: 1060
input: Goodix Capacitive TouchScreen as .../i2c-10/10-005d/input/inputN
```

### 4.3 Benign log lines (ignore)

- `supply AVDD28/VDDIO not found, using dummy regulator` — driver falls back to
  dummy regulators; touch works without them.
- `Direct firmware load for goodix_911_cfg.bin failed ... error -2` — optional
  custom-config blob; absent it, the GT911 uses the config in its own flash.
- The original `0-005d` node still probe-fails with `-5` every boot — it's the
  mis-targeted node from `m5stack-cm4`, now harmless noise. The only way to fully
  silence it is rebuilding `m5stack-cm4` from source with the panel node
  preserved by hand; not worth it.

### 4.4 Orientation trim

If touch is rotated/mirrored vs. the panel, add the matching subset of these to
the `gt911@5d` node, then rebuild the overlay (same `dtc` + copy + reboot). Find
the combination by experiment so a top-left touch reads top-left:

```dts
touchscreen-size-x = <240>;
touchscreen-size-y = <320>;
touchscreen-swapped-x-y;
touchscreen-inverted-x;
touchscreen-inverted-y;
```

---

## 5. Fan

The CM4Stack fan is on **GPIO13**. Two options, both in `/boot/firmware/config.txt`:

**Thermostatic (preferred)** — fan turns on at the threshold, off below it:

```ini
dtoverlay=gpio-fan,gpiopin=13,temp=60000   # on at 60 °C
```

**Always-on** — force GPIO13 high at boot:

```ini
gpio=13=op,dh
```

> **Not applicable on this board:** the `dtparam=fan_temp0…fan_temp3` parameters
> are for the **Raspberry Pi 5** onboard PWM fan controller, which the CM4 does
> not have. They do nothing here. Use the `gpio-fan` overlay above.

---

## 6. Backlight & "stays on" lessons

The `m5stack-cm4` overlay declares a **`pwm-backlight`** device (PWM on GPIO12),
not the older `gpio-backlight`. Confirm what you actually have and control it
through sysfs:

```bash
ls /sys/class/backlight/
BL=/sys/class/backlight/$(ls /sys/class/backlight/ | head -1)
cat "$BL"/bl_power "$BL"/max_brightness "$BL"/brightness 2>/dev/null
```

Portable lessons (independent of backlight type):

1. **Run headless** — under X the compositor reclaims the panel and blanks it
   (see §3.3). This is the single biggest "screen went dark" cause.
2. **`gpioset`/`pinctrl` can't hold GPIO12** — the backlight driver owns the line
   (`Device or resource busy`). Control brightness/power via the sysfs
   `backlight` device, not raw GPIO.
3. *If* your setup yields a `gpio-backlight` device instead, note its polarity was
   **inverted** (`bl_power=1` → ON) and it was on/off only. Re-verify under
   `pwm-backlight` before relying on that.

---

## 7. Audio amp (AW882xx) — deferred, and why

The Awinic AW882xx smart-PA is alive on the I2C bus (`0x34` on `i2c-10`), but it
is **not driven**:

- The M5Stack `aw882xx_drv.ko` shipped in the overlay repo is a **prebuilt 32-bit
  ARMv7 module built against kernel 5.15** (`vermagic 5.15.81-v7l+`). It cannot
  load on this 64-bit aarch64 / 6.12 system — `insmod` returns
  `Invalid module format`. The repo ships **no source** for it, so it can't be
  rebuilt as-is.
- The mainline kernel has in-tree Awinic ASoC drivers, but they use different
  compatible strings (`awinic,aw88395`, etc.); none matches the overlay's
  `awinic,aw882xx_smartpa`, so they won't bind to the `aw88xx` overlay either.

**To get speaker audio later:** obtain the real Awinic aw882xx driver source and
rebuild it for aarch64 against the running kernel headers
(`sudo apt install raspberrypi-kernel-headers` or `linux-headers-$(uname -r)`),
with a matching overlay. Until then, leave `dtoverlay=aw88xx` **out** of
`config.txt` to avoid a failing insmod / unbound node at every boot.

---

## 8. Architecture

- **Small panel (framebuffer)** → shows **services + clock only**. A Python script
  renders a 240×320 RGB565 image and writes it to the panel framebuffer every
  second. No display library. It also asserts the backlight on at startup.
- **Full dashboard** → served by the existing **Flask app** to any browser on the
  network (APRS table, OPS, reference, etc.).
- **One source of truth** → both the web kiosk and the panel poll the **same**
  Flask status endpoint (e.g. `/api/status`). Don't build a second status path.

`oasis-panel.py` runs as a systemd service:

```ini
# /etc/systemd/system/oasis-panel.service
[Unit]
Description=OASIS panel display
After=multi-user.target

[Service]
ExecStart=/usr/bin/python3 /home/mihaim/cm4stack/oasis-panel.py
Restart=always
RestartSec=3
User=mihaim
Group=vide 
SupplementaryGroups=video

[Install]
WantedBy=multi-user.target
```

> Confirm the framebuffer device name under the overlay (`ls /dev/fb*`) and point
> the script at the right node.

---

## 9. What did NOT work — avoid these

- **Userspace display libraries** (luma.lcd `st7789`/`st7735`, Pimoroni `st7789`)
  — wrong/locked geometry for 240×320, blank renders, or import-name mismatches.
  Dead end. Use the kernel driver via the overlay instead.
- **`gpioset`/`pinctrl` for backlight** — line owned by the backlight driver;
  they set-and-release so the light drops. Use the sysfs `backlight` device.
- **Loading the M5Stack 32-bit `aw882xx_drv.ko`** — wrong arch + wrong kernel;
  `Invalid module format` (§7).
- **Attaching `gt911` via `&i2c0`** — lands on the wrong mux channel (`i2c-0`),
  probe fails `-5`. Target the `i2c-10` node directly (§4).

**Lesson:** drive everything through the kernel + device tree; keep the
out-of-tree blobs at arm's length.

---

## 10. Open items / next session

- [ ] **Touch orientation** — run `evtest`, confirm a top-left touch reads
      top-left; if not, add the `touchscreen-*` props (§4.4) and rebuild.
- [ ] Wire touch into the panel app (or kiosk) — events land on
      `/dev/input/eventN`.
- [ ] Point `oasis-panel.py` `STATUS_URL` at the real Flask endpoint; confirm the
      JSON shape and match the parser.
- [ ] Confirm the framebuffer device name + backlight device under the overlay
      and update the app/service accordingly.
- [ ] (Optional) Audio: source-build the aw882xx driver for arm64 if speaker
      output is wanted (§7).

---

## 11. One-glance "is it alive" checklist

```bash
ls /dev/fb*                                   # panel framebuffer exists
ls /sys/bus/i2c/devices/ | grep 10-005d       # touch bound on i2c-10
i2cdetect -y 10 | grep -q ' UU ' && echo "touch: UU (bound)"
ls /sys/class/backlight/                      # backlight device present
systemctl get-default                         # multi-user.target (headless)
grep -E 'gpio-fan|gpio=13' /boot/firmware/config.txt   # fan configured
```
