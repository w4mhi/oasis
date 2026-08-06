#!/usr/bin/env python3
"""
RGB Cooling HAT — temperature-driven fan + status OLED for a headless Pi.

Drives the Yahboom "Raspberry Pi RGB Cooling HAT" on the 40-pin I2C bus (i2c-1):
  • Fan + RGB MCU  @ 0x0d   (write byte registers — see PROTOCOL below)
  • SSD1306 OLED   @ 0x3c   (128x32, 1-bit)

The fan is switched on/off with temperature hysteresis (this HAT's firmware is
on/off, not PWM). The OLED shows two lines — CPU% · temp · RAM, then host:ip — and
the RGB LEDs show a thermal colour by default (green→amber→red), or fan-state /
static (see RGB_MODE).

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

import argparse
import socket
import sys
import time
import signal

# NB: `smbus` and Pillow (`PIL`) are Pi-only apt packages, so they're imported
# lazily inside the functions that touch hardware. That keeps this module
# importable off-Pi — the pure logic (fan_decision, init_oled's degrade path)
# is unit-tested there.

# ── Config ───────────────────────────────────────────────────────────────────
I2C_BUS       = 1
HAT_ADDR      = 0x0d        # fan + RGB controller
OLED_ADDR     = 0x3c        # SSD1306 128x32
FAN_REG       = 0x08

# Fan hysteresis (°C). Pi 3B idles warm, so leave headroom to avoid chattering:
# turn on at FAN_ON, don't turn off again until it cools to FAN_OFF.
FAN_ON        = 55.0
FAN_OFF       = 48.0

ENABLE_RGB    = True        # drive the RGB LEDs at all
RGB_MODE      = "thermal"   # "thermal" = colour by temp (blue→amber→red);
                            # "fan" = amber while the fan runs, DEFAULT_COLOR when off;
                            # "static" = fixed STATIC_COLOR
DEFAULT_COLOR = (0, 0, 255)    # (R,G,B) when the fan is OFF (RGB_MODE="fan")
FAN_ON_COLOR  = (255, 110, 0)  # (R,G,B) when the fan is ON  (RGB_MODE="fan", amber)
STATIC_COLOR  = (0, 80, 255)   # (R,G,B) used when RGB_MODE == "static"
BRIGHTNESS    = 25          # 0–100 %, scales all RGB output (these LEDs have no
                            # brightness register — dimming = scaling R/G/B)
REFRESH_S     = 2.0         # OLED / fan update cadence

N_LEDS        = 3           # the HAT has 3 RGB LEDs (indices 0..2)
LED_ALL       = 0xff        # 0x00 register value that targets every LED at once

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
        from PIL import Image
        self.display(Image.new("1", (OLED_W, OLED_H), 0))


# ── HAT fan + RGB ─────────────────────────────────────────────────────────────
def set_fan(bus, on):
    bus.write_byte_data(HAT_ADDR, FAN_REG, 0x01 if on else 0x00)

def fan_decision(t, fan_on):
    """Desired fan state from temp with hysteresis. Forces a known state when
    fan_on is None (first pass / boot) so a fan left on at boot gets synced;
    holds the current state inside the FAN_OFF..FAN_ON dead-band."""
    if t >= FAN_ON:  return True
    if t <= FAN_OFF: return False
    return fan_on if fan_on is not None else False

def scale(rgb, pct):
    """Apply a brightness percent (0–100) to an (R,G,B) tuple."""
    f = max(0, min(100, pct)) / 100.0
    return tuple(int(round(c * f)) for c in rgb)

def set_rgb(bus, r, g, b, led=LED_ALL):
    """Static colour on the selected LED (LED_ALL = every LED). Writing the colour
    registers directly overrides the boot-default breathing effect."""
    bus.write_byte_data(HAT_ADDR, 0x00, led & 0xff); time.sleep(0.005)
    bus.write_byte_data(HAT_ADDR, 0x01, r & 0xff);   time.sleep(0.005)
    bus.write_byte_data(HAT_ADDR, 0x02, g & 0xff);   time.sleep(0.005)
    bus.write_byte_data(HAT_ADDR, 0x03, b & 0xff);   time.sleep(0.005)

def parse_hex_color(s):
    """'FF8800' / '#ff8800' / 'ff8800' -> (255, 136, 0)."""
    s = s.strip().lstrip("#")
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"colour must be 6 hex digits (RRGGBB), got '{s}'")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)

def temp_colour(t):
    if t < FAN_ON:  return (0, 0, 255)    # cool — blue
    if t < 68.0:    return (255, 110, 0)  # warm — amber
    return (255, 0, 0)                    # hot  — red


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
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet sent; just picks the route
        return s.getsockname()[0]
    except Exception:
        return "no-ip"
    finally:
        s.close()

# ── Daemon loop ───────────────────────────────────────────────────────────────
def init_oled(bus):
    """Bring up the SSD1306, or return None when the panel is absent/unwritable.

    The OLED is optional: a HAT with no display fitted (the operator pulled it
    for space) — or a wedged panel — must still get fan + RGB control, which is
    the thermally important half. The init sequence writes to 0x3c, so a missing
    panel raises OSError(EIO) here; catching it (best-effort, like every other
    I2C write in this daemon) is what keeps the fan alive instead of the service
    crash-looping."""
    try:
        return SSD1306(bus)
    except Exception as e:                     # noqa: BLE001 — bus is best-effort
        print(f"rgb-cooling-hat: OLED not responding ({e}); "
              f"running fan + RGB without the display.", file=sys.stderr)
        return None


def run_daemon():
    import smbus
    from PIL import Image, ImageDraw, ImageFont
    bus  = smbus.SMBus(I2C_BUS)
    oled = init_oled(bus)          # None when no panel is fitted — fan runs anyway
    if oled:
        try: oled.clear()
        except Exception: oled = None
    font = ImageFont.load_default()
    host = socket.gethostname()
    fan_on = None              # unknown until the first reading forces a known state
    cpu_pct()  # prime the /proc/stat baseline

    def shutdown(*_):
        # Fail-safe on exit: leave the fan running and blank the screen (if any).
        try:
            set_fan(bus, True)
            if oled: oled.clear()
        except Exception: pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        t = cpu_temp()
        desired = fan_decision(t, fan_on)
        if desired != fan_on:
            try: set_fan(bus, desired)
            except Exception: pass
            fan_on = desired

        if ENABLE_RGB:
            if RGB_MODE == "static":
                rgb = STATIC_COLOR
            elif RGB_MODE == "fan":     # amber while running, default when off
                rgb = FAN_ON_COLOR if fan_on else DEFAULT_COLOR
            else:                       # "thermal": colour by temperature
                rgb = temp_colour(t)
            try: set_rgb(bus, *scale(rgb, BRIGHTNESS))
            except Exception: pass

        if oled:
            used, total = ram_mb()
            img  = Image.new("1", (OLED_W, OLED_H), 0)
            draw = ImageDraw.Draw(img)
            # Two lines (128x32 is too cramped for four). Kept compact to fit ~21
            # default-font chars/line: line 1 = CPU% · temp · RAM, line 2 = host:ip.
            draw.text((0,  4), f"CPU {cpu_pct():>3d}% {t:>2.0f}C {used}/{total}M",
                      font=font, fill=1)
            draw.text((0, 18), f"{host}:{local_ip()}", font=font, fill=1)
            try: oled.display(img)
            except Exception: pass

        time.sleep(REFRESH_S)


# ── One-shot LED control (CLI) ────────────────────────────────────────────────
def apply_led_cli(args):
    """Set colour/brightness (or turn LEDs off) once, then exit."""
    import smbus
    led = LED_ALL if args.led == "all" else int(args.led)
    bus = smbus.SMBus(I2C_BUS)
    if args.off:
        set_rgb(bus, 0, 0, 0, led)
        print(f"LEDs ({args.led}) off.")
        return
    try:
        base = parse_hex_color(args.color)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    r, g, b = scale(base, args.brightness)
    set_rgb(bus, r, g, b, led)
    print(f"LED {args.led}: #{args.color.lstrip('#').upper()} @ {args.brightness}% "
          f"-> RGB({r},{g},{b}).")
    print("Note: if the rgb-cooling-hat service is running with ENABLE_RGB and "
          "RGB_MODE='thermal'/'fan', it will overwrite this within REFRESH_S. For "
          "a persistent manual colour set RGB_MODE='static' + STATIC_COLOR, or "
          "stop the service.")


def main():
    ap = argparse.ArgumentParser(
        description="RGB Cooling HAT fan + OLED daemon. With no arguments it runs "
                    "the service loop; with --color/--off it sets the LEDs once "
                    "and exits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 rgb-cooling-hat.py                       # run the daemon\n"
                "  python3 rgb-cooling-hat.py --color FF8800        # all LEDs orange\n"
                "  python3 rgb-cooling-hat.py --color 00FF00 --brightness 60\n"
                "  python3 rgb-cooling-hat.py --color 0000FF --led 1  # just LED 1\n"
                "  python3 rgb-cooling-hat.py --off                 # LEDs off\n"),
    )
    ap.add_argument("--color", metavar="RRGGBB",
                    help="Hex colour to set on the LEDs, then exit.")
    ap.add_argument("--brightness", type=int, default=BRIGHTNESS, metavar="0-100",
                    help=f"Brightness percent for --color (default {BRIGHTNESS}).")
    ap.add_argument("--led", default="all", choices=["all"] + [str(i) for i in range(N_LEDS)],
                    help="Which LED to set: all (default) or 0..%d." % (N_LEDS - 1))
    ap.add_argument("--off", action="store_true", help="Turn the LEDs off, then exit.")
    args = ap.parse_args()

    if args.color or args.off:
        apply_led_cli(args)
    else:
        run_daemon()


if __name__ == "__main__":
    main()
