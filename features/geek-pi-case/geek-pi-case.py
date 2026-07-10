#!/usr/bin/env python3
"""
GeekPi ZP-0129 Case — temperature-driven fan + status OLED + UPS Plus monitor.

Drives the GeekPi ABS Mini Tower Kit (ZP-0129) on a Raspberry Pi 4/5:
  • Fan          @ GPIO18 (BCM), on/off via gpiozero (lgpio backend, Pi 5-safe)
  • SSD1306 OLED @ I2C 0x3c   (128x64, 1-bit)
  • UPS Plus     @ I2C 0x17   (EP-0136) — battery %, voltage, input source

Fan uses temperature hysteresis (on/off). The OLED shows CPU%/temp/RAM, host,
IP, and a UPS battery line. When on battery at/below SHUTDOWN_PCT for
SHUTDOWN_SAMPLES consecutive reads, a safe `systemctl poweroff` is issued.

Offline-first: apt deps only, inlined SSD1306 driver, no pip/CDN:
    sudo apt install -y python3-pil python3-smbus i2c-tools python3-gpiozero python3-lgpio

Pure decision logic lives in scripts/common/geek_pi_case.py (unit-tested);
this file is the hardware-I/O shell. Hardware libs are imported lazily so
`--help` works off-Pi.
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
FAN_GPIO         = 18                  # BCM pin driving the fan transistor
FAN_ON           = 55.0               # °C — hysteresis high
FAN_OFF          = 48.0               # °C — hysteresis low
FAN_PWM          = False              # reserved; on/off only in v1
SHUTDOWN_PCT     = 15                 # on-battery capacity % that triggers poweroff
SHUTDOWN_SAMPLES = 3                  # consecutive low reads required first
REFRESH_S        = 2.0                # OLED / fan / UPS cadence

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


# ── Fan (GPIO18 via gpiozero / lgpio) ─────────────────────────────────────────
def make_fan():
    from gpiozero import OutputDevice
    return OutputDevice(FAN_GPIO, active_high=True, initial_value=False)


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
    fan  = make_fan()
    fan_state = None
    guard = L.ShutdownGuard(pct=SHUTDOWN_PCT, samples=SHUTDOWN_SAMPLES)
    cpu_pct()  # prime the /proc/stat baseline

    def shutdown(*_):
        try:
            fan.on()          # fail-safe: leave cooling running
            oled.clear()
        except Exception:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        t = cpu_temp()
        desired = L.fan_decision(t, fan_state, FAN_ON, FAN_OFF)
        if desired != fan_state:
            fan.on() if desired else fan.off()
            fan_state = desired

        ups = read_ups(bus)

        img = Image.new("1", (OLED_W, OLED_H), 0)
        d = ImageDraw.Draw(img)
        d.text((0, 0),  L.format_stats_line(cpu_pct(), t, ram_pct()), font=font, fill=1)
        d.text((0, 16), host[:20], font=font, fill=1)
        d.text((0, 32), local_ip(), font=font, fill=1)
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
    print(f"cpu temp : {t:.1f} C   fan would be: {'ON' if L.fan_decision(t, None, FAN_ON, FAN_OFF) else 'off'}")
    ups = read_ups(bus)
    if ups is None:
        print("ups      : not readable on I2C 0x17")
    else:
        print(f"ups      : {L.format_ups_line(ups['capacity'], ups['batt_mv'], ups['on_batt'])}  "
              f"({ups['temp_c']} C, {'battery' if ups['on_batt'] else 'mains'})")

def one_shot_fan(state):
    fan = make_fan()
    fan.on() if state == "on" else fan.off()
    print(f"fan → {state}")
    # gpiozero releases the pin on process exit; hold briefly so the write lands.
    time.sleep(0.2)


def main():
    ap = argparse.ArgumentParser(
        description="GeekPi ZP-0129 case daemon (fan + OLED + UPS). No args = run the daemon.",
    )
    ap.add_argument("--check", action="store_true", help="Print CPU temp, fan intent, and UPS snapshot, then exit.")
    ap.add_argument("--fan", choices=("on", "off"), help="Manually set the fan and exit (testing).")
    args = ap.parse_args()
    if args.check:
        one_shot_check()
        return
    if args.fan:
        one_shot_fan(args.fan)
        return
    run_daemon()


if __name__ == "__main__":
    main()
