"""RTL-SDR "listen" capture for the satellites feature (Phase 2).

Tunes rtl_fm to a selected satellite's downlink and records the pass audio to a
WAV file — e.g. to capture a NOAA/Meteor weather pass for offline APT/LRPT
decoding, or a voice/APRS bird. Only one process can own the dongle, so recording
requires the APRS SDR feed (aprs-sdr-feed.service) to be stopped first, exactly
like the tuning bench.

Record-first MVP. NOT yet implemented (next increments, and the riskiest untested
pieces): live browser audio streaming, and active Doppler retuning across the
pass. A fixed narrowband-FM tune already captures NOAA APT (the Doppler stays
within the passband).

The pure helpers below — command building, path building, dependency and dongle
checks — are unit-tested. The subprocess capture needs a real dongle and rtl_fm
on the Pi; it mirrors the proven aprs-sdr-feed invocation to maximise the chance
it works first try, but it is exercised on the Pi, not in CI.
"""
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time

SAMPLE_RATE = 48000       # rtl_fm -s — matches the proven aprs-sdr-feed build
MAX_SECONDS = 20 * 60     # safety cap: a forgotten recording can't run forever
DEFAULT_GAIN = "40"
DEFAULT_PPM = "0"


def recordings_dir(repo_root):
    """Where pass recordings are written (per-machine runtime data, gitignored)."""
    return os.path.join(repo_root, "configuration", "sat-recordings")


def mhz_to_hz(freq_mhz):
    """Roster downlinks are in MHz; rtl_fm -f wants Hz."""
    return int(round(float(freq_mhz) * 1_000_000))


CW_OFFSET_HZ = 700   # CW is tuned this far LOW so the carrier beats as an audible tone (USB)


# FM-demodulable modulations: analog FM voice, packet (AFSK/FSK/GMSK/MSK/GFSK),
# analog SSTV, and APT — all captured by `rtl_fm -M fm`.
_FM_TOK = {"FM", "NFM", "FMN", "AFSK", "FSK", "GFSK", "GMSK", "MSK", "SSTV", "APT", "APRS"}


def _mode_tokens(mode):
    # Modes are messy ("AFSK1k2", "GMSK USP", "FSK AX.100 Mode 5"); classify on
    # split tokens, not exact strings.
    return [t for t in re.split(r"[^A-Z0-9]+", (mode or "").upper()) if t]


def demod_params(dmode):
    """(rtl_fm mode, sample rate, tuning offset Hz) for a downlink mode, or
    (None, None, None) when we can't demodulate it live (LRPT / DVB / PSK …).

    CW → USB tuned 700 Hz low, so the carrier lands as an audible ~700 Hz tone
    (glides in pitch with Doppler — expected for a beacon). USB/SSB → narrow USB;
    LSB → narrow LSB; the FM family → wide FM (48 kHz)."""
    toks = _mode_tokens(dmode)

    def starts(prefixes):
        # startswith, not equality: SatNOGS glues the baud on ("AFSK1k2", "GMSK4k8").
        return any(t.startswith(p) for t in toks for p in prefixes)

    if starts(("CW",)):               return ("usb", 12000, -CW_OFFSET_HZ)
    if starts(("LSB",)):              return ("lsb", 12000, 0)
    if starts(("USB", "SSB")):        return ("usb", 12000, 0)
    if starts(tuple(_FM_TOK)):        return ("fm", SAMPLE_RATE, 0)
    return (None, None, None)


def record_command(freq_hz, out_wav, gain=DEFAULT_GAIN, ppm=DEFAULT_PPM,
                   srate=None, max_seconds=MAX_SECONDS, device_serial=None, dmode="fm"):
    """The rtl_fm | sox shell pipeline that records demodulated audio to a WAV.
    Mirrors the proven aprs-sdr-feed rtl_fm invocation, piped to sox -> WAV instead
    of socat -> GrayWolf. `timeout` bounds the run so it always finalises the WAV.
    freq_hz is an integer Hz; the demod + rate + CW offset come from dmode
    (see demod_params). `srate` overrides the per-mode default when given.

    device_serial pins rtl_fm to a specific dongle via `-d <serial>` (same as the
    APRS feed's aprs_feed_device_args). Without it, rtl_fm defaults to device
    index 0 — which on a multi-dongle Pi is often another service's dongle (e.g.
    ADS-B/dump1090), so it can't claim it and dies immediately."""
    rmode, rate, offset = demod_params(dmode)
    if rmode is None:            # unsupported mode slipped past the route → default FM
        rmode, rate, offset = "fm", SAMPLE_RATE, 0
    if srate:
        rate = int(srate)
    dev = f"-d {shlex.quote(str(device_serial))} " if device_serial else ""
    rtl = (f"timeout {int(max_seconds)} "
           f"rtl_fm {dev}-f {int(freq_hz) + offset} -M {rmode} -s {int(rate)} -g {gain} -p {ppm} -")
    sox = (f"sox -t raw -r {int(rate)} -e signed-integer -b 16 -c 1 - "
           f"{shlex.quote(out_wav)}")
    return f"{rtl} | {sox}"


def stream_encoder(srate, which=shutil.which):
    """(encoder_shell, mime) that turns rtl_fm's raw s16le mono into a
    browser-playable stream on stdout. Chromium plays MP3; prefer ffmpeg
    (reliable libmp3lame) and fall back to sox (needs libsox-fmt-mp3). Returns
    (None, None) when neither can encode — the /stream route reports that."""
    if which("ffmpeg"):
        return (f"ffmpeg -hide_banner -loglevel error -f s16le -ar {int(srate)} "
                f"-ac 1 -i - -f mp3 -c:a libmp3lame -b:a 96k -", "audio/mpeg")
    if which("sox"):
        return (f"sox -t raw -r {int(srate)} -e signed-integer -b 16 -c 1 - "
                f"-t mp3 -C 96 -", "audio/mpeg")
    return (None, None)


def stream_command(freq_hz, gain=DEFAULT_GAIN, ppm=DEFAULT_PPM, srate=None,
                   device_serial=None, which=shutil.which, dmode="fm"):
    """rtl_fm | <encoder> → browser audio on stdout. Same capture as
    record_command (per-mode demod via dmode — FM / CW / SSB), but the audio is
    encoded (MP3) for a live <audio> element instead of a WAV, and no `timeout` so
    it runs for the connection's lifetime. Returns (shell_cmd, mime), or
    (None, None) when no encoder is available."""
    rmode, rate, offset = demod_params(dmode)
    if rmode is None:            # unsupported mode slipped past the route → default FM
        rmode, rate, offset = "fm", SAMPLE_RATE, 0
    if srate:
        rate = int(srate)
    enc, mime = stream_encoder(rate, which=which)
    if not enc:
        return (None, None)
    dev = f"-d {shlex.quote(str(device_serial))} " if device_serial else ""
    rtl = f"rtl_fm {dev}-f {int(freq_hz) + offset} -M {rmode} -s {int(rate)} -g {gain} -p {ppm} -"
    return (f"{rtl} | {enc}", mime)


def missing_deps(which=shutil.which):
    """Binaries needed for recording that aren't on PATH (rtl_fm, sox, timeout)."""
    return [b for b in ("rtl_fm", "sox", "timeout") if not which(b)]


def dongle_present(run=None):
    """True if `rtl_test -t` reports at least one RTL-SDR device (Pi/Linux only)."""
    run = run or subprocess.run
    try:
        from common.hardware_detect import parse_rtl_test_devices
        r = run(["rtl_test", "-t"], capture_output=True, text=True, timeout=6)
        return bool(parse_rtl_test_devices((r.stdout or "") + "\n" + (r.stderr or "")))
    except Exception:
        return False


# ── Downlink type support (FM family + CW/SSB) ───────────────────────────────
_MODE_BLURBS = {
    "apt": "NOAA/weather image (FM) — recorded now, decoded offline",
    "aprs": "APRS packet (FM) — hear the packet chirp; decode offline",
    "afsk": "APRS / packet (AFSK over FM) — hear the chirp",
    "sstv": "SSTV image (FM) — hear the warble; decode offline",
    "fm voice": "FM voice audio",
    "voice": "FM voice audio",
    "fm": "FM voice audio",
    "cw": "CW / Morse — heard as a tone (USB); pitch glides with Doppler",
    "ssb": "SSB voice (USB) — narrowband",
    "usb": "SSB voice (USB) — narrowband",
    "lsb": "SSB voice (LSB) — narrowband",
    "lrpt": "digital image — record-only / offline decode (planned)",
}
_DEMOD_BLURB = {"fm": "FM-family — hearable now", "usb": "SSB / USB — narrowband",
                "lsb": "SSB / LSB — narrowband"}


def mode_support(mode):
    """Classify a downlink `mode` string. Supported = anything demod_params can
    handle live: the FM family (voice / packet / SSTV / APT via -M fm) plus CW/SSB
    (-M usb/lsb). `demod` is the actual rtl_fm mode; everything else is ghosted."""
    rmode, _, _ = demod_params(mode)
    m = (mode or "").strip().lower()
    blurb = _MODE_BLURBS.get(m) or _DEMOD_BLURB.get(rmode) or "mode not yet supported"
    return {"supported": rmode is not None, "demod": rmode, "blurb": blurb}


def is_active_wrapper(base_is_active=None):
    """Wrap systemctl is-active so the SYNTHETIC "satellites-listen" unit is
    answered from our own recorder state — this is the recorder→engine bridge
    that lets device_states()/dongle_busy() see satellites as a claimant even
    though it is not a systemd unit (see hardware.SERVICE_UNITS)."""
    if base_is_active is None:
        from common.hardware import _default_is_active
        base_is_active = _default_is_active
    def _wrapped(unit):
        if unit == "satellites-listen":
            return is_capturing()   # record OR live stream both hold the dongle
        return base_is_active(unit)
    return _wrapped


def dongle_busy(inv, is_active):
    """(busy, holder) for the dongle assigned to satellites. Busy when ANOTHER
    co-assigned service holds it (its unit is-active). Our own recording is not
    "busy" (that's the red REC state, tracked via is_recording). If satellites
    is unassigned or there's no inventory, fall back to the global SDR-consumer
    check so a bare dev/Pi without hardware.json still arbitrates sensibly."""
    from common import hardware
    from common.hardware_detect import sdr_services_active
    dev = inv.assignments.get("satellites") if inv else None
    if dev is None:
        holders = sdr_services_active(is_active)
        return (bool(holders), holders[0] if holders else None)
    for svc in hardware.assignees(inv, dev):
        if svc == "satellites":
            continue
        if any(is_active(u) for u in hardware.service_units(inv, svc)):
            return True, svc
    return False, None


def preconditions(which=shutil.which, run=None, is_active=None, inv=None):
    """Everything the UI needs to decide whether listening is possible right now.
    Pure of side effects; each check is independently injectable for tests.
    `is_active` defaults to the recorder-aware wrapper; `inv` is the hardware
    inventory (None → global SDR-consumer fallback)."""
    missing = missing_deps(which)
    eff_is_active = is_active if is_active is not None else is_active_wrapper()
    busy, holder = dongle_busy(inv, eff_is_active)
    dev_id = inv.assignments.get("satellites") if inv else None
    dev = inv.devices.get(dev_id) if (inv and dev_id) else None
    return {
        "missing_deps": missing,
        "dongle_present": (not missing) and dongle_present(run),
        # Whether satellites has its OWN device assignment (Setup → Hardware).
        # Unassigned → the UI shows "unassigned" rather than the global-fallback
        # holder (busy/holder still arbitrate recording; see dongle_busy).
        "assigned": bool(dev_id),
        # Assigned device's display label (e.g. "RTL-SDR (00000001)") so the card
        # can show the device identity like the dashboard, not just "free".
        "device": (dev.get("label") if dev else None),
        "busy": busy,
        "holder": holder,
    }


# ── Capture manager — one dongle, one recording at a time ────────────────────
_lock = threading.Lock()
_state = {"proc": None, "norad": None, "path": None, "started": 0.0,
          "freq_hz": 0, "mode": None}   # mode: "record" (→WAV) | "stream" (→browser)


def is_capturing():
    """True while the dongle is held by our own capture (record OR live stream)."""
    p = _state["proc"]
    return p is not None and p.poll() is None


def is_recording():
    """True only for a to-WAV recording (not a live stream) — the red REC state."""
    return is_capturing() and _state.get("mode") == "record"


def is_streaming():
    """True while a live browser stream holds the dongle."""
    return is_capturing() and _state.get("mode") == "stream"


def status():
    cap = is_capturing()
    return {
        "recording": is_recording(),
        "streaming": is_streaming(),
        "mode": _state["mode"] if cap else None,
        "norad": _state["norad"] if cap else None,
        "file": os.path.basename(_state["path"]) if (cap and _state["path"]) else None,
        "seconds": round(time.time() - _state["started"]) if cap else 0,
        "freq_hz": _state["freq_hz"] if cap else 0,
    }


def start(freq_hz, norad, out_wav, gain=DEFAULT_GAIN, ppm=DEFAULT_PPM,
          srate=None, device_serial=None, dmode="fm"):
    """Begin recording to out_wav. Raises RuntimeError if already recording. The
    caller verifies deps/dongle/feed first (see preconditions). device_serial
    pins rtl_fm to the dongle assigned to satellites; dmode selects the demod
    (see record_command / demod_params)."""
    with _lock:
        if is_recording():
            raise RuntimeError("already recording")
        os.makedirs(os.path.dirname(out_wav), exist_ok=True)
        proc = subprocess.Popen(
            record_command(freq_hz, out_wav, gain, ppm, srate, device_serial=device_serial, dmode=dmode),
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid)   # own process group so stop() kills the whole pipe
        _state.update(proc=proc, norad=norad, path=out_wav,
                      started=time.time(), freq_hz=freq_hz, mode="record")
        return status()


def stop():
    """Stop the current recording (SIGTERM the whole pipeline). Idempotent."""
    with _lock:
        p = _state["proc"]
        if p is not None and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                p.terminate()
            try:
                p.wait(timeout=3)
            except Exception:
                pass
        _state.update(proc=None, mode=None)
        return {"recording": False}


def stream(freq_hz, norad, gain=DEFAULT_GAIN, ppm=DEFAULT_PPM, srate=None,
           device_serial=None, chunk=8192, dmode="fm"):
    """Start a live capture and return (generator, mime). The generator yields
    encoded audio bytes for a browser <audio> element; it holds the dongle for
    the connection and SIGTERMs the whole pipeline when the client disconnects.
    Mutually exclusive with recording. dmode selects the demod (FM / CW / SSB).
    Raises RuntimeError if busy / no encoder."""
    cmd, mime = stream_command(freq_hz, gain, ppm, srate, device_serial=device_serial, dmode=dmode)
    if not cmd:
        raise RuntimeError("no audio encoder — install ffmpeg (or libsox-fmt-mp3)")
    with _lock:
        if is_capturing():
            raise RuntimeError("dongle busy")
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid)   # own process group so we can kill the whole pipe
        _state.update(proc=proc, norad=norad, path=None,
                      started=time.time(), freq_hz=freq_hz, mode="stream")

    def _generate():
        try:
            while True:
                data = proc.stdout.read(chunk)
                if not data:
                    break
                yield data
        finally:
            with _lock:
                if _state["proc"] is proc:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        pass
                    _state.update(proc=None, mode=None)
    return _generate(), mime
