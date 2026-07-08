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
    dw  = f"direwolf -t 0 -c {conf_path} -r {srate} -D 1 -"
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


# R820T/R820T2 tuner gain steps — fallback when rtl_test can't be queried.
STATIC_R820T_GAINS = [
    0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7,
    20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2,
    42.1, 43.4, 43.9, 44.5, 48.0, 49.6,
]


def parse_gains(rtl_test_output):
    """Extract the tuner's supported gain values from `rtl_test` output."""
    for line in rtl_test_output.splitlines():
        if "gain values" in line.lower():
            nums = re.findall(r"\d+\.\d+", line.split(":", 1)[-1])
            return [float(n) for n in nums]
    return []


def ppm_sweep_values(lo=-50, hi=50, step=5):
    return list(range(lo, hi + 1, step))


def rank_sweep(results, target_level=50):
    """Pick the best value from (value, decode_count, avg_level) rows.
    Rank by decode_count, tie-break by avg_level nearest target_level.
    Returns None when no row decoded anything (quiet band — don't guess)."""
    scored = [r for r in results if r[1] > 0]
    if not scored:
        return None
    return max(scored, key=lambda r: (r[1], -abs(r[2] - target_level)))[0]


def level_band(level):
    if level < 20:
        return "low"
    if level > 80:
        return "high"
    return "good"


def format_bar(level, width=27):
    level = max(0, min(100, level))
    filled = round(level / 100 * width)
    return "█" * filled + "░" * (width - filled)


# Matches features/rtl-sdr/enable-rtl-sdr.py: SAMPLE_RATE 48000, DATAGRAM 1920, port 7355.
def build_feed_command(freq, gain, ppm, port=7355):
    """The GrayWolf feed command as it belongs in aprs-sdr-feed.service.
    Always -s 48000 (GrayWolf's sample_rate) and no sox — only gain/ppm carry
    over from the bench (both RF-domain, sample-rate-independent)."""
    return (f"rtl_fm -f {freq} -M fm -s 48000 -g {gain} -p {ppm} - "
            f"| socat -u -b 1920 - UDP-SENDTO:127.0.0.1:{port}")


_REQUIRED_BINS = ("rtl_fm", "sox", "direwolf")


def check_deps(which):
    """Return the missing binaries (order: rtl_fm, sox, direwolf)."""
    return [b for b in _REQUIRED_BINS if not which.get(b)]


def deps_message(missing):
    return ("Missing required tools: " + ", ".join(missing) + "\n"
            "Install them with:  python3 features/rtl-sdr/install-rtl-sdr.py")


# Operator's known-good minimal Direwolf config. Audio comes from stdin at the
# selected rate via `-r <srate> -`, so the conf is rate-agnostic. LOGDIR points
# at the tool's temp dir so Direwolf CSV logs don't land in the operator's cwd.
SDR_CONF_TEMPLATE = """\
ACHANNELS 1
CHANNEL 0
MODEM 1200
LOGDIR {logdir}
"""
