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


def mhz_to_hz(freq_mhz):
    """MHz (float or str) -> integer Hz, which is what rtl_fm -f wants."""
    return int(round(float(freq_mhz) * 1_000_000))


def missing_deps(binaries, which=shutil.which):
    """Which of `binaries` are not on PATH, in the order given.

    Parameterised rather than fixed: satellites needs rtl_fm/sox/timeout,
    NWR needs rtl_fm/multimon-ng. `which` is injectable for tests.
    """
    return [b for b in binaries if not which(b)]


def stream_encoder(srate, which=shutil.which):
    """(encoder_shell, mime) turning rtl_fm's raw s16le mono into a
    browser-playable stream on stdout. Chromium plays MP3; prefer ffmpeg
    (reliable libmp3lame) and fall back to sox (needs libsox-fmt-mp3). Returns
    (None, None) when neither can encode — callers report that as a state, not
    a failure."""
    if which("ffmpeg"):
        return (f"ffmpeg -hide_banner -loglevel error -f s16le -ar {int(srate)} "
                f"-ac 1 -i - -f mp3 -c:a libmp3lame -b:a 96k -", "audio/mpeg")
    if which("sox"):
        return (f"sox -t raw -r {int(srate)} -e signed-integer -b 16 -c 1 - "
                f"-t mp3 -C 96 -", "audio/mpeg")
    return (None, None)


def dongle_present(run=None):
    """True if `rtl_test -t` reports at least one RTL-SDR device (Pi/Linux only)."""
    run = run or subprocess.run
    try:
        from common.hardware_detect import parse_rtl_test_devices
        r = run(["rtl_test", "-t"], capture_output=True, text=True, timeout=6)
        return bool(parse_rtl_test_devices((r.stdout or "") + "\n" + (r.stderr or "")))
    except Exception:                                   # noqa: BLE001
        return False


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
