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
import re
import sys
import time
import json
import mmap
import signal
import struct
import threading
import urllib.request
from io import BytesIO
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
STALE_SEC     = 600        # APRS "fresh" window (s) for the Live/Feed markers
FB_NAME_MATCH = "st7789"   # substring matched against /sys/class/graphics/fbN/name
BL_POWER_ON   = 0          # 0 = unblank (pwm-backlight). Old gpio-backlight needs 1.
# Rotate the composed frame 180° before it reaches the panel — for an upside-down
# CM4Stack mount. Touch is tap-only (no coordinates), so it needs no remap.
# Enable at runtime with OASIS_PANEL_FLIP=1 (e.g. Environment= in the unit).
FLIP_180      = os.environ.get("OASIS_PANEL_FLIP", "0") == "1"

# Colours (R, G, B)
BG     = (8, 10, 12)
ACCENT = (0, 200, 100)
GREEN_DIM = (0, 90, 45)     # dim phase of the Feed heartbeat pulse
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
F_CALL  = _font(True, 20)
F_BIG   = _font(True, 15)
F_LBL   = _font(False, 13)
F_SM    = _font(False, 11)
F_OVL   = _font(True, 9)

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
    """Returns a list of stations, or None when the feed is offline.

    Returns every heard station, including position-less ones (status/message
    traffic): the RECENT list shows all callsigns and the LAST HEARD card
    represents whatever was heard most recently. Position-dependent fields on
    the card (grid, position, altitude) are simply omitted when absent.
    """
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

_ARROWS = ["\u2191", "\u2197", "\u2192", "\u2198",
           "\u2193", "\u2199", "\u2190", "\u2196"]
_CARD   = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

def course_dir(course):
    """Map a course in degrees to (arrow, cardinal); (None, None) if absent."""
    if course is None:
        return None, None
    try:
        i = int((float(course) % 360) / 45.0 + 0.5) % 8
    except (TypeError, ValueError):
        return None, None
    return _ARROWS[i], _CARD[i]

def grid_square(lat, lon):
    """6-char Maidenhead locator, or None. Inlined to stay import-free."""
    try:
        lat = float(lat); lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    lon += 180.0; lat += 90.0
    out = [chr(int(lon / 20) + 65), chr(int(lat / 10) + 65)]
    lon %= 20; lat %= 10
    out += [str(int(lon / 2)), str(int(lat))]
    lon %= 2; lat %= 1
    out += [chr(int(lon / (2 / 24.0)) + 97), chr(int(lat / (1 / 24.0)) + 97)]
    return "".join(out)

# ── APRS symbol sprites ───────────────────────────────────────────────────────
# Same 24×24 sheets the web map uses; fetched once over local HTTP (offline-safe).
SPRITE_CELL = 24
SPRITE_COLS = 16
SPRITE_URLS = {
    "/":  BASE_URL + "/maps/traffic/assets/aprs-symbols-24-0.png",   # primary table
    "\\": BASE_URL + "/maps/traffic/assets/aprs-symbols-24-1.png",   # alternate table
}
_sprites   = {}   # table char -> PIL.Image (RGBA) or False once a fetch failed
_sym_cache = {}   # (table, code) -> rendered 24×24 RGBA cell

def _load_sprite(table):
    """Fetch + cache a sprite sheet. Returns an RGBA Image or None."""
    if table in _sprites:
        return _sprites[table] or None
    img = None
    url = SPRITE_URLS.get(table)
    if url:
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
                img = Image.open(BytesIO(r.read())).convert("RGBA")
        except Exception:
            img = None
    _sprites[table] = img or False
    return img

def symbol_cell(sym_table, sym_code):
    """Return a 24×24 RGBA APRS symbol for table+code, or None if unavailable.

    Mirrors the map: backslash + overlay tables use the alternate sheet, and an
    overlay character is stamped into the bottom-right corner."""
    key = (sym_table, sym_code)
    if key in _sym_cache:
        return _sym_cache[key]
    overlay = sym_table not in ("/", "\\")
    sheet = _load_sprite("\\" if (overlay or sym_table == "\\") else "/")
    if sheet is None:
        return None
    idx = ord(sym_code) - 33 if sym_code else 0
    if idx < 0 or idx > 95:
        idx = 0
    x0 = (idx % SPRITE_COLS) * SPRITE_CELL
    y0 = (idx // SPRITE_COLS) * SPRITE_CELL
    cell = sheet.crop((x0, y0, x0 + SPRITE_CELL, y0 + SPRITE_CELL)).copy()
    if overlay:
        od = ImageDraw.Draw(cell)
        od.rectangle([15, 15, 23, 23], fill=(0, 0, 0, 191))
        od.text((17, 14), sym_table, font=F_OVL, fill=(255, 255, 255, 255))
    _sym_cache[key] = cell
    return cell

# ── Render ──────────────────────────────────────────────────────────────────
def render(w, h, aprs, system, view="list"):
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

    # ── GrayWolf service health: Live · API · Feed (dot + word). We monitor
    # APRS, so the old "APRS" label is gone; the row now carries three markers.
    stations = aprs or []                 # aprs is None (unreachable) or a list
    live = bool(stations)                 # packets present (used by sections below)
    up_txt = fmt_uptime(system.get("uptime_sec")) if system else "\u2014"

    # Most-recent station — drives freshness here and the LAST HEARD card below.
    newest, best = None, None
    for s in stations:
        dt = parse_iso(s.get("last_heard"))
        if dt and (best is None or dt > best):
            best, newest = dt, s
    fresh = best is not None and \
        (datetime.now(timezone.utc) - best).total_seconds() < STALE_SEC

    serving  = aprs is not None                          # endpoint answered ok:true
    api_hits = (system is not None) + (aprs is not None)  # 0, 1 or 2 endpoints up

    # Live (GrayWolf core): green fresh · amber stale · red "Dead" when not serving
    if not serving:
        live_word, live_col = "DEAD", RED
    else:
        live_word, live_col = "LIVE", (ACCENT if fresh else AMBER)
    # API (State API): green both endpoints · amber partial · red none
    api_col = ACCENT if api_hits == 2 else (AMBER if api_hits == 1 else RED)
    # Feed (APRS activity): green + heartbeat when fresh · amber silent · red down
    if aprs is None:
        feed_col, feed_pulse = RED, False
    elif fresh:
        feed_col, feed_pulse = ACCENT, True
    else:
        feed_col, feed_pulse = AMBER, False

    def marker(x, word, color, pulse=False):
        dot = GREEN_DIM if (pulse and int(time.time()) % 2) else color   # heartbeat
        d.ellipse([(x, y + 3), (x + 11, y + 14)], fill=dot)
        text(x + 16, y, word, F_BIG, color)
        return int(x + 16 + d.textlength(word, font=F_BIG) + 16)

    nx = marker(pad, live_word, live_col)
    nx = marker(nx, "API", api_col)
    marker(nx, "FEED", feed_col, feed_pulse)
    y += 22

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
    else:
        text(pad, y, "system: n/a", F_LBL, DIM)
        y += 18
    hline(y); y += 6

    if view == "card":
        # ── LAST HEARD station card: icon · position · altitude · speed · comment
        text(pad, y, "LAST HEARD", F_BIG, AMBER)
        if live:
            rtext(y, f"{len(stations)} stn", F_BIG, AMBER)
        y += 20

        if newest:
            # APRS symbol (sprite cell scaled 2× → 48px), callsign + age beside it.
            icon = symbol_cell(str(newest.get("sym_table") or "/"),
                               str(newest.get("sym_code") or "-"))
            icon_x, icon_y, icon_sz = pad, y, 48
            if icon is not None:
                big = icon.resize((icon_sz, icon_sz), Image.NEAREST)
                img.paste(big, (icon_x, icon_y), big)
            else:                                # sprites unreachable: text fallback
                d.rectangle([icon_x, icon_y, icon_x + icon_sz, icon_y + icon_sz],
                            outline=LINE, width=1)
                sc = f"{newest.get('sym_table', '/')}{newest.get('sym_code', '-')}"
                text(icon_x + 12, icon_y + 16, sc, F_LBL, DIM)

            tx = icon_x + icon_sz + 10
            text(tx, icon_y + 1, str(newest.get("callsign") or "?")[:10], F_CALL, ACCENT)
            text(tx, icon_y + 26, f"{age_str(best)} ago", F_LBL, DIM)
            via = str(newest.get("via") or "").strip()
            if via:
                rtext(icon_y + 26, via[:10], F_SM, DIM)
            y = icon_y + icon_sz + 8

            # Position + Maidenhead grid
            lat, lon = newest.get("lat"), newest.get("lon")
            if lat is not None and lon is not None:
                text(pad, y, f"{float(lat):.4f}, {float(lon):.4f}", F_LBL, TEXT)
                g = grid_square(lat, lon)
                if g:
                    rtext(y, g, F_LBL, ACCENT)
                y += 18

            # Speed + course (arrow · cardinal · bearing)
            arrow, card = course_dir(newest.get("course"))
            spd = speed_mph(newest)
            text(pad, y, "SPD", F_SM, DIM)
            if spd > 0:
                line = f"{round(spd)} mph"
                if arrow:
                    line += f"  {arrow} {card} {int(float(newest['course']))}\u00b0"
                text(pad + 34, y, line, F_LBL, AMBER)
            else:
                text(pad + 34, y, "stationary", F_LBL, DIM)
            y += 18

            # Altitude — feet first (US ham), metres second
            alt = newest.get("alt_m")
            text(pad, y, "ALT", F_SM, DIM)
            if alt is not None:
                text(pad + 34, y,
                     f"{round(float(alt) * 3.28084)} ft \u00b7 {round(float(alt))} m",
                     F_LBL, TEXT)
            else:
                text(pad + 34, y, "\u2014", F_LBL, DIM)
            y += 20

            # Comment — word-wrapped to the panel width, as many lines as fit.
            cmt = str(newest.get("comment") or "").strip()
            if cmt:
                avail = w - 2 * pad
                lines, cur = [], ""
                for word in cmt.split():
                    trial = f"{cur} {word}".strip()
                    if d.textlength(trial, font=F_SM) <= avail:
                        cur = trial
                    else:
                        if cur:
                            lines.append(cur)
                        cur = word
                if cur:
                    lines.append(cur)
                for ln in lines:
                    if y + 14 > h - 22:          # stop before the footer
                        break
                    text(pad, y, ln, F_SM, DIM)
                    y += 14
        elif aprs is None:
            text(pad, y, "feed offline", F_LBL, DIM)
        else:
            text(pad, y, "no stations yet", F_LBL, DIM)
    else:
        # ── RECENT list: callsign · speed (amber, only if moving) · age ─────────
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
            text(pad, y, "\u2014", F_LBL, DIM)
        else:
            text(pad, y, "no stations yet", F_LBL, DIM)

    # Footer: host · ip (left)  ·  uptime (right)
    host = (system or {}).get("hostname") or os.uname().nodename
    ip = (system or {}).get("ip")
    left = host + (f" \u00b7 {ip}" if ip else "")
    d.line([(pad, h - 20), (w - pad, h - 20)], fill=LINE, width=1)
    text(pad, h - 16, left[:26], F_SM, DIM)
    if up_txt and up_txt != "\u2014":
        rtext(h - 16, f"\u2191 {up_txt}", F_SM, DIM)
    return img

# ── Touch input (GT911) ──────────────────────────────────────────────────────
# Raw evdev reader — no python-evdev dependency. A single tap toggles the view
# between the station list and the LAST HEARD card.
EV_KEY             = 0x01
EV_ABS             = 0x03
BTN_TOUCH          = 0x14a
ABS_MT_TRACKING_ID = 0x39
_EV_FORMAT = "llHHi"                 # input_event on 64-bit Linux (24 bytes)
_EV_SIZE   = struct.calcsize(_EV_FORMAT)

def find_touch_device():
    """Locate the touchscreen's /dev/input/eventN via /proc/bus/input/devices."""
    try:
        blocks = open("/proc/bus/input/devices").read().split("\n\n")
    except OSError:
        return None
    for blk in blocks:
        low = blk.lower()
        if "goodix" in low or "gt911" in low or "touch" in low:
            for ln in blk.splitlines():
                if ln.startswith("H:"):
                    m = re.search(r"event\d+", ln)
                    if m:
                        return "/dev/input/" + m.group(0)
    return None

def touch_watch(state):
    """Background thread: toggle state['view'] on each screen tap (touch-down)."""
    contact = False
    while state["go"]:
        dev = find_touch_device()
        if not dev:
            time.sleep(3)
            continue
        try:
            with open(dev, "rb", buffering=0) as f:
                sys.stderr.write(f"[oasis-panel] touch input: {dev}\n")
                while state["go"]:
                    data = f.read(_EV_SIZE)
                    if not data or len(data) < _EV_SIZE:
                        break
                    _s, _us, etype, code, value = struct.unpack(_EV_FORMAT, data)
                    down = None
                    if etype == EV_KEY and code == BTN_TOUCH:
                        down = (value == 1)
                    elif etype == EV_ABS and code == ABS_MT_TRACKING_ID:
                        down = (value != -1)
                    if down is None:
                        continue
                    if down and not contact:        # rising edge = one tap
                        state["view"] = "card" if state["view"] == "list" else "list"
                    contact = down
        except OSError as e:
            sys.stderr.write(f"[oasis-panel] touch read error: {e}\n")
            time.sleep(2)

# ── Main loop ───────────────────────────────────────────────────────────────
def main():
    state = {"go": True, "view": "list"}

    def stop(*_):
        state["go"] = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    # Tap the screen to flip between the station list and the LAST HEARD card.
    threading.Thread(target=touch_watch, args=(state,), daemon=True).start()

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

            frame = render(fb.w, fb.h, aprs, system, state["view"])
            if FLIP_180:
                frame = frame.rotate(180)
            fb.show(frame)
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


# ── Preview / self-test (no framebuffer) ─────────────────────────────────────
_MOCK_STATION = {
    "callsign": "W4MHI-9", "sym_table": "/", "sym_code": ">",
    "lat": 39.7128, "lon": -104.9851, "alt_m": 1609.3, "speed_mph": 34.2,
    "course": 132, "comment": "Mobile - heading home for dinner",
    "last_heard": datetime.now(timezone.utc).isoformat(), "via": "WIDE1-1",
}
_MOCK_SYSTEM = {
    "cpu_pct": 23, "cpu_temp_c": 47, "ram": {"pct": 41}, "disk": {"pct": 55},
    "ip": "192.168.1.42", "uptime_sec": 90000, "hostname": os.uname().nodename,
}

def preview(out_path, mock=False, view="card", size=(240, 320)):
    """Render one frame to a PNG instead of the panel. Great for testing the
    layout over SSH: scp the file back, or open it on a desktop.

    mock=False pulls live data from the running server (same as the panel);
    mock=True uses built-in sample data so it works with no server at all.
    view is "card" (LAST HEARD) or "list" (RECENT stations).
    """
    if mock:
        aprs, system = [_MOCK_STATION], _MOCK_SYSTEM
    else:
        aprs, system = fetch_aprs(), fetch_system()
    img = render(size[0], size[1], aprs, system, view)
    if FLIP_180:
        img = img.rotate(180)
    img.save(out_path)
    sys.stderr.write(f"[oasis-panel] preview written: {out_path} "
                     f"({'mock' if mock else 'live'} data, {view} view)\n")
    return out_path


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("--preview", "--selftest", "--mock"):
        # oasis-panel.py --preview [--list|--card] [OUT.png]   -> live data
        # oasis-panel.py --mock    [--list|--card] [OUT.png]   -> sample data
        out = next((a for a in args[1:] if not a.startswith("-")),
                   "/tmp/oasis-panel-preview.png")
        view = "list" if "--list" in args else "card"
        preview(out, mock=(args[0] == "--mock"), view=view)
    else:
        main()