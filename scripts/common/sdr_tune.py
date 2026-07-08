"""Pure logic for the RTL-SDR APRS tuning bench (features/rtl-sdr/tune-rtl-sdr.py).

Everything here is import-safe and hardware-free so it can be unit-tested in CI.
The curses TUI, subprocess orchestration, and sweep driving live in the entry
point features/rtl-sdr/tune-rtl-sdr.py."""

import re
from collections import namedtuple


def build_pipeline(freq, gain, ppm, vol, srate, conf_path):
    """The rtl_fm | sox | direwolf shell pipeline. srate (24000/48000) drives
    rtl_fm -s, sox -r, and direwolf -r together — they must always match."""
    rtl = f"rtl_fm -M fm -f {freq} -s {srate} -F 0 -g {gain} -p {ppm} -"
    sox = (f"sox -t raw -r {srate} -e signed-integer -b 16 -c 1 - "
           f"-t raw - vol {vol}")
    dw  = f"direwolf -c {conf_path} -r {srate} -D 1 -"
    return f"{rtl} | {sox} | {dw}"


AudioLevel = namedtuple("AudioLevel", "level lo hi")
Decoded = namedtuple("Decoded", "src dest payload")

_AUDIO_RE = re.compile(r"audio level = (\d+)\((\d+)/(\d+)\)")
# [0.4] SRC>DEST,path:payload   — SRC/DEST are callsign-SSID tokens.
_PKT_RE = re.compile(
    r"^\[\d+(?:\.\d+)?\]\s+"
    r"([A-Z0-9]{1,6}(?:-\d{1,2})?)>([A-Z0-9]{1,6}(?:-\d{1,2})?)"
    r"[^:]*:(.*)$"
)


def parse_line(line):
    """Classify one line of Direwolf output. Returns AudioLevel, Decoded, or None."""
    m = _AUDIO_RE.search(line)
    if m:
        return AudioLevel(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _PKT_RE.match(line.strip())
    if m:
        return Decoded(m.group(1), m.group(2), m.group(3))
    return None


def score(events):
    """Given a window of events, return (decode_count, avg_audio_level)."""
    decodes = sum(1 for e in events if isinstance(e, Decoded))
    levels = [e.level for e in events if isinstance(e, AudioLevel)]
    avg = sum(levels) / len(levels) if levels else 0.0
    return decodes, avg
