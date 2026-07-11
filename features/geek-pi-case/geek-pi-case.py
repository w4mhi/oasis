#!/usr/bin/env python3
"""
GeekPi ZP-0129 Case — status OLED + UPS Plus monitor + WS281x thermal LEDs.

Drives the GeekPi ABS Mini Tower Kit (ZP-0129) on a Raspberry Pi 4/5:
  • WS281x strip @ GPIO18 (PWM0) — colour by CPU temp (green→amber→red)
  • SSD1306 OLED @ I2C 0x3c   (128x64, 1-bit)
  • UPS Plus     @ I2C 0x17   (EP-0136) — battery %, voltage, input source

The case fan is hardwired (always-on), so there is no fan control — the LED
strip is the temperature indicator. The OLED shows CPU/temp/RAM, host, IP, and a
UPS battery line. When on battery at/below SHUTDOWN_PCT for SHUTDOWN_SAMPLES
consecutive reads, a safe `systemctl poweroff` is issued.

WS281x on GPIO18 uses PWM/DMA and needs root — the systemd unit runs as root.
It conflicts with onboard PWM audio (blacklist snd_bcm2835; the installer does
this). Offline-first: apt deps + a vendored rpi_ws281x wheel (installed
--no-index), inlined SSD1306 driver, no CDN. Hardware libs (smbus, PIL,
rpi_ws281x) are imported lazily so `--help` works off-Pi.

Pure decision logic lives in geek_pi_case.py (same directory, unit-tested);
this file is the hardware-I/O shell.
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geek_pi_case as L

# ── Config (tune here) ────────────────────────────────────────────────────────
I2C_BUS          = 1
OLED_ADDR        = 0x3c
UPS_ADDR         = L.UPS_ADDR          # 0x17
SHUTDOWN_PCT     = 15                 # on-battery capacity % that triggers poweroff
SHUTDOWN_SAMPLES = 3                  # consecutive low reads required first
REFRESH_S        = 2.0                # OLED / LED / UPS cadence

# ── WS281x LED strip (GPIO18 / PWM0) ──────────────────────────────────────────
LED_COUNT        = 8                   # number of LEDs on the strip — SET to your real count
LED_PIN          = 18                  # BCM GPIO (PWM0) — the ZP-0129 strip data line
LED_FREQ         = 800000              # Hz (WS281x standard)
LED_DMA          = 10                  # DMA channel
LED_CHANNEL      = 0                   # PWM channel (0 for GPIO18)
LED_INVERT       = False               # True only with an inverting level shifter
LED_BRIGHTNESS   = 64                  # 0–255 master brightness

OLED_W, OLED_H = 128, 64


# ── SSD1306 (128x64, I2C) — minimal driver, no external display lib ───────────
class SSD1306:
    def __init__(self, bus, addr=OLED_ADDR):
        self.bus, self.addr = bus, addr
        # 128x64 init: MUX 0x3F, COM pins 0x12 (vs 0x1F/0x02 on a 128x32 panel).
        for cmd in (0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
                    0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12,
                    0x81, 0x8F, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6,
                    0x2E, 0xAF):
            self.bus.write_byte_data(self.addr, 0x00, cmd)

    def display(self, image):
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
        for cmd in (0x21, 0, OLED_W - 1, 0x22, 0, (OLED_H // 8) - 1):
            self.bus.write_byte_data(self.addr, 0x00, cmd)
        for i in range(0, len(buf), 16):
            self.bus.write_i2c_block_data(self.addr, 0x40, buf[i:i + 16])

    def clear(self):
        from PIL import Image
        self.display(Image.new("1", (OLED_W, OLED_H), 0))


# ── Local stats (no subprocess, no network) ───────────────────────────────────
def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return L.parse_cpu_temp(f.read())
    except Exception:
        return 0.0

_prev = [0, 0]
def cpu_pct():
    try:
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        idle, total = parts[3] + parts[4], sum(parts)
        d_idle, d_total = idle - _prev[0], total - _prev[1]
        _prev[0], _prev[1] = idle, total
        return 0 if d_total <= 0 else int(100 * (d_total - d_idle) / d_total)
    except Exception:
        return 0

def ram_pct():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k] = int(v.split()[0])  # kB
        total = info["MemTotal"]
        avail = info.get("MemAvailable", info["MemFree"])
        return int(100 * (total - avail) / total) if total else 0
    except Exception:
        return 0

def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet sent; just picks the route
        return s.getsockname()[0]
    except Exception:
        return "no-ip"
    finally:
        s.close()


# ── WS281x LED strip (GPIO18 via rpi_ws281x — PWM/DMA, needs root) ────────────
def make_strip():
    from rpi_ws281x import PixelStrip
    strip = PixelStrip(LED_COUNT, LED_PIN, LED_FREQ, LED_DMA, LED_INVERT,
                       LED_BRIGHTNESS, LED_CHANNEL)
    strip.begin()
    return strip

def set_strip(strip, rgb):
    """Paint every LED the same (R, G, B)."""
    from rpi_ws281x import Color
    c = Color(rgb[0], rgb[1], rgb[2])
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, c)
    strip.show()

def parse_hex_color(s):
    """'FF8800' / '#ff8800' -> (255, 136, 0)."""
    s = s.strip().lstrip("#")
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"colour must be 6 hex digits (RRGGBB), got '{s}'")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


# ── UPS Plus (I2C 0x17) ───────────────────────────────────────────────────────
def read_ups(bus):
    """Return dict(capacity, batt_mv, temp_c, on_batt) or None on I2C failure."""
    try:
        cap     = bus.read_word_data(UPS_ADDR, L.REG_CAPACITY)
        batt    = bus.read_word_data(UPS_ADDR, L.REG_VBATT)
        usbc    = bus.read_word_data(UPS_ADDR, L.REG_VUSBC)
        micro   = bus.read_word_data(UPS_ADDR, L.REG_VMICRO)
        temp    = L.decode_temp(bus.read_word_data(UPS_ADDR, L.REG_TEMP))
        return {
            "capacity": cap,
            "batt_mv":  batt,
            "temp_c":   temp,
            "on_batt":  L.on_battery(usbc, micro),
        }
    except Exception:
        return None


# ── Daemon loop ───────────────────────────────────────────────────────────────
def run_daemon():
    import smbus
    from PIL import Image, ImageDraw, ImageFont

    bus  = smbus.SMBus(I2C_BUS)
    oled = SSD1306(bus)
    oled.clear()
    font = ImageFont.load_default()
    host = socket.gethostname()
    try:
        strip = make_strip()
    except Exception as e:
        strip = None
        print(f"[geek-pi-case] LED strip unavailable ({e}); running OLED+UPS only",
              file=sys.stderr)
    guard = L.ShutdownGuard(pct=SHUTDOWN_PCT, samples=SHUTDOWN_SAMPLES)
    cpu_pct()  # prime the /proc/stat baseline

    def shutdown(*_):
        try:
            if strip is not None:
                set_strip(strip, (0, 0, 0))   # blank the LEDs on exit
            oled.clear()
        except Exception:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        t = cpu_temp()
        if strip is not None:
            try:
                set_strip(strip, L.temp_colour(t))
            except Exception:
                pass

        ups = read_ups(bus)

        img = Image.new("1", (OLED_W, OLED_H), 0)
        d = ImageDraw.Draw(img)
        d.text((0, 0),  L.format_stats_line(cpu_pct(), t, ram_pct()), font=font, fill=1)
        d.text((0, 16), f"HOST: {host[:20]}", font=font, fill=1)
        d.text((0, 32), f"IP: {local_ip()}", font=font, fill=1)
        if ups is not None:
            d.text((0, 48), L.format_ups_line(ups["capacity"], ups["batt_mv"], ups["on_batt"]),
                   font=font, fill=1)
            if guard.update(ups["on_batt"], ups["capacity"]):
                d.text((0, 48), "LOW BATT — SHUTDOWN", font=font, fill=1)
                oled.display(img)
                subprocess.run(["sudo", "systemctl", "poweroff"], check=False)
        else:
            d.text((0, 48), "UPS: --", font=font, fill=1)
        try:
            oled.display(img)
        except Exception:
            pass

        time.sleep(REFRESH_S)


# ── One-shot CLI ──────────────────────────────────────────────────────────────
def one_shot_check():
    import smbus
    bus = smbus.SMBus(I2C_BUS)
    t = cpu_temp()
    r, g, b = L.temp_colour(t)
    print(f"cpu temp : {t:.1f} C   LED colour: #{r:02X}{g:02X}{b:02X}")
    ups = read_ups(bus)
    if ups is None:
        print("ups      : not readable on I2C 0x17")
    else:
        print(f"ups      : {L.format_ups_line(ups['capacity'], ups['batt_mv'], ups['on_batt'])}  "
              f"({ups['temp_c']} C, {'battery' if ups['on_batt'] else 'mains'})")

def one_shot_led(spec):
    strip = make_strip()
    rgb = (0, 0, 0) if spec == "off" else parse_hex_color(spec)
    set_strip(strip, rgb)
    print(f"led → {'off' if spec == 'off' else '#' + spec.lstrip('#')}")
    time.sleep(0.2)   # let the DMA transfer land before the process exits


def main():
    ap = argparse.ArgumentParser(
        description="GeekPi ZP-0129 case daemon (OLED + UPS + WS281x thermal LEDs). "
                    "No args = run the daemon.",
    )
    ap.add_argument("--check", action="store_true",
                    help="Print CPU temp, LED colour, and UPS snapshot, then exit.")
    ap.add_argument("--color", metavar="RRGGBB",
                    help="Set all LEDs to a hex colour and exit (testing; needs root).")
    ap.add_argument("--off", action="store_true",
                    help="Turn the LED strip off and exit (needs root).")
    args = ap.parse_args()
    if args.check:
        one_shot_check()
        return
    if args.color:
        one_shot_led(args.color)
        return
    if args.off:
        one_shot_led("off")
        return
    run_daemon()


if __name__ == "__main__":
    main()
