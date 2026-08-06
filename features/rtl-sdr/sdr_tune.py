"""Pure logic for the RTL-SDR APRS tuning bench (features/rtl-sdr/tune-rtl-sdr.py).

Everything here is import-safe and hardware-free so it can be unit-tested in CI.
The curses TUI, subprocess orchestration, and sweep driving live in the entry
point features/rtl-sdr/tune-rtl-sdr.py."""

import re
from collections import namedtuple


def build_pipeline(freq, gain, ppm, vol, srate, conf_path, fir=0, device=None):
    """The rtl_fm | sox | direwolf shell pipeline. srate (24000/48000) drives
    rtl_fm -s, sox -r, and direwolf -r together — they must always match.

    fir is rtl_fm's -F down-sample FIR: 0 = off (plain decimation), 9 = the
    9-tap low-leakage filter (cleaner passband, less aliasing, more CPU). Only
    0 and 9 are meaningful to rtl_fm, but the bench exposes the whole 0–9 range
    so the operator can A/B any value against the live decode count.

    device selects WHICH dongle when several are attached: an rtl_fm -d value,
    which librtlsdr resolves as either a numeric index or a serial string. None
    (the default) omits -d entirely — rtl_fm falls back to index 0, preserving
    the original single-dongle behaviour byte-for-byte."""
    dev = f" -d {device}" if device is not None else ""
    rtl = f"rtl_fm{dev} -M fm -f {freq} -s {srate} -F {fir} -g {gain} -p {ppm} -"
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


# Matches services/rtl-feed/common/feed.py: SAMPLE_RATE 48000, DATAGRAM 1920, port 7355.
def build_feed_command(freq, gain, ppm, fir=0, port=7355):
    """The GrayWolf feed command as it belongs in aprs-sdr-feed.service.
    Always -s 48000 (GrayWolf's sample_rate) and no sox — only gain/ppm/fir
    carry over from the bench (all RF/decimation-domain, sample-rate-agnostic).
    fir is rtl_fm's -F down-sample FIR; only emitted when non-zero so the default
    command is unchanged (note: -F 9 costs CPU — measure it on the Pi first)."""
    fir_opt = f"-F {fir} " if fir else ""
    return (f"rtl_fm -f {freq} -M fm -s 48000 {fir_opt}-g {gain} -p {ppm} - "
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
            "  1. If a SECOND dongle is attached, target it instead of freeing "
            "this one:\n"
            "       rtl_test            # list dongles + their index/serial\n"
            "       ./tune-rtl-sdr.py --device 1   # or --device <serial>\n"
            "  2. Otherwise stop whatever holds it:\n"
            "       sudo systemctl stop aprs-sdr-feed.service   # APRS RX feed\n"
            "       sudo systemctl stop dump1090-fa.service     # ADS-B decoder\n"
            "  3. Kill any stray tuner:   pkill -f rtl_fm ; pkill -f rtl_tcp\n"
            "  4. If still busy, unload the DVB-T driver:\n"
            "       sudo modprobe -r dvb_usb_rtl28xxu\n"
            "     make it permanent: echo 'blacklist dvb_usb_rtl28xxu' | "
            "sudo tee /etc/modprobe.d/blacklist-rtl.conf\n"
            "Then re-run this tool.")
    return (
        "No RTL-SDR dongle detected. Check the USB connection, then run "
        "`rtl_test -t` to confirm the device enumerates.")


# rtl_test prints an enumeration header before it ever opens a device, so it
# lists EVERY attached dongle (index + description + serial) even when device 0
# is busy — exactly what a "which dongle?" picker needs. Lines look like:
#     Found 2 device(s):
#       0:  Realtek, RTL2832U, SN: 00001000
#       1:  RTLSDRBlog, Blog V4, SN: 00000001
# The "  N:  desc" rows are the only lines that start (after indent) with an
# integer + colon, so this pattern can't collide with "Found …", "Using …", or
# the "Supported gain values" line.
_DEVICE_LINE = re.compile(r"^\s*(\d+):\s+(.+?)\s*$")
_DEVICE_SN = re.compile(r",?\s*SN:\s*(\S+)$")


def parse_devices(rtl_test_output):
    """Parse rtl_test's device list into an ordered list of
    {"index": int, "name": str, "serial": str}. The serial is "" on older
    librtlsdr builds that omit the SN suffix. Returns [] when nothing
    enumerates (no dongle, or rtl_test unavailable)."""
    devices = []
    for line in rtl_test_output.splitlines():
        m = _DEVICE_LINE.match(line)
        if not m:
            continue
        rest, serial = m.group(2), ""
        sn = _DEVICE_SN.search(rest)
        if sn:
            serial = sn.group(1)
            rest = rest[:sn.start()].rstrip().rstrip(",").rstrip()
        devices.append({"index": int(m.group(1)), "name": rest, "serial": serial})
    return devices


def format_device_menu(devices):
    """Render the dongle picker shown before the TUI launches."""
    lines = ["Attached RTL-SDR dongles:"]
    for d in devices:
        sn = f"   SN {d['serial']}" if d["serial"] else ""
        lines.append(f"  [{d['index']}] {d['name']}{sn}")
    return "\n".join(lines)


def parse_menu_choice(raw, devices):
    """Map a picker keystroke to a device index. Empty input selects the first
    dongle (the Enter default); a bare integer must match a listed index.
    Returns the chosen index (int) or None when the entry isn't a listed
    dongle, so the caller can re-prompt."""
    valid = {d["index"] for d in devices}
    raw = (raw or "").strip()
    if raw == "":
        return devices[0]["index"] if devices else None
    if raw.lstrip("-").isdigit() and int(raw) in valid:
        return int(raw)
    return None


def resolve_device(devices, target):
    """Pick the parsed device entry the pipeline will actually open. target is
    the resolved --device value: None (default index 0), a device-index string,
    or a dongle serial. Returns the matching {"index","name","serial"} entry, or
    None when nothing enumerated / the target names no listed dongle (so the
    caller can still launch but shows an unresolved dongle line)."""
    if not devices:
        return None
    if target is None:
        return devices[0]
    t = str(target).strip()
    for d in devices:
        if str(d["index"]) == t or d["serial"] == t:
            return d
    return None


# librtlsdr dongles are Realtek RTL2832U front-ends: USB vendor 0bda, product
# 2832 or 2838 (Blog V4 included). Some rebrands report only via the product
# string, so a name containing "RTL" also qualifies. Used to filter sysfs USB
# devices down to plausible dongles before matching one to the opened tuner.
_RTL_VID = "0bda"
_RTL_PIDS = {"2832", "2838"}


def is_rtl_usb(vid, pid, name=""):
    """Does a USB device look like an RTL-SDR dongle? vid/pid are the lowercase
    hex ids from sysfs (idVendor/idProduct); name is the USB product string."""
    if (vid or "").lower() == _RTL_VID and (pid or "").lower() in _RTL_PIDS:
        return True
    return "rtl" in (name or "").lower()


def match_usb_port(records, serial):
    """Find the USB port path (e.g. "1-1.4") of the dongle with this serial.
    records is read_usb_devices()' output: [{"port","vid","pid","serial","name"}].
    Joins on serial because that's the only stable key librtlsdr and sysfs share.
    Returns the port, or None when the serial is blank, matches no RTL device, or
    matches more than one (generic "00000001" serials collide — don't guess a
    port that could be wrong; the caller renders "USB ?")."""
    s = (serial or "").strip()
    if not s:
        return None
    hits = [r for r in records
            if (r.get("serial") or "").strip() == s
            and is_rtl_usb(r.get("vid", ""), r.get("pid", ""), r.get("name", ""))]
    return hits[0]["port"] if len(hits) == 1 else None


def format_dongle(entry, port):
    """One-line dongle identity for the TUI header + startup banner:
    "[1] RTLSDRBlog, Blog V4   SN 00000001   USB 1-1.4". entry is a
    resolve_device() result (or None when nothing enumerated); port is
    match_usb_port()'s answer (or None → "USB ?")."""
    if entry:
        idx, name, sn = entry["index"], entry["name"], entry.get("serial", "")
    else:
        idx, name, sn = 0, "RTL-SDR (default)", ""
    sn_txt = sn if sn else "—"
    port_txt = port if port else "?"
    return f"[{idx}] {name}   SN {sn_txt}   USB {port_txt}"


# Operator's known-good minimal Direwolf config. Audio comes from stdin at the
# selected rate via `-r <srate> -`, so the conf is rate-agnostic. It is written
# into the operator's working directory as rtl-sdr-direwolf.conf (reused if
# already present) so it is easy to inspect while the session is running. No
# LOGDIR is set — the bench reads decodes off Direwolf's stdout, so leaving it
# out keeps the conf generic and avoids baking a machine-specific path into it.
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
"""
