#!/usr/bin/env python3
"""
APRS symbol sprites for the e-ink panel.

Fetches the same 24x24 sheets the OASIS web map uses over localhost HTTP
(/map-assets/aprs-symbols-24-0.png = primary '/' table, -1.png = alternate '\\'
table), crops the cell for a symbol, and reduces it to 1-bit for the mono panel
(composite onto white, then threshold). Sheets and rendered cells are cached, so
only the first lookup of each touches the network — offline-safe thereafter.
"""

import urllib.request
from io import BytesIO

from PIL import Image, ImageDraw

CELL = 24
COLS = 16

_sheets = {}   # table char -> RGBA Image, or False once a fetch failed
_cache = {}    # (sym_table, sym_code, size, threshold) -> "1" Image or None


def _sheet(table, base_url, timeout):
    if table in _sheets:
        return _sheets[table] or None
    fname = "aprs-symbols-24-1.png" if table == "\\" else "aprs-symbols-24-0.png"
    img = None
    try:
        with urllib.request.urlopen(f"{base_url}/map-assets/{fname}", timeout=timeout) as r:
            img = Image.open(BytesIO(r.read())).convert("RGBA")
    except Exception:
        img = None
    _sheets[table] = img or False
    return img


def symbol_1bit(sym_table, sym_code, base_url, size=40, timeout=5, threshold=160):
    """A 1-bit (mode '1') APRS symbol at `size` px, or None if unavailable.

    Mirrors the map: the alternate sheet backs '\\' and overlay tables, with the
    overlay character stamped into the corner.
    """
    key = (sym_table, sym_code, size, threshold)
    if key in _cache:
        return _cache[key]

    overlay = sym_table not in ("/", "\\")
    sheet = _sheet("\\" if (overlay or sym_table == "\\") else "/", base_url, timeout)
    if sheet is None:
        _cache[key] = None
        return None

    idx = (ord(sym_code) - 33) if sym_code else 0
    if idx < 0 or idx > 95:
        idx = 0
    x0 = (idx % COLS) * CELL
    y0 = (idx // COLS) * CELL
    cell = sheet.crop((x0, y0, x0 + CELL, y0 + CELL)).convert("RGBA")

    # Composite onto white so transparent areas read as background, scale in
    # grayscale for smoother edges, then threshold to 1-bit.
    flat = Image.new("RGBA", cell.size, (255, 255, 255, 255))
    flat.alpha_composite(cell)
    gray = flat.convert("L")
    if size != CELL:
        gray = gray.resize((size, size), Image.LANCZOS)
    mono = gray.point(lambda p: 0 if p < threshold else 255, mode="1")

    if overlay:
        d = ImageDraw.Draw(mono)
        d.text((size - 9, size - 11), str(sym_table)[:1], fill=0)

    _cache[key] = mono
    return mono
