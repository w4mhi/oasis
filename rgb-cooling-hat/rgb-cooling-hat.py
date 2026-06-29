#!/usr/bin/env python3
"""
RGB Cooling HAT — temperature-driven fan + status OLED for a headless Pi.

Drives the Yahboom "Raspberry Pi RGB Cooling HAT" on the 40-pin I2C bus (i2c-1):
  • Fan + RGB MCU  @ 0x0d   (write byte registers — see PROTOCOL below)
  • SSD1306 OLED   @ 0x3c   (128x32, 1-bit)

The fan is switched on/off with temperature hysteresis (this HAT's firmware is
on/off, not PWM). The OLED shows host / CPU% / temp / RAM / IP / fan state, and
the RGB LEDs give an at-a-glance thermal colour (green→amber→red).

Offline-first, like the rest of OASIS: no pip, no venv, no display library. A
~40-line SSD1306 driver is inlined and the frame is composed with Pillow, so the
only dependencies are apt packages:

    sudo apt install -y python3-pil python3-smbus i2c-tools

Enable I2C first (`sudo raspi-config nonint do_i2c 0; sudo reboot`) and confirm
the HAT is present — `i2cdetect -y 1` must show 0d and 3c.

Run headless as a systemd service (see rgb-cooling-hat/README.md). The user it
runs as must be in the `i2c` group.

── PROTOCOL (0x0d), from Yahboom's sample code ───────────────────────────────
  reg 0x08  fan        : 0x00 off, 0x01 on
  reg 0x00  RGB select : LED index 0..2, or 0xff = all LEDs
  reg 0x01/0x02/0x03   : R / G / B for the selected LED(s) (static colour)
"""

import os
import sys
import time
import signal

from PIL import Image, ImageDraw, ImageFont
import smbus

# ── Config ───────────────────────────────────────────────────────────────────
I2C_BUS       = 1
HAT_ADDR      = 0x0d        # fan + RGB controller
OLED_ADDR     = 0x3c        # SSD1306 128x32
FAN_REG       = 0x08

# Fan hysteresis (°C). Pi 3B idles warm, so leave headroom to avoid chattering:
# turn on at FAN_ON, don't turn off again until it cools to FAN_OFF.
FAN_ON        = 55.0
FAN_OFF       = 48.0

ENABLE_RGB    = True        # temperature colour on the RGB LEDs
REFRESH_S     = 2.0         # OLED / fan update cadence

OLED_W, OLED_H = 128, 32

# ── SSD1306 (128x32, I2C) — minimal driver, no external display lib ───────────
class SSD1306:
    def __init__(self, bus, addr=OLED_ADDR):
        self.bus, self.addr = bus, addr
        # Init sequence for a 128x32 panel (charge-pump on, horizontal mode).
        for cmd in (0xAE, 0xD5, 0x80, 0xA8, 0x1F, 0xD3, 0x00, 0x40,
                    0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x02,
                    0x81, 0x8F, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6,
                    0x2E, 0xAF):
            self.bus.write_byte_data(self.addr, 0x00, cmd)

    def display(self, image):
        """Push a 128x32 mode-'1' Pillow image to the panel."""
        px = image.load()
        buf = [0] * (OLED_W * (OLED_H // 8))
        for page in range(OLED_H // 8):
            base = page * OLED_W
            for x in range(OLED_W):
                bits = 0
                for bit in range(8):
                    if px[x, page * 8 + bit]:
                        bits |= (1 << bit)
                buf[base + x] = bits
        # Address the whole panel, then stream the framebuffer in 16-byte runs.
        for cmd in (0x21, 0, OLED_W - 1, 0x22, 0, (OLED_H // 8) - 1):
            self.bus.write_byte_data(self.addr, 0x00, cmd)
        for i in range(0, len(buf), 16):
            self.bus.write_i2c_block_data(self.addr, 0x40, buf[i:i + 16])

    def clear(self):
        self.display(Image.new("1", (OLED_W, OLED_H), 0))


# ── HAT fan + RGB ─────────────────────────────────────────────────────────────
def set_fan(bus, on):
    bus.write_byte_data(HAT_ADDR, FAN_REG, 0x01 if on else 0x00)

def set_rgb(bus, r, g, b):
    """Static colour on all LEDs (overrides the boot-default breathing effect)."""
    bus.write_byte_data(HAT_ADDR, 0x00, 0xff); time.sleep(0.005)
    bus.write_byte_data(HAT_ADDR, 0x01, r & 0xff); time.sleep(0.005)
    bus.write_byte_data(HAT_ADDR, 0x02, g & 0xff); time.sleep(0.005)
    bus.write_byte_data(HAT_ADDR, 0x03, b & 0xff); time.sleep(0.005)

def temp_colour(t):
    if t < FAN_ON:  return (0, 24, 0)     # cool  — dim green
    if t < 68.0:    return (40, 16, 0)    # warm  — amber
    return (60, 0, 0)                     # hot   — red


# ── Stats (local, no subprocess, no network) ──────────────────────────────────
def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except Exception:
        return 0.0

_prev = [0, 0]
def cpu_pct():
    """Busy % from the /proc/stat delta between calls."""
    try:
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        idle, total = parts[3] + parts[4], sum(parts)
        d_idle, d_total = idle - _prev[0], total - _prev[1]
        _prev[0], _prev[1] = idle, total
        return 0 if d_total <= 0 else int(100 * (d_total - d_idle) / d_total)
    except Exception:
        return 0

def ram_mb():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k] = int(v.split()[0])  # kB
        total = info["MemTotal"] // 1024
        used = (info["MemTotal"] - info.get("MemAvailable", info["MemFree"])) // 1024
        return used, total
    except Exception:
        return 0, 0

def local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet sent; just picks the route
        return s.getsockname()[0]
    except Exception:
        return "no-ip"
    finally:
        s.close()


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    bus  = smbus.SMBus(I2C_BUS)
    oled = SSD1306(bus)
    oled.clear()
    font = ImageFont.load_default()
    host = os.uname().nodename
    fan_on = False
    cpu_pct()  # prime the /proc/stat baseline

    def shutdown(*_):
        # Fail-safe on exit: leave the fan running and blank the screen.
        try: set_fan(bus, True); oled.clear()
        except Exception: pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        t = cpu_temp()
        if not fan_on and t >= FAN_ON:
            set_fan(bus, True);  fan_on = True
        elif fan_on and t <= FAN_OFF:
            set_fan(bus, False); fan_on = False

        if ENABLE_RGB:
            try: set_rgb(bus, *temp_colour(t))
            except Exception: pass

        used, total = ram_mb()
        img  = Image.new("1", (OLED_W, OLED_H), 0)
        draw = ImageDraw.Draw(img)
        draw.text((0,  0), f"{host[:13]}  {t:>4.0f}C",          font=font, fill=1)
        draw.text((0,  8), f"CPU {cpu_pct():>3d}%  Fan {'ON ' if fan_on else 'off'}",
                  font=font, fill=1)
        draw.text((0, 16), f"RAM {used}/{total}MB",             font=font, fill=1)
        draw.text((0, 24), local_ip(),                          font=font, fill=1)
        try: oled.display(img)
        except Exception: pass

        time.sleep(REFRESH_S)


if __name__ == "__main__":
    main()
