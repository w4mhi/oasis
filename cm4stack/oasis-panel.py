#!/usr/bin/env python3
"""
OASIS panel — renders station status to the CM4Stack ST7789V SPI panel.

Composes a 240x320 frame once a second with Pillow, packs it to RGB565, and
writes straight to the panel framebuffer (auto-detected by name 'fb_st7789v',
normally /dev/fb0). No SPI/display library.

Data comes from the SAME same-origin Flask app the web dashboard uses, on
127.0.0.1:8083 — so the panel and the website always agree:
    /api/aprs/stations -> {ok, count, stations:[{callsign,last_heard,speed_mph,via,...}]}
    /api/system        -> {hostname, ip, cpu_pct, cpu_temp_c, ram{pct}, disk{pct}, uptime_sec}

APRS "feed offline" mirrors the dashboard: shown when the endpoint is
unreachable OR returns ok:false. Empty station list = live-but-idle, NOT offline.

Run headless as a root systemd service (needs write access to the framebuffer).

Dependencies:
    sudo apt install -y python3-pil python3-numpy
"""

import os
import sys
import time
import json
import mmap
import signal
import urllib.request
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont
import numpy as np

# ── Config ──────────────────────────────────────────────────────────────────
BASE_URL      = "http://127.0.0.1:8083"
APRS_URL      = BASE_URL + "/api/aprs/stations"
SYSTEM_URL    = BASE_URL + "/api/system"
HTTP_TIMEOUT  = 5          # seconds per request
APRS_EVERY    = 15         # data refresh cadence (s)
SYSTEM_EVERY  = 30
REDRAW_EVERY  = 1          # frame/clock cadence (s)
FB_NAME_MATCH = "st7789"   # substring matched against /sys/class/graphics/fbN/name
BL_POWER_ON   = 0          # 0 = unblank (pwm-backlight). Old gpio-backlight needs 1.

# Colours (R, G, B)
BG     = (8, 10, 12)
ACCENT = (0, 200, 100)
TEXT   = (220, 225, 228)
DIM    = (120, 128, 135)
AMBER  = (255, 180, 84)
RED    = (220, 60, 60)
LINE   = (32, 38, 44)

# ── Fonts ───────────────────────────────────────────────────────────────────
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"

def _font(bold, size):
    names = (["DejaVuSansMono-Bold.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["DejaVuSansMono.ttf", "DejaVuSans.ttf"])
    for n in names:
        p = os.path.join(_FONT_DIR, n)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return ImageFont.load_default()

F_TITLE = _font(True, 22)
F_CLOCK = _font(True, 17)
F_BIG   = _font(True, 15)
F_LBL   = _font(False, 13)
F_SM    = _font(False, 11)

# ── Framebuffer ─────────────────────────────────────────────────────────────
class Framebuffer:
    """Auto-detected panel framebuffer, written via mmap."""

    def __init__(self):
        self.dev = None
        self.mm = None
        self.open()

    @staticmethod
    def _find():
        base = "/sys/class/graphics"
        for name in sorted(os.listdir(base)):
            if not name.startswith("fb"):
                continue
            try:
                nm = open(f"{base}/{name}/name").read().strip().lower()
            except OSError:
                continue
            if FB_NAME_MATCH in nm:
                return name
        return "fb0"  # fallback

    def open(self):
        self.fb = self._find()
        base = f"/sys/class/graphics/{self.fb}"
        w, h = (int(x) for x in open(f"{base}/virtual_size").read().strip().split(","))
        bpp = int(open(f"{base}/bits_per_pixel").read().strip())
        try:
            stride = int(open(f"{base}/stride").read().strip())
        except OSError:
            stride = w * bpp // 8
        self.w, self.h, self.bpp, self.stride = w, h, bpp, stride
        self.dev = os.open(f"/dev/{self.fb}", os.O_RDWR)
        self.mm = mmap.mmap(self.dev, self.stride * self.h,
                            mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        sys.stderr.write(f"[oasis-panel] using /dev/{self.fb} {self.w}x{self.h} "
                         f"{self.bpp}bpp stride={self.stride}\n")

    def close(self):
        try:
            if self.mm:
                self.mm.close()
            if self.dev is not None:
                os.close(self.dev)
        except OSError:
            pass
        self.mm = None
        self.dev = None

    def show(self, img):
        """img: PIL RGB image sized (w, h). Packs little-endian RGB565."""
        a = np.asarray(img, dtype=np.uint16)            # H x W x 3
        r = (a[:, :, 0] >> 3) << 11
        g = (a[:, :, 1] >> 2) << 5
        b = (a[:, :, 2] >> 3)
        buf = (r | g | b).astype("<u2").tobytes()
        row = self.w * 2
        if self.stride == row:
            self.mm.seek(0)
            self.mm.write(buf)
        else:                                            # honour line padding
            for y in range(self.h):
                self.mm.seek(y * self.stride)
                self.mm.write(buf[y * row:(y + 1) * row])


def set_backlight_on():
    """Best-effort: drive any backlight device to full + unblank. Never fatal."""
    base = "/sys/class/backlight"
    try:
        devs = os.listdir(base)
    except OSError:
        return
    for d in devs:
        p = f"{base}/{d}"
        try:
            mb = int(open(f"{p}/max_brightness").read().strip())
            with open(f"{p}/brightness", "w") as f:
                f.write(str(mb))
        except OSError:
            pass
        try:
            with open(f"{p}/bl_power", "w") as f:
                f.write(str(BL_POWER_ON))
        except OSError:
            pass

# ── Data ────────────────────────────────────────────────────────────────────
def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
            return json.load(r)
    except Exception:
        return None

def fetch_aprs():
    """Returns a list of stations, or None when the feed is offline."""
    d = get_json(APRS_URL)
    if not d or not d.get("ok"):
        return None                      # unreachable or ok:false -> offline
    return d.get("stations") or []       # [] = live but no packets yet

def fetch_system():
    return get_json(SYSTEM_URL)

# ── Helpers ─────────────────────────────────────────────────────────────────
def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

def age_str(dt):
    if not dt:
        return "—"
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 120:
        return "now"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"

def fmt_uptime(sec):
    if sec is None:
        return "—"
    sec = int(sec)
    d, h, m = sec // 86400, (sec % 86400) // 3600, (sec % 3600) // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"

def stat_color(v, warn, bad):
    if v is None:
        return DIM
    if v >= bad:
        return RED
    if v >= warn:
        return AMBER
    return ACCENT

def speed_mph(s):
    """Return numeric mph (>=0), tolerating missing/None/garbage values."""
    try:
        return float(s.get("speed_mph") or 0)
    except (TypeError, ValueError):
        return 0.0

# ── Render ──────────────────────────────────────────────────────────────────
def render(w, h, aprs, system):
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    pad = 8
    y = 6

    def text(x, yy, s, font, fill):
        d.text((x, yy), s, font=font, fill=fill)

    def rtext(yy, s, font, fill):
        """Right-aligned to the w-pad edge."""
        x = w - pad - d.textlength(s, font=font)
        d.text((x, yy), s, font=font, fill=fill)

    def hline(yy):
        d.line([(pad, yy), (w - pad, yy)], fill=LINE, width=1)

    # Header: OASIS + local clock (top), then date + UTC clock (second row),
    # both clocks sharing the same right edge so they stack into a column.
    now_utc = datetime.now(timezone.utc)
    now_loc = datetime.now().astimezone()            # system local timezone

    text(pad, y, "OASIS", F_TITLE, ACCENT)
    rtext(y + 5, now_loc.strftime("%H:%M"), F_CLOCK, AMBER)   # local, amber
    y += 28

    text(pad, y, now_utc.strftime("%Y-%m-%d"), F_LBL, DIM)
    rtext(y, now_utc.strftime("%H:%M:%SZ"), F_CLOCK, TEXT)    # UTC, same right edge
    y += 22
    hline(y); y += 6

    # APRS status — mirrors index.html setTitle(): LIVE only when stations are
    # present; empty feed / ok:false / unreachable all read as OFF (amber).
    stations = aprs or []                 # aprs is None (unreachable) or a list
    live = bool(stations)
    up_txt = fmt_uptime(system.get("uptime_sec")) if system else "—"
    text(pad, y, "APRS", F_BIG, TEXT)
    d.ellipse([(pad + 50, y + 4), (pad + 58, y + 12)],
              fill=ACCENT if live else AMBER)
    status = "LIVE" if live else "OFF"
    text(pad + 66, y, status, F_BIG, ACCENT if live else AMBER)
    sw = d.textlength(status, font=F_BIG)
    text(pad + 66 + sw + 5, y, f"\u00b7 {up_txt}", F_BIG, AMBER)   # uptime, after status
    y += 22
    if live:
        newest, best = None, None
        for s in stations:
            dt = parse_iso(s.get("last_heard"))
            if dt and (best is None or dt > best):
                best, newest = dt, s
        if newest:
            text(pad, y, "last", F_SM, DIM)
            text(pad + 34, y, str(newest.get("callsign", "?"))[:12], F_LBL, ACCENT)
            rtext(y, age_str(best), F_LBL, DIM)
            y += 18
    hline(y); y += 6

    # System telemetry
    if system:
        cpu = system.get("cpu_pct")
        temp = system.get("cpu_temp_c")
        ram = (system.get("ram") or {}).get("pct")
        disk = (system.get("disk") or {}).get("pct")
        text(pad, y, "CPU", F_SM, DIM)
        text(pad + 34, y, f"{round(cpu)}%" if cpu is not None else "n/a",
             F_LBL, stat_color(cpu, 70, 90))
        text(pad + 112, y, "TEMP", F_SM, DIM)
        text(pad + 152, y, f"{round(temp)}\u00b0C" if temp is not None else "n/a",
             F_LBL, stat_color(temp, 65, 80))
        y += 18
        text(pad, y, "RAM", F_SM, DIM)
        text(pad + 34, y, f"{round(ram)}%" if ram is not None else "n/a",
             F_LBL, stat_color(ram, 70, 90))
        text(pad + 112, y, "DISK", F_SM, DIM)
        text(pad + 152, y, f"{round(disk)}%" if disk is not None else "n/a",
             F_LBL, stat_color(disk, 70, 90))
        y += 18
        text(pad, y, "IP", F_SM, DIM)
        text(pad + 34, y, str(system.get("ip") or "—"), F_LBL, TEXT)
        y += 18
    else:
        text(pad, y, "system: n/a", F_LBL, DIM)
        y += 18
    hline(y); y += 6

    # Recent stations: callsign · speed (amber, only if moving) · age
    text(pad, y, "RECENT", F_BIG, AMBER)
    if live:
        rtext(y, f"{len(stations)} stn", F_BIG, AMBER)
    y += 16
    if aprs:
        rows = sorted(
            (s for s in aprs if parse_iso(s.get("last_heard"))),
            key=lambda s: parse_iso(s["last_heard"]), reverse=True,
        )
        line_h = 16
        max_rows = max(0, (h - y - 24) // line_h)
        for s in rows[:max_rows]:
            text(pad, y, str(s.get("callsign") or "?")[:9], F_LBL, ACCENT)
            spd = speed_mph(s)
            if spd > 0:
                text(w - pad - 96, y, f"{round(spd)}mph", F_LBL, AMBER)  # moving
            text(w - pad - 44, y, age_str(parse_iso(s.get("last_heard"))), F_LBL, DIM)
            y += line_h
    elif aprs is None:
        text(pad, y, "—", F_LBL, DIM)
    else:
        text(pad, y, "no stations yet", F_LBL, DIM)

    # Footer: host -> ip
    host = (system or {}).get("hostname") or os.uname().nodename
    ip = (system or {}).get("ip")
    foot = host + (f" \u00b7 {ip}" if ip else "")
    d.line([(pad, h - 20), (w - pad, h - 20)], fill=LINE, width=1)
    text(pad, h - 16, foot[:34], F_SM, DIM)
    return img

# ── Main loop ───────────────────────────────────────────────────────────────
def main():
    state = {"go": True}

    def stop(*_):
        state["go"] = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    set_backlight_on()

    fb = None
    while fb is None and state["go"]:
        try:
            fb = Framebuffer()
        except Exception as e:
            sys.stderr.write(f"[oasis-panel] framebuffer open failed: {e}\n")
            time.sleep(2)

    aprs = system = None
    last_aprs = last_sys = last_bl = 0.0

    while state["go"]:
        tick = time.time()
        try:
            if tick - last_aprs >= APRS_EVERY or last_aprs == 0:
                aprs = fetch_aprs()
                last_aprs = tick
            if tick - last_sys >= SYSTEM_EVERY or last_sys == 0:
                system = fetch_system()
                last_sys = tick
            if tick - last_bl >= 60:                 # re-assert backlight periodically
                set_backlight_on()
                last_bl = tick

            fb.show(render(fb.w, fb.h, aprs, system))
        except Exception as e:
            sys.stderr.write(f"[oasis-panel] loop error: {e}\n")
            try:                                     # recover a stale fb handle
                fb.close()
                fb = Framebuffer()
            except Exception:
                time.sleep(2)

        time.sleep(max(0.05, REDRAW_EVERY - (time.time() - tick)))

    if fb:
        fb.close()
    sys.stderr.write("[oasis-panel] stopped\n")


if __name__ == "__main__":
    main()