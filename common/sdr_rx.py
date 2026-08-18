"""common/sdr_rx.py — RTL-SDR receive primitives shared by every OASIS receiver.

Extracted from services/satellites/listen.py so a second receiver (NWR weather
radio, services/nwr/) does not copy dongle probing, arbitration and encoder
selection. Satellites keeps thin delegations, so its callers and tests are
unchanged.

DELIBERATELY NOT HERE: the rtl_fm command builder. Satellites resolves per-mode
demodulation (FM / CW / SSB plus the CW offset) through its own demod.py; NWR is
fixed FM at 22050 Hz forever. A builder whose parameters exist only to be ignored
by one caller is exactly the false symmetry listen.py already argues against for
its own two backends. Two literal lines of rtl_fm invocation is not duplication
worth paying that price for.

Nothing here imports demod, connector, capture, or predict. The Doppler/csdr
capture path is untouched by construction, not by care.
"""
import shutil
import subprocess
import threading
import time


def mhz_to_hz(freq_mhz):
    """MHz (float or str) -> integer Hz, which is what rtl_fm -f wants."""
    return int(round(float(freq_mhz) * 1_000_000))


def missing_deps(binaries, which=shutil.which):
    """Which of `binaries` are not on PATH, in the order given.

    Parameterised rather than fixed: satellites needs rtl_fm/sox/timeout,
    NWR needs rtl_fm/multimon-ng. `which` is injectable for tests.
    """
    return [b for b in binaries if not which(b)]


def gain_flag(gain):
    """argv fragment for a configured gain value: [] for automatic gain,
    ["-g", <value>] for a numeric one.

    rtl_fm parses -g with atof(), and atof() has no concept of the word
    "auto" -- it just reads a leading float and stops, so atof("auto") is
    0.0. `-g auto` was therefore running the dongle at 0 dB tuner gain, not
    at automatic gain, and rtl_fm's own stderr proves the two are different
    things: with `-g auto` it prints "Tuner gain set to 0.00 dB.", and with
    -g omitted entirely it prints "Tuner gain set to automatic." Measured on
    the Pi at 162.550 MHz: RMS 0.0244 / peak 0.123 with `-g auto` vs
    RMS 0.0772 / peak 0.498 with -g omitted -- a quarter of the level,
    peaking at 12% of full scale, quantisation-noise-dominated. rtl_power
    (scan.py) takes -g the same way and hit the identical bug, so this lives
    here rather than in listener.py: both listener.rtl_command() and
    scan.scan_command() need it, and services/nwr/ may not use bare sibling
    imports, so a helper in services/nwr/common/scan.py or listener.py could
    not be shared between them without one importing the other's module for
    an unrelated reason. common/sdr_rx.py already holds the RTL primitives
    both call into (missing_deps, stderr_summary, ...), so this is one more.

    Whoever is tempted to "fix" this by restoring `-g auto`: don't. There is
    no such rtl_fm keyword. Omitting -g is the only way to ask for real AGC.
    """
    text = "" if gain is None else str(gain).strip()
    if not text or text.lower() == "auto":
        return []
    return ["-g", text]


def stream_encoder(srate, which=shutil.which, gain_db=0):
    """(encoder_shell, mime) turning rtl_fm's raw s16le mono into a
    browser-playable stream on stdout. Chromium plays MP3; prefer ffmpeg
    (reliable libmp3lame) and fall back to sox (needs libsox-fmt-mp3). Returns
    (None, None) when neither can encode — callers report that as a state, not
    a failure.

    `gain_db` inserts a gain stage into the encode. IT DEFAULTS TO OFF, and the
    default must produce the command this function produced before the gain
    existed, byte for byte — satellites streams through here too (listen.py's
    stream_encoder/stream_command and routes.py's _stream_from_capture), and
    satellite audio is SSB, CW and FM at levels nothing below was measured on.
    Only NWR opts in.

    WHY NWR ASKS FOR 8 dB. FM-demodulated broadcast voice arrives well below
    full scale and this chain had no gain stage at all, so the weather relay was
    the quietest thing the station makes. Measured on pi5draws with
    `ffmpeg -af volumedetect`:

        NWR relay stream          mean -22.6 dB   peak -10.5 dB
        OASIS piper speech        mean -13.7 dB   peak  -0.0 dB

    9 dB below the station's own voice, with 10 dB of headroom left unused, so
    at any volume the operator picks the weather is about half as loud as the
    pass announcements. Candidates, run against 11 s of that same live capture:

        volume=8dB                        mean -14.6   peak -2.5
        volume=8dB,alimiter=limit=0.9     mean -13.6   peak -1.6   <- chosen
        volume=12dB,alimiter=limit=0.9    mean -10.2   peak  0.0
        dynaudnorm=f=250:g=15             mean -13.9   peak -0.7

    THE TARGET IS PARITY WITH OASIS SPEECH, NOT "LOUDER" — so a future change
    has a criterion instead of a preference. 8 dB behind a limiter lands within
    0.1 dB of the speech mean; 12 dB overshoots and touches the ceiling. The
    limiter is not decoration: `volume=8dB` alone peaks at -2.5 dB on THIS
    capture, and a stronger signal on another day would clip. dynaudnorm was
    rejected deliberately — it adapts to varying input but pumps on speech and
    costs more per sample, and a fixed gain behind a limiter is predictable and
    cheap enough for a Pi 3 on mono 22 kHz.

    What would legitimately change the number: re-measuring both sides the same
    way and finding the relay no longer matches speech — a different piper voice
    or a changed rtl_fm gain policy (see gain_flag) moves one end of the
    comparison. Turning the filter into a bare `volume=` or dropping it because
    it "looks redundant" does not.

    The sox fallback uses sox's own limiter form of `vol` (gain, then a limiter
    gain of 0.05) rather than a naked `gain`/`vol`, for the same reason ffmpeg
    gets alimiter: sox clips hard on peaks otherwise.
    """
    gain = float(gain_db or 0)
    if which("ffmpeg"):
        af = f' -af "volume={gain:g}dB,alimiter=limit=0.9"' if gain else ""
        return (f"ffmpeg -hide_banner -loglevel error -f s16le -ar {int(srate)} "
                f"-ac 1 -i -{af} -f mp3 -c:a libmp3lame -b:a 96k -", "audio/mpeg")
    if which("sox"):
        vol = f" vol {gain:g} dB 0.05" if gain else ""
        return (f"sox -t raw -r {int(srate)} -e signed-integer -b 16 -c 1 - "
                f"-t mp3 -C 96 -{vol}", "audio/mpeg")
    return (None, None)


# `rtl_test -t` takes up to 6 s; NWR's weather page polls /api/nwr/status
# every 5 s and both dashboards' health checks call it too, so an unthrottled
# probe could outlast index.html's own 4 s health-check budget and paint the
# chip TIMEOUT/DOWN with a dongle that is actually fine. 30 s is short enough
# that an unplugged dongle is never masked for more than one health-check cycle
# or two, and long enough that a Pi 3 isn't shelling out to rtl_test 3+ times a
# minute per open dashboard tab.
_PRESENCE_TTL_S = 30
_presence_lock = threading.Lock()
_presence_state = {"t": None, "present": False}


def _reset_presence_cache():
    """Test hook: forget the cached answer so the next dongle_present() call
    re-probes unconditionally."""
    with _presence_lock:
        _presence_state["t"] = None


def dongle_present(run=None, now=None, cache=False):
    """True if `rtl_test -t` reports at least one RTL-SDR device (Pi/Linux only).

    UNCACHED BY DEFAULT, deliberately. This function was extracted verbatim
    from satellites' listen.py, which probed on every call, and the cache
    arrived with the extraction. Satellites hard-refuses Listen with NO_DONGLE
    when this is False (services/satellites/routes.py), so a cached False meant
    plugging a dongle in and pressing Listen bought up to 30 s of "no RTL-SDR
    dongle detected" on a station where it used to work immediately — and one
    transient rtl_test timeout blocked the page for the rest of the window
    instead of for one call. Caching is a POLLING optimisation; a caller acting
    on the answer wants the truth.

    `cache=True` is for the pollers: /api/nwr/status is fetched every 5 s by the
    weather page and again by both dashboards' health checks, and those want a
    cheap recent answer, not a fresh 6 s shell-out apiece.

    The cache is process-local, never shared across processes. gunicorn runs
    this app as a single worker (--workers 1, see scripts/start-server.sh), so
    in practice there is only ever one cache to be surprised by.

    Two things are NEVER cached:

      * the exception path. A timeout or an OSError is one bad probe, not a
        measurement of the hardware, and remembering it for 30 s turns a blip
        into an outage;
      * any call with `run` injected. A fake answer that outlives its test
        poisons a process-global for whatever runs next — listen.preconditions
        (run=fake) would decide the real dongle question for the following test
        in the same process.

    `now` (default time.time()) is a parameter so the cache window is testable
    without sleeping for real.
    """
    cacheable = bool(cache) and run is None
    now = time.time() if now is None else now
    if cacheable:
        with _presence_lock:
            cached_t = _presence_state["t"]
            if cached_t is not None and (now - cached_t) < _PRESENCE_TTL_S:
                return _presence_state["present"]
    run_fn = run or subprocess.run
    try:
        from common.hardware_detect import parse_rtl_test_devices
        r = run_fn(["rtl_test", "-t"], capture_output=True, text=True, timeout=6)
        present = bool(parse_rtl_test_devices((r.stdout or "") + "\n" + (r.stderr or "")))
    except Exception:                                   # noqa: BLE001
        return False
    if cacheable:
        with _presence_lock:
            _presence_state["t"] = now
            _presence_state["present"] = present
    return present


def stderr_summary(stderr):
    """A short, actionable line for an `error` field, not a stderr dump.

    Shared by NWR's scan.py (rtl_power) and listener.py (rtl_fm) — both wrap
    the same rtl-sdr tools, both fail the same way, and both used to carry
    their own copy of this. A busy dongle's failure text is often multi-line
    and noisy; only the last non-blank line usually names the actual cause.
    `usb_claim_interface` is called out by name because a busy dongle --
    another service already holding it -- is the recurring failure mode on
    this hardware, and the generic last line ("usb_claim_interface error -6")
    means nothing to an operator who isn't reading libusb source.
    """
    text = (stderr or "").strip()
    if "usb_claim_interface" in text:
        return "another service is already using the RTL-SDR dongle"
    if not text:
        return "the process exited with an error and produced no output"
    return text.splitlines()[-1].strip()[:200]


def dongle_busy(inv, is_active, service):
    """(busy, holder) for the dongle assigned to `service`.

    Busy when ANOTHER co-assigned service holds it (its unit is-active). Our own
    capture is NOT "busy" — the caller tracks that separately, because holding
    the dongle ourselves is the working state, not a conflict. If `service` is
    unassigned or there is no inventory, fall back to the global SDR-consumer
    check so a bare dev box or a Pi without hardware.json still arbitrates
    sensibly.

    `service` is a parameter (it used to be hardcoded "satellites") because this
    is now shared with nwr.
    """
    from common import hardware
    from common.hardware_detect import sdr_services_active
    dev = inv.assignments.get(service) if inv else None
    if dev is None:
        holders = sdr_services_active(is_active)
        return (bool(holders), holders[0] if holders else None)
    for svc in hardware.assignees(inv, dev):
        if svc == service:
            continue
        if any(is_active(u) for u in hardware.service_units(inv, svc)):
            return True, svc
    return False, None
