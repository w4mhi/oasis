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


def record_command(freq_hz, out_wav, gain=DEFAULT_GAIN, ppm=DEFAULT_PPM,
                   srate=SAMPLE_RATE, max_seconds=MAX_SECONDS):
    """The rtl_fm | sox shell pipeline that records demodulated narrowband-FM
    audio to a WAV. Mirrors the proven aprs-sdr-feed rtl_fm invocation, piped to
    sox -> WAV instead of socat -> GrayWolf. `timeout` bounds the run so it always
    finalises the WAV. freq_hz is an integer Hz."""
    rtl = (f"timeout {int(max_seconds)} "
           f"rtl_fm -f {int(freq_hz)} -M fm -s {int(srate)} -g {gain} -p {ppm} -")
    sox = (f"sox -t raw -r {int(srate)} -e signed-integer -b 16 -c 1 - "
           f"{shlex.quote(out_wav)}")
    return f"{rtl} | {sox}"


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


# ── Downlink type support (v1 = FM family only) ──────────────────────────────
_SUPPORTED_MODES = ("fm", "fm voice", "voice", "aprs", "apt")
_MODE_BLURBS = {
    "apt": "NOAA/weather image (FM) — recorded now, decoded offline",
    "aprs": "APRS packet (FM) — recorded now, decoded offline",
    "fm voice": "FM voice audio",
    "voice": "FM voice audio",
    "fm": "FM voice audio",
    "lrpt": "digital image — record-only / offline decode (planned)",
    "ssb": "SSB / linear transponder — not yet supported",
    "usb": "SSB / linear transponder — not yet supported",
    "lsb": "SSB / linear transponder — not yet supported",
}


def mode_support(mode):
    """Classify a downlink `mode` string for v1. Supported = the FM family
    (rtl_fm -M fm captures it); everything else is shown-but-ghosted."""
    m = (mode or "").strip().lower()
    supported = m in _SUPPORTED_MODES
    return {"supported": supported, "demod": "fm" if supported else None,
            "blurb": _MODE_BLURBS.get(m, "mode not yet supported")}


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
            return is_recording()
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
    return {
        "missing_deps": missing,
        "dongle_present": (not missing) and dongle_present(run),
        "busy": busy,
        "holder": holder,
    }


# ── Capture manager — one dongle, one recording at a time ────────────────────
_lock = threading.Lock()
_state = {"proc": None, "norad": None, "path": None, "started": 0.0, "freq_hz": 0}


def is_recording():
    p = _state["proc"]
    return p is not None and p.poll() is None


def status():
    rec = is_recording()
    return {
        "recording": rec,
        "norad": _state["norad"] if rec else None,
        "file": os.path.basename(_state["path"]) if (rec and _state["path"]) else None,
        "seconds": round(time.time() - _state["started"]) if rec else 0,
        "freq_hz": _state["freq_hz"] if rec else 0,
    }


def start(freq_hz, norad, out_wav, gain=DEFAULT_GAIN, ppm=DEFAULT_PPM, srate=SAMPLE_RATE):
    """Begin recording to out_wav. Raises RuntimeError if already recording. The
    caller verifies deps/dongle/feed first (see preconditions)."""
    with _lock:
        if is_recording():
            raise RuntimeError("already recording")
        os.makedirs(os.path.dirname(out_wav), exist_ok=True)
        proc = subprocess.Popen(
            record_command(freq_hz, out_wav, gain, ppm, srate),
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid)   # own process group so stop() kills the whole pipe
        _state.update(proc=proc, norad=norad, path=out_wav,
                      started=time.time(), freq_hz=freq_hz)
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
        _state.update(proc=None)
        return {"recording": False}
