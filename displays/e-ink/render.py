#!/usr/bin/env python3
"""
Frame composition for the OASIS e-ink monitor.

Every screen is a 1-bit Pillow image at the panel's logical (landscape)
resolution, drawn here and handed to the display backend (see display.py).
Keeping all drawing in one place means the on-Pi panel and the PNG preview
share the exact same compositing code — the preview is faithful.

For now this only builds the boot splash (OASIS / subtitle / motto). Screen
renderers (stations, winlink, propagation, weather) land in later tasks.
"""

from PIL import Image, ImageDraw, ImageFont

# 1-bit palette: e-ink is black ink on a white background.
WHITE = 255
BLACK = 0

# Font search order. First existing file wins per weight; if none resolve we
# fall back to Pillow's built-in bitmap font so rendering never hard-fails.
_FONT_CANDIDATES = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",          # Pi / Debian
        "/System/Library/Fonts/Supplemental/Arial.ttf",            # macOS
        "/Library/Fonts/Arial.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",     # Pi / Debian
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # macOS
        "/Library/Fonts/Arial Bold.ttf",
    ],
}


def _first_existing(paths):
    import os
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def load_font(size, weight="regular", override=None):
    """A TrueType font at `size`, or the bitmap default if none is found."""
    path = override or _first_existing(_FONT_CANDIDATES.get(weight, []))
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _text_size(draw, text, font):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _wrap(draw, text, font, max_w):
    """Greedy word-wrap `text` to fit `max_w` pixels."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if _text_size(draw, trial, font)[0] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_centered(draw, lines, font, y, width, gap=2):
    """Draw each line horizontally centered; return the y below the block."""
    for line in lines:
        tw, th = _text_size(draw, line, font)
        draw.text(((width - tw) // 2, y), line, font=font, fill=BLACK)
        y += th + gap
    return y


def render_splash(cfg, version=""):
    """Boot splash: OASIS title, subtitle, motto, and a station/version footer."""
    disp = cfg["display"]
    W, H = disp["width"], disp["height"]
    brand = cfg["branding"]
    fonts = cfg.get("fonts", {})

    img = Image.new("1", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # Border to frame the panel.
    draw.rectangle([0, 0, W - 1, H - 1], outline=BLACK)

    title_font = load_font(46, "bold", fonts.get("bold"))
    sub_font = load_font(14, "regular", fonts.get("regular"))
    motto_font = load_font(14, "bold", fonts.get("bold"))
    foot_font = load_font(11, "regular", fonts.get("regular"))

    inner = W - 24  # horizontal padding for wrapped text

    # Title, tracked out slightly for presence at this size.
    title = " ".join(brand["title"])
    tw, th = _text_size(draw, title, title_font)
    y = 16
    draw.text(((W - tw) // 2, y), title, font=title_font, fill=BLACK)
    y += th + 8

    # Thin rule under the title.
    draw.line([(W // 2 - tw // 2, y), (W // 2 + tw // 2, y)], fill=BLACK)
    y += 8

    # Subtitle (wraps).
    y = _draw_centered(draw, _wrap(draw, brand["subtitle"], sub_font, inner),
                       sub_font, y, W, gap=1)
    y += 8

    # Motto (quoted, wraps).
    motto = f"“{brand['motto']}”"
    y = _draw_centered(draw, _wrap(draw, motto, motto_font, inner),
                       motto_font, y, W, gap=1)

    # Footer pinned to the bottom: callsign - version - booting.
    call = cfg.get("station", {}).get("callsign", "").strip()
    parts = [p for p in (call, f"v{version}" if version else "", "booting…") if p]
    footer = "   ·   ".join(parts)
    fw, fh = _text_size(draw, footer, foot_font)
    draw.text(((W - fw) // 2, H - fh - 8), footer, font=foot_font, fill=BLACK)

    return img


# ── Common header + screens ──────────────────────────────────────────────────

def _dot(draw, x, cy, filled, r=3):
    """Small status dot: filled = up, hollow ring = down. Returns right edge x."""
    box = (x, cy - r, x + 2 * r, cy + r)
    if filled:
        draw.ellipse(box, fill=BLACK)
    else:
        draw.ellipse(box, outline=BLACK)
    return x + 2 * r


def render_header(draw, cfg, ctx, W):
    """Draw the common header (title+clock, service dots, system) and a divider.

    Returns the y coordinate where the screen body should start.
    """
    fonts = cfg.get("fonts", {})
    title_f = load_font(15, "bold", fonts.get("bold"))
    clock_f = load_font(11, "regular", fonts.get("regular"))
    svc_up_f = load_font(11, "bold", fonts.get("bold"))       # enabled: bold
    svc_down_f = load_font(11, "regular", fonts.get("regular"))  # disabled: normal
    sys_f = load_font(11, "regular", fonts.get("regular"))
    pad = 5

    # Row 1: screen title (left) + LOC · UTC clock (right, same line).
    draw.text((pad, 2), ctx.get("title", ""), font=title_f, fill=BLACK)
    loc, utc = ctx.get("clock", ("", ""))
    clock = f"LOC {loc} · UTC {utc}"
    cw, _ = _text_size(draw, clock, clock_f)
    draw.text((W - pad - cw, 5), clock, font=clock_f, fill=BLACK)

    # Row 2: service dots. Two cues per service so up/down reads at a glance —
    # enabled = filled dot + bold label, disabled = hollow circle + normal label.
    # GPS carries its fix state (3D / 2D / no fix / off) in the label.
    y = 21
    x = pad
    for name in cfg["services"].get("order", []):
        up = ctx.get("services", {}).get(name)
        x = _dot(draw, x, y + 6, up) + 3
        label = cfg["services"][name].get("label", name)
        if name == "GPS":
            label = f"GPS {ctx.get('gps_fix', 'off')}"
        f = svc_up_f if up else svc_down_f
        draw.text((x, y), label, font=f, fill=BLACK)
        lw, _ = _text_size(draw, label, f)
        x += lw + 9

    # Row 3: system — CPU / RAM / TEMP / IP.
    y = 35
    sysd = ctx.get("system", {})

    def fmt(v, suf=""):
        return f"{v}{suf}" if v is not None else "—"

    seg = []
    for field in cfg.get("system", {}).get("show", []):
        if field == "CPU":
            seg.append("CPU " + fmt(sysd.get("cpu"), "%"))
        elif field == "RAM":
            seg.append("RAM " + fmt(sysd.get("ram"), "%"))
        elif field == "TEMP":
            seg.append("TEMP" + fmt(sysd.get("temp"), "°"))
        elif field == "IP":
            seg.append(fmt(sysd.get("ip")))
    draw.text((pad, y), "  ".join(seg), font=sys_f, fill=BLACK)

    # Divider.
    y = 49
    draw.line((pad, y, W - pad, y), fill=BLACK)
    return y + 4


# ── Station-card formatting (ported from displays/cm4stack/oasis-panel.py) ─────────────

def _parse_iso(s):
    """Parse graywolf timestamps and plain ISO 8601.

    graywolf sends 'YYYY-MM-DD HH:MM:SS.fffffffff±HH:MM' — a space separator and
    nanosecond precision, both of which datetime.fromisoformat() rejects on
    older Pythons. Normalize the space to 'T' and clamp the fraction to 6 digits.
    """
    if not s:
        return None
    import re
    from datetime import datetime
    text = str(s).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)   # nanoseconds -> microseconds
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _age_str(dt):
    from datetime import datetime, timezone
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


def _speed_mph(s):
    try:
        return float(s.get("speed_mph") or 0)
    except (TypeError, ValueError):
        return 0.0


_CARD = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _course_cardinal(course):
    if course is None:
        return None
    try:
        return _CARD[int((float(course) % 360) / 45.0 + 0.5) % 8]
    except (TypeError, ValueError):
        return None


def _grid_square(lat, lon):
    """6-char Maidenhead locator, or None."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    lon += 180.0
    lat += 90.0
    out = [chr(int(lon / 20) + 65), chr(int(lat / 10) + 65)]
    lon %= 20
    lat %= 10
    out += [str(int(lon / 2)), str(int(lat))]
    lon %= 2
    lat %= 1
    out += [chr(int(lon / (2 / 24.0)) + 97), chr(int(lat / (1 / 24.0)) + 97)]
    return "".join(out)


def _short_age(secs):
    """Compact age string for a stale-data tag."""
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return ""
    if secs < 3600:
        return f"{max(1, secs // 60)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _weather_icon(draw, x, y, size, cond, night=False):
    """Draw a 1-bit weather glyph in the size×size box at (x, y).

    Clouds are solid black silhouettes; sun/moon are outlines — the contrast
    reads clearly on 1-bit at both the base (40px) and forecast (22-24px) sizes.
    """
    import math
    s = int(size)
    cx = x + s // 2

    def sun(ox, oy, r):
        draw.ellipse([ox - r, oy - r, ox + r, oy + r], outline=BLACK)
        for a in range(0, 360, 45):
            dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
            draw.line([(ox + dx * (r + 2), oy + dy * (r + 2)),
                       (ox + dx * (r + r), oy + dy * (r + r))], fill=BLACK)

    def moon(ox, oy, r):
        draw.ellipse([ox - r, oy - r, ox + r, oy + r], fill=BLACK)
        draw.ellipse([ox - r + r // 2, oy - r - 1, ox + r + r // 2, oy + r - 1],
                     fill=WHITE)

    def cloud(ox, oy, w, h):
        r = max(3, h // 2)
        draw.ellipse([ox, oy + h - 2 * r, ox + 2 * r, oy + h], fill=BLACK)
        draw.ellipse([ox + w - 2 * r, oy + h - 2 * r, ox + w, oy + h], fill=BLACK)
        draw.ellipse([ox + w // 2 - r, oy, ox + w // 2 + r, oy + 2 * r], fill=BLACK)
        draw.rectangle([ox + r, oy + h - 2 * r, ox + w - r, oy + h], fill=BLACK)

    def streaks(ox, oy, w, kind):
        step = max(4, w // 3)
        for i in range(3):
            sx = ox + i * step
            if kind == "rain":
                draw.line([(sx, oy), (sx - 2, oy + 5)], fill=BLACK)
            elif kind == "drizzle":
                draw.point((sx, oy + 2), fill=BLACK)
            elif kind == "snow":
                draw.line([(sx - 2, oy + 2), (sx + 2, oy + 2)], fill=BLACK)
                draw.line([(sx, oy), (sx, oy + 4)], fill=BLACK)

    if cond == "clear":
        sun(cx, y + s // 2, s // 3)
    elif cond == "moon":
        moon(cx, y + s // 2, s // 3)
    elif cond == "fog":
        for i in range(4):
            yy = y + s // 4 + i * max(2, s // 6)
            draw.line([(x + 2, yy), (x + s - 2, yy)], fill=BLACK)
    elif cond in ("partly", "partly_night"):
        r = s // 4
        if cond == "partly_night":
            moon(x + r + 1, y + r + 1, r)
        else:
            sun(x + r + 2, y + r + 2, r)
        cloud(x + s // 4, y + s // 3, s * 2 // 3, s // 3)
    else:
        cloud(x + 2, y + 2, s - 4, s // 2)
        base = y + 2 + s // 2 + 1
        if cond in ("rain", "drizzle", "snow"):
            streaks(x + 5, base, s - 10, cond)
        elif cond == "storm":
            draw.polygon([(cx, base), (cx - 4, base + 7), (cx, base + 6),
                          (cx - 3, base + 13)], fill=BLACK)
        # plain "cloud" (and any unknown key) draws just the cloud silhouette


def _plane_icon(draw, x, y, size):
    """A simple 1-bit top-view airplane glyph filling the size×size icon box —
    the ADS-B counterpart to an APRS station's symbol."""
    cx = x + size // 2
    top, bot = y + 4, y + size - 6
    draw.line([(cx, top), (cx, bot)], fill=BLACK, width=3)             # fuselage
    draw.line([(x + 4, y + size // 2), (x + size - 4, y + size // 2)],
              fill=BLACK, width=3)                                     # wings
    draw.line([(cx - 6, bot - 3), (cx + 6, bot - 3)], fill=BLACK, width=2)  # tail
    draw.ellipse([cx - 2, top - 2, cx + 2, top + 3], fill=BLACK)       # nose


def _mini_plane(draw, x, y):
    """A 9px top-view plane marker for the merged list's aircraft rows."""
    cx = x + 4
    draw.line([(cx, y), (cx, y + 9)], fill=BLACK)
    draw.line([(x, y + 4), (x + 8, y + 4)], fill=BLACK)
    draw.line([(cx - 2, y + 8), (cx + 2, y + 8)], fill=BLACK)


def _render_screen1(draw, img, cfg, ctx, W, y):
    """Screen 1 (TRAFFIC) — last-heard contact card for the merged APRS + ADS-B
    feed: icon · callsign · age · source line · position + grid · speed/course ·
    altitude · comment. APRS stations show their symbol + digi path + comment;
    ADS-B aircraft show a plane glyph + squawk/hex + barometric altitude."""
    fonts = cfg.get("fonts", {})
    call_f = load_font(15, "bold", fonts.get("bold"))
    age_f = load_font(11, "regular", fonts.get("regular"))
    via_f = load_font(10, "regular", fonts.get("regular"))
    lbl_f = load_font(10, "bold", fonts.get("bold"))
    val_f = load_font(12, "regular", fonts.get("regular"))
    cmt_f = load_font(10, "regular", fonts.get("regular"))
    pad = 6

    c = ctx.get("contacts", {})
    if not c.get("ok"):
        draw.text((pad, y + 12), "APRS & ADS-B offline", font=call_f, fill=BLACK)
        return
    s = c.get("last")
    if not s:
        draw.text((pad, y + 12), "No traffic heard yet", font=call_f, fill=BLACK)
        return
    is_air = s.get("source") == "adsb"

    # Icon: aircraft draw a plane glyph; stations use the pre-fetched APRS symbol
    # (or a labelled box fallback).
    icon_sz = 40
    ix, iy = pad, y
    if is_air:
        _plane_icon(draw, ix, iy, icon_sz)
    else:
        icon = ctx.get("station_icon")
        if icon is not None:
            img.paste(icon, (ix, iy))
        else:
            draw.rectangle([ix, iy, ix + icon_sz, iy + icon_sz], outline=BLACK)
            sc = f"{s.get('sym_table', '/')}{s.get('sym_code', '-')}"
            draw.text((ix + 8, iy + 14), sc, font=via_f, fill=BLACK)

    # Callsign + age (right), beside the icon.
    tx = ix + icon_sz + 8
    call = str(s.get("callsign") or "?")[:11]
    draw.text((tx, iy), call, font=call_f, fill=BLACK)
    age_raw = _age_str(_parse_iso(s.get("last_heard")))
    age = age_raw if age_raw in ("now", "—") else f"{age_raw} ago"
    aw, _ = _text_size(draw, age, age_f)
    age_x = W - pad - aw
    draw.text((age_x, iy + 2), age, font=age_f, fill=BLACK)

    # Airline tag beside the callsign (aircraft only): ASA424 [ALASKA] — the same
    # bracket the dashboard cards show. Truncates the name (keeping the closing
    # bracket) so it never runs under the age; drops out entirely if nothing fits.
    op = s.get("operator")
    if is_air and op:
        cw, _ = _text_size(draw, call, call_f)
        tag_x = tx + cw + 5
        avail = age_x - 4 - tag_x
        name = str(op)
        tag = f"[{name}]"
        while name and _text_size(draw, tag, via_f)[0] > avail:
            name = name[:-1]
            tag = f"[{name}]"
        if name:
            draw.text((tag_x, iy + 4), tag, font=via_f, fill=BLACK)

    # Source line: ADS-B → squawk (emergency-flagged) + hex; APRS → RF/IS + path.
    if is_air:
        bits = ["ADS-B"]
        sq = str(s.get("squawk") or "").strip()
        if sq in ("7500", "7600", "7700"):
            bits.append(f"! {sq} EMERG")
        elif sq:
            bits.append(f"sq {sq}")
        hexid = str(s.get("hex") or "").strip()
        if hexid:
            bits.append(hexid.upper())
        heard = "  ".join(bits)
    else:
        src = str(s.get("via") or "").strip().lower()
        tag = "RF" if src == "rf" else ("IS" if src else "")
        path = s.get("path")
        pstr = ",".join(str(p) for p in path) if isinstance(path, list) else str(path or "")
        heard = "  ".join(p for p in (tag, pstr) if p)
    if heard:
        draw.text((tx, iy + 23), heard[:32], font=via_f, fill=BLACK)

    y = iy + icon_sz + 6

    # Position + Maidenhead grid.
    lat, lon = s.get("lat"), s.get("lon")
    if lat is not None and lon is not None:
        try:
            draw.text((pad, y), f"{float(lat):.4f}, {float(lon):.4f}",
                      font=val_f, fill=BLACK)
            g = _grid_square(lat, lon)
            if g:
                gw, _ = _text_size(draw, g, val_f)
                draw.text((W - pad - gw, y), g, font=val_f, fill=BLACK)
            y += 17
        except (TypeError, ValueError):
            pass

    # Speed + course.
    spd = _speed_mph(s)
    draw.text((pad, y + 1), "SPD", font=lbl_f, fill=BLACK)
    if spd > 0:
        line = f"{round(spd)} mph"
        card = _course_cardinal(s.get("course"))
        if card:
            try:
                line += f"  {card} {int(float(s['course']))}°"
            except (TypeError, ValueError):
                pass
        draw.text((pad + 30, y), line, font=val_f, fill=BLACK)
    else:
        draw.text((pad + 30, y), "stationary", font=val_f, fill=BLACK)
    y += 17

    # Altitude — aircraft report barometric feet directly (or 'ground');
    # APRS stations report metres, shown as feet · metres.
    if is_air:
        alt = s.get("alt_ft")
        draw.text((pad, y + 1), "ALT", font=lbl_f, fill=BLACK)
        if alt == "ground":
            draw.text((pad + 30, y), "on ground", font=val_f, fill=BLACK)
            y += 17
        elif alt is not None:
            try:
                draw.text((pad + 30, y), f"{round(float(alt))} ft",
                          font=val_f, fill=BLACK)
                y += 17
            except (TypeError, ValueError):
                pass
    else:
        alt = s.get("alt_m", s.get("alt"))
        if alt is not None:
            try:
                draw.text((pad, y + 1), "ALT", font=lbl_f, fill=BLACK)
                draw.text((pad + 30, y),
                          f"{round(float(alt) * 3.28084)} ft · {round(float(alt))} m",
                          font=val_f, fill=BLACK)
                y += 17
            except (TypeError, ValueError):
                pass

    # Comment (APRS only — aircraft carry none).
    if not is_air:
        cmt = str(s.get("comment") or "").strip()
        if cmt:
            for line in _wrap(draw, cmt, cmt_f, W - 2 * pad):
                if y > 160:
                    break
                draw.text((pad, y), line, font=cmt_f, fill=BLACK)
                y += 12


def _dist_dir(home, lat, lon):
    """'4.2mi NE' from home (lat,lon) to a station, or '' if either is missing."""
    import math
    try:
        hlat, hlon = float(home[0]), float(home[1])
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError, IndexError):
        return ""
    p1, p2 = math.radians(hlat), math.radians(lat)
    dphi, dl = math.radians(lat - hlat), math.radians(lon - hlon)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * 3958.8 * math.asin(min(1.0, math.sqrt(a)))   # miles
    if d < 0.1:
        return "here"
    yb = math.sin(dl) * math.cos(p2)
    xb = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    card = _CARD[int((math.degrees(math.atan2(yb, xb)) % 360) / 45.0 + 0.5) % 8]
    return f"{d:.1f}mi {card}" if d < 100 else f"{int(d)}mi {card}"


def _render_screen1_list(draw, img, cfg, ctx, W, y):
    """Screen 1 long-press view — the merged APRS + ADS-B contact list, most
    recent first: [✈] callsign · age · distance+bearing from home. Aircraft rows
    carry a small plane marker. Non-scrolling: shows as many as fit with a
    '+N more' footer (the combined count can be large)."""
    fonts = cfg.get("fonts", {})
    cs_f = load_font(12, "bold", fonts.get("bold"))
    row_f = load_font(11, "regular", fonts.get("regular"))
    pad = 6
    bottom = cfg["display"]["height"] - 6

    c = ctx.get("contacts", {})
    if not c.get("ok"):
        draw.text((pad, y + 12), "APRS & ADS-B offline", font=cs_f, fill=BLACK)
        return
    lst = c.get("list") or []
    if not lst:
        draw.text((pad, y + 12), "No traffic heard yet", font=cs_f, fill=BLACK)
        return

    station = cfg.get("station", {})
    home = (station.get("lat"), station.get("lon"))

    row_h = 16
    fits = max(1, (bottom - y) // row_h)
    footer = len(lst) > fits
    rows = lst[: fits - 1] if footer else lst[:fits]

    cs_x = pad + 11   # leave a marker column so aircraft/station rows align
    for s in rows:
        if s.get("source") == "adsb":
            _mini_plane(draw, pad, y + 3)
        draw.text((cs_x, y), str(s.get("callsign") or "?")[:9], font=cs_f, fill=BLACK)
        age = _age_str(_parse_iso(s.get("last_heard")))
        draw.text((cs_x + 92, y + 1), age, font=row_f, fill=BLACK)
        dd = _dist_dir(home, s.get("lat"), s.get("lon"))
        if dd:
            dw, _ = _text_size(draw, dd, row_f)
            draw.text((W - pad - dw, y + 1), dd, font=row_f, fill=BLACK)
        y += row_h

    if footer:
        more = f"+{len(lst) - len(rows)} more"
        mw, _ = _text_size(draw, more, row_f)
        draw.text((W - pad - mw, y + 1), more, font=row_f, fill=BLACK)


def _alert_banner(draw, alert, W, y):
    """Draw the severe-weather strip; return the new body-top y. No alert: no-op."""
    if not alert:
        return y
    f = load_font(11, "bold")
    filled = alert.get("severity") in ("Extreme", "Severe")
    txt = f"! {alert.get('event', 'Alert')}"
    if alert.get("until"):
        txt += f" until {alert['until']}"
    pad, h = 6, 15
    while txt and _text_size(draw, txt, f)[0] > W - 2 * pad:
        txt = txt[:-1]
    if filled:
        draw.rectangle([3, y, W - 3, y + h], fill=BLACK)
        draw.text((pad, y + 2), txt, font=f, fill=WHITE)
    else:
        draw.rectangle([3, y, W - 3, y + h], outline=BLACK)
        draw.text((pad, y + 2), txt, font=f, fill=BLACK)
    return y + h + 3


def _weather_unavailable(draw, wx, y, font):
    msg = (wx or {}).get("error") or "weather unavailable"
    if msg == "no API key":
        msg = "set OWM_API_KEY"
    draw.text((6, y + 14), msg, font=font, fill=BLACK)


def _render_screen4(draw, img, cfg, ctx, W, y):
    """Screen 4 base — 'Now' conditions + a mini multi-day strip."""
    fonts = cfg.get("fonts", {})
    temp_f = load_font(30, "bold", fonts.get("bold"))
    cond_f = load_font(12, "regular", fonts.get("regular"))
    det_f = load_font(11, "regular", fonts.get("regular"))
    hilo_f = load_font(11, "bold", fonts.get("bold"))
    dow_f = load_font(11, "bold", fonts.get("bold"))
    mini_f = load_font(11, "regular", fonts.get("regular"))
    pad = 6

    wx = ctx.get("weather")
    if not wx or not wx.get("ok"):
        _weather_unavailable(draw, wx, y, cond_f)
        return

    y = _alert_banner(draw, wx.get("alert"), W, y)

    stale = wx.get("stale_s")
    if stale:
        tag = f"stale {_short_age(stale)}"
        tw, _ = _text_size(draw, tag, det_f)
        draw.text((W - pad - tw, y), tag, font=det_f, fill=BLACK)

    now = wx.get("now") or {}
    icon_sz = 40
    _weather_icon(draw, pad, y, icon_sz, now.get("cond", "cloud"), now.get("night"))

    tx = pad + icon_sz + 8
    temp = now.get("temp")
    tstr = f"{temp}°" if temp is not None else "—°"
    draw.text((tx, y - 4), tstr, font=temp_f, fill=BLACK)
    tw, _ = _text_size(draw, tstr, temp_f)
    draw.text((tx + tw + 6, y + 6), str(now.get("cond_text", ""))[:15],
              font=cond_f, fill=BLACK)

    det = (f"feels {now.get('feels', '—')}°  H{now.get('humidity', '—')}%  "
           f"{now.get('wind_dir', '')} {now.get('wind_mph', '—')}").strip()
    draw.text((tx, y + 26), det, font=det_f, fill=BLACK)

    hi, lo = now.get("hi"), now.get("lo")
    if hi is not None or lo is not None:
        draw.text((tx, y + 41), f"Hi {hi}  Lo {lo}", font=hilo_f, fill=BLACK)

    y += icon_sz + 13
    for dx in range(pad, W - pad, 4):        # dotted divider
        draw.point((dx, y), fill=BLACK)
    y += 5

    days = (wx.get("days") or [])[: cfg.get("weather", {}).get("mini_days", 3)]
    if days:
        col = (W - 2 * pad) // max(1, len(days))
        mini_sz = 22
        for i, d in enumerate(days):
            cxp = pad + i * col
            draw.text((cxp, y), d.get("dow", ""), font=dow_f, fill=BLACK)
            _weather_icon(draw, cxp, y + 14, mini_sz, d.get("cond", "cloud"), False)
            draw.text((cxp, y + 14 + mini_sz + 1),
                      f"{d.get('hi', '—')}/{d.get('lo', '—')}",
                      font=mini_f, fill=BLACK)


def _render_screen4_list(draw, img, cfg, ctx, W, y):
    """Screen 4 hold — full multi-day forecast (dow / icon / hi / lo / wind)."""
    fonts = cfg.get("fonts", {})
    dow_f = load_font(11, "bold", fonts.get("bold"))
    hi_f = load_font(11, "regular", fonts.get("regular"))
    det_f = load_font(10, "regular", fonts.get("regular"))
    pad = 6
    H = cfg["display"]["height"]

    wx = ctx.get("weather")
    if not wx or not wx.get("ok"):
        _weather_unavailable(draw, wx, y, hi_f)
        return

    y = _alert_banner(draw, wx.get("alert"), W, y)

    days = wx.get("days") or []
    if not days:
        draw.text((pad, y + 14), "No forecast", font=hi_f, fill=BLACK)
        return
    n = max(1, min(len(days), cfg.get("weather", {}).get("forecast_days", 5)))
    days = days[:n]
    col = (W - 2 * pad) // n
    icon_sz = 24
    for i, d in enumerate(days):
        cxp = pad + i * col
        draw.text((cxp, y), d.get("dow", ""), font=dow_f, fill=BLACK)
        _weather_icon(draw, cxp, y + 13, icon_sz, d.get("cond", "cloud"), False)
        yy = y + 13 + icon_sz + 2
        draw.text((cxp, yy), f"{d.get('hi', '—')}°", font=hi_f, fill=BLACK)
        draw.text((cxp, yy + 13), f"{d.get('lo', '—')}°", font=det_f, fill=BLACK)
        if d.get("wind_dir"):
            draw.text((cxp, yy + 25), d["wind_dir"], font=det_f, fill=BLACK)

    now = wx.get("now") or {}
    foot = f"↑{now.get('sunrise', '')}  ↓{now.get('sunset', '')}"
    if days[0].get("pop") is not None:
        foot += f"   precip {days[0]['pop']}%"
    draw.text((pad, H - 15), foot, font=det_f, fill=BLACK)


def _render_placeholder(draw, img, cfg, ctx, W, y):
    """Body for screens not built yet — centered, so a button press is visibly
    a different (intentional) screen, not a blank/broken one."""
    fonts = cfg.get("fonts", {})
    f = load_font(13, "regular", fonts.get("regular"))
    text = f"{ctx.get('title', 'screen')} — coming soon"
    tw, th = _text_size(draw, text, f)
    draw.text(((W - tw) // 2, y + 40), text, font=f, fill=BLACK)


_SCREEN_BODIES = {1: _render_screen1, 4: _render_screen4}
_SCREEN_LIST_BODIES = {1: _render_screen1_list, 4: _render_screen4_list}


def compose(cfg, ctx):
    """Full frame for the current screen: border + common header + screen body."""
    W, H = cfg["display"]["width"], cfg["display"]["height"]
    img = Image.new("1", (W, H), WHITE)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W - 1, H - 1], outline=BLACK)

    y_body = render_header(draw, cfg, ctx, W)
    screen, view = ctx.get("screen", 1), ctx.get("view", "base")
    if view == "list":
        body = _SCREEN_LIST_BODIES.get(screen, _render_placeholder)
    else:
        body = _SCREEN_BODIES.get(screen, _render_placeholder)
    body(draw, img, cfg, ctx, W, y_body)
    return img
