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
    # -a 2: Direwolf prints an audio-device stats line every 2 s (approx sample
    # rate, error count, continuous receive level) regardless of decodes — the
    # bench's "audio flowing" heartbeat. Parsed by parse_line into AudioStat.
    dw  = f"direwolf -t 0 -a 2 -c {conf_path} -r {srate} -D 1 -"
    return f"{rtl} | {sox} | {dw}"


AudioLevel = namedtuple("AudioLevel", "level lo hi")
Decoded = namedtuple("Decoded", "src dest payload")
# Direwolf -a periodic stats: "ADEVICE0: Sample rate approx. 24.6 k, 0 errors,
# receive audio level CH0 49". A decode-independent heartbeat + health signal.
AudioStat = namedtuple("AudioStat", "rate_k errors level")

_AUDIO_RE = re.compile(r"audio level = (\d+)\((\d+)/(\d+)\)")
_ASTAT_RE = re.compile(
    r"Sample rate approx\.\s*([\d.]+)\s*k,\s*(\d+)\s*errors,"
    r"\s*receive audio level CH\d+\s+(\d+)"
)
# [0.4] SRC>DEST,path:payload   — SRC/DEST are callsign-SSID tokens.
_PKT_RE = re.compile(
    r"^\[\d+(?:\.\d+)?\]\s+"
    r"([A-Z0-9]{1,6}(?:-\d{1,2})?)>([A-Z0-9]{1,6}(?:-\d{1,2})?)"
    r"[^:]*:(.*)$"
)


def parse_line(line):
    """Classify one line of Direwolf output. Returns AudioLevel, AudioStat,
    Decoded, or None."""
    m = _ASTAT_RE.search(line)
    if m:
        return AudioStat(float(m.group(1)), int(m.group(2)), int(m.group(3)))
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


# Accepts operator-typed frequencies like "144.390M", "144800k", "144800000".
# rtl_fm's -f parser (atofs) understands a trailing k/M/G suffix; we canonicalise
# to that spelling and range-check against the RTL2832U's tuning span so a typo
# ("14.4390M") is rejected in the TUI instead of silently killing the pipeline.
_FREQ_RE = re.compile(r"^\d+(?:\.\d+)?[kKmMgG]?$")
_SUFFIX_HZ = {"k": 1e3, "M": 1e6, "G": 1e9}
_RTL_MIN_HZ, _RTL_MAX_HZ = 24e6, 1_766e6


def _freq_to_hz(canon):
    if canon and canon[-1] in _SUFFIX_HZ:
        return float(canon[:-1]) * _SUFFIX_HZ[canon[-1]]
    return float(canon)


def normalize_freq(text):
    """Validate a user-entered frequency for rtl_fm -f. Return the canonical
    string (e.g. "144.800M") or None if it isn't a plausible RTL-SDR frequency."""
    t = (text or "").strip().replace(" ", "")
    if not _FREQ_RE.match(t):
        return None
    if t[-1] in "kKmMgG":
        t = t[:-1] + {"K": "k", "M": "M", "G": "G"}[t[-1].upper()]
    try:
        hz = _freq_to_hz(t)
    except ValueError:
        return None
    if not (_RTL_MIN_HZ <= hz <= _RTL_MAX_HZ):
        return None
    return t


def ppm_sweep_values(lo=-50, hi=50, step=5):
    return list(range(lo, hi + 1, step))


def rank_sweep(results, target_level=50, min_decodes=1):
    """Pick the best value from (value, decode_count, avg_level) rows.
    Only rows with at least `min_decodes` decodes are eligible (a step must
    clear the confidence bar to count). Rank by decode_count, tie-break by
    avg_level nearest target_level. Returns None when no row qualifies
    (quiet/too-sparse band — don't guess)."""
    scored = [r for r in results if r[1] >= min_decodes]
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


# libusb error -6 (LIBUSB_ERROR_BUSY): the dongle is already claimed by another
# process (aprs-sdr-feed.service, a stray rtl_fm/rtl_tcp) or by the kernel DVB-T
# driver. When rtl_fm can't open the device it dies immediately, the pipeline
# collapses, and the *last* stage (direwolf) exits 0 on EOF — masking the real
# cause. Probing rtl_test before launching the TUI surfaces it up front.
_BUSY_MARKERS = ("usb_claim_interface error", "Failed to open rtlsdr device")
_ABSENT_MARKERS = ("No supported devices found", "usb_open error")


def device_error(rtl_test_output):
    """Inspect `rtl_test -t` output. Return "busy", "absent", or None (usable)."""
    if any(m in rtl_test_output for m in _BUSY_MARKERS):
        return "busy"
    if any(m in rtl_test_output for m in _ABSENT_MARKERS):
        return "absent"
    return None


def device_help(reason):
    """Actionable fix steps for a device_error() reason."""
    if reason == "busy":
        return (
            "RTL-SDR dongle is BUSY — another process or the kernel DVB-T driver "
            "already owns it.\n"
            "  1. Stop the feed service:  sudo systemctl stop aprs-sdr-feed.service\n"
            "  2. Kill any stray tuner:   pkill -f rtl_fm ; pkill -f rtl_tcp\n"
            "  3. If still busy, unload the DVB-T driver:\n"
            "       sudo modprobe -r dvb_usb_rtl28xxu\n"
            "     make it permanent: echo 'blacklist dvb_usb_rtl28xxu' | "
            "sudo tee /etc/modprobe.d/blacklist-rtl.conf\n"
            "Then re-run this tool.")
    return (
        "No RTL-SDR dongle detected. Check the USB connection, then run "
        "`rtl_test -t` to confirm the device enumerates.")


# Operator's known-good minimal Direwolf config. Audio comes from stdin at the
# selected rate via `-r <srate> -`, so the conf is rate-agnostic. LOGDIR points
# at the tool's temp dir so Direwolf CSV logs don't land in the operator's cwd.
# ADEVICE '- null': receive from stdin, transmit to the ALSA null device. The
# bench is receive-only, but Direwolf 1.7 refuses to start unless it can open an
# audio *output* device — on a headless Pi with no audio configured, the default
# device fails ("Could not open audio device default for output"). null always
# opens, so the bench works regardless of the box's audio setup.
SDR_CONF_TEMPLATE = """\
ADEVICE - null
ACHANNELS 1
CHANNEL 0
MODEM 1200
LOGDIR {logdir}
"""
