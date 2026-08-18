"""Sweep the NWR band and report which of the seven channels is actually there.

rtl_power is the instrument. Measuring RMS on demodulated audio is NOT a
substitute — an empty channel demodulates to full-scale noise and reads as a
strong signal, which is how a dead band has been mistaken for a live one before.

Needs the dongle exclusively for the duration, so it goes through the same
arbitration as listening. rtl_power ships with the rtl-sdr tools; when it is
absent, scanning is reported unavailable and listening is unaffected.

run() blocks its Flask worker thread for the duration of the sweep (bounded
by `seconds + 30`, the timeout ceiling — see run()). listener.scan_begin()/
scan_end() bracket that block so every arbitration surface (is_listening(),
the nwr-listen synthetic token, the hardware console) can see the claim for
as long as it's held, not just once listening actually starts.
"""
import subprocess

from common import sdr_rx
from services.nwr.common import listener

BAND_LOW = 162390000
BAND_HIGH = 162560000
STEP_HZ = 5000
# 6 s of `-e` still gives multiple 1 s (-i 1) integration passes over all 34
# bins in the 170 kHz band -- plenty to tell a live channel from noise -- while
# cutting the normal-case Flask-worker block from ~10-12 s to ~6-8 s and the
# worst-case timeout ceiling from 40 s to 36 s. Both matter more on a Pi 3
# with one gunicorn worker (4 threads total, see scripts/start-server.sh)
# than they would on a workstation.
DEFAULT_SECONDS = 6


def scan_command(gain=listener.DEFAULT_GAIN, ppm=listener.DEFAULT_PPM,
                 device_serial=None, seconds=DEFAULT_SECONDS, step_hz=STEP_HZ):
    """argv for one bounded rtl_power sweep of the NWR band, CSV on stdout."""
    argv = ["rtl_power"]
    if device_serial:
        argv += ["-d", str(device_serial)]
    argv += ["-f", f"{BAND_LOW}:{BAND_HIGH}:{int(step_hz)}",
             "-g", str(gain), "-p", str(ppm),
             "-i", "1", "-e", str(int(seconds)), "-"]
    return argv


def channel_powers(csv_text):
    """{channel_hz: best dBm seen} for all seven channels.

    rtl_power emits: date, time, low, high, step, samples, then one dBm per bin.
    Each bin's centre is low + step * (i + 0.5); every bin is credited to its
    nearest channel, and a channel keeps the strongest bin it saw.
    """
    best = {}
    for line in (csv_text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            low = float(parts[2])
            step = float(parts[4])
            values = [float(v) for v in parts[6:]]
        except ValueError:
            continue
        for i, dbm in enumerate(values):
            centre = low + step * (i + 0.5)
            hz = min((h for _, h in listener.CHANNELS),
                     key=lambda h: abs(h - centre))
            if abs(hz - centre) > step:
                continue
            if hz not in best or dbm > best[hz]:
                best[hz] = round(dbm, 2)
    return best


def best_channel(powers):
    """(hz, dbm) of the strongest channel, or (None, None) when nothing was read.

    Ties go to the lowest-frequency channel: `max()` keeps the first key it
    saw at the winning value, and `powers` is built by `channel_powers` in
    `listener.CHANNELS` order (WX1 low to WX7 high), so an exact dBm tie
    resolves to whichever of the tied channels comes first in that order.
    """
    if not powers:
        return (None, None)
    hz = max(powers, key=lambda k: powers[k])
    return (hz, powers[hz])


def run(gain=listener.DEFAULT_GAIN, ppm=listener.DEFAULT_PPM, device_serial=None,
        seconds=DEFAULT_SECONDS, runner=None):
    """Execute a sweep. {"ok", "error", "code", "powers", "best_hz", "best_dbm"}.

    Every return carries all six keys, `None`/`{}` where there is no answer,
    so a caller can read `best_hz` without checking `ok` first.

    `runner` is injectable for tests; it is NOT named `run`, which would shadow
    this function's own name inside its body.

    listener.scan_begin()/scan_end() bracket the ENTIRE sweep, not just the
    subprocess call, and always via try/finally — a claim that outlives an
    exception (a timeout, a missing binary) would wedge every arbitration
    surface into reporting NWR busy forever.
    """
    runner = runner or subprocess.run
    failed = {"powers": {}, "best_hz": None, "best_dbm": None}
    listener.scan_begin()
    try:
        r = runner(scan_command(gain, ppm, device_serial, seconds),
                   capture_output=True, text=True, timeout=seconds + 30)
    except FileNotFoundError:
        return {"ok": False, "error": "rtl_power is not installed",
                "code": "NWR_NO_RTL_POWER", **failed}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e), "code": "NWR_SCAN_FAILED", **failed}
    finally:
        listener.scan_end()

    if r.returncode != 0:
        # A nonzero exit means the band was never actually swept -- most
        # often another service already holds the dongle. Reporting {} here
        # would read as "swept and silent," which is a false answer, not an
        # absent one; the docstring's contract is to report failure as an
        # ANSWER, and a false answer fails that harder than a crash would.
        return {"ok": False, "error": sdr_rx.stderr_summary(r.stderr),
                "code": "NWR_SCAN_FAILED", **failed}

    powers = channel_powers(r.stdout or "")
    if not powers:
        # Exit 0 with nothing parseable is not "swept and genuinely silent":
        # a real sweep emits dozens of CSV bins per second of -e, so a
        # silent band still produces rows (all low dBm), just not an empty
        # stdout. Empty stdout on a clean exit means the sweep never
        # happened -- treat it the same as a failed sweep rather than
        # reporting "no transmitter" from a measurement that never ran.
        return {"ok": False, "error": sdr_rx.stderr_summary(r.stderr),
                "code": "NWR_SCAN_FAILED", **failed}

    hz, dbm = best_channel(powers)
    return {"ok": True, "error": None, "code": None, "powers": powers,
            "best_hz": hz, "best_dbm": dbm}
