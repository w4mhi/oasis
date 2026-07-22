"""TLE cache: parse, load, and freshness. Read-only at runtime — the cache is
populated out-of-band by build-roster.py (online). No network here."""
import os
import time

# CelesTrak groups the roster draws from. Fetched by build-roster.py.
GROUPS = {
    "weather":  "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle",
    "amateur":  "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle",
    "stations": "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle",
    "cubesat":  "https://celestrak.org/NORAD/elements/gp.php?GROUP=cubesat&FORMAT=tle",
}

# High-value APT weather birds that CelesTrak lists in NO bulk group, so the
# group pulls above miss them. build-roster.py gap-fills these individually via
# CATNR (a handful of extra requests) and writes them to the cache as extra.txt.
CATNR_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={}&FORMAT=tle"
GAPFILL_NORADS = (25338, 28654, 33591)  # NOAA 15 / 18 / 19 (137 MHz APT)


def parse_tle_text(text):
    """Parse 3-line TLE blocks into {name: (line1, line2)}. Tolerant of blank
    lines and trailing whitespace; ignores malformed trailing fragments."""
    out = {}
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    i = 0
    while i + 2 < len(lines) + 1 and i + 2 <= len(lines):
        name, l1, l2 = lines[i], lines[i + 1] if i + 1 < len(lines) else "", \
            lines[i + 2] if i + 2 < len(lines) else ""
        if l1.startswith("1 ") and l2.startswith("2 "):
            out[name.strip()] = (l1, l2)
            i += 3
        else:
            i += 1
    return out


def load_cache(cache_dir):
    """Merge every *.txt group file in the cache dir into one name→lines dict."""
    out = {}
    if not os.path.isdir(cache_dir):
        return out
    for fn in sorted(os.listdir(cache_dir)):
        if fn.endswith(".txt"):
            with open(os.path.join(cache_dir, fn), encoding="utf-8") as fh:
                out.update(parse_tle_text(fh.read()))
    return out


def cache_age_days(cache_dir):
    """Age (days) of the freshest group file, or None if the cache is empty."""
    if not os.path.isdir(cache_dir):
        return None
    mtimes = [os.path.getmtime(os.path.join(cache_dir, fn))
              for fn in os.listdir(cache_dir) if fn.endswith(".txt")]
    if not mtimes:
        return None
    return (time.time() - max(mtimes)) / 86400.0


def cache_mtime(cache_dir):
    """Newest group-file mtime (epoch seconds), or None if empty. Stable across
    repeated calls (unlike cache_age_days) — suitable as a cache-version key so a
    TLE refresh invalidates cached results but back-to-back requests reuse them."""
    if not os.path.isdir(cache_dir):
        return None
    mtimes = [os.path.getmtime(os.path.join(cache_dir, fn))
              for fn in os.listdir(cache_dir) if fn.endswith(".txt")]
    return max(mtimes) if mtimes else None


def norad_of(line1):
    """NORAD catalog number from TLE line 1 (columns 3-7), or None if unparseable."""
    try:
        return int(line1[2:7])
    except (ValueError, IndexError):
        return None


def index_by_norad(cache):
    """Re-index a name->(l1,l2) cache as {norad_int: (name, l1, l2)}. Matching by
    NORAD id is robust against the roster's clean names not matching the verbose
    names CelesTrak uses (e.g. roster 'SO-50' vs cache 'SAUDISAT 1C (SO-50)')."""
    out = {}
    for name, (l1, l2) in cache.items():
        n = norad_of(l1)
        if n is not None:
            out[n] = (name, l1, l2)
    return out
