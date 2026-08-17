"""Sweep the NWR band and report which of the seven channels is actually there.

rtl_power is the instrument. Measuring RMS on demodulated audio is NOT a
substitute — an empty channel demodulates to full-scale noise and reads as a
strong signal, which is how a dead band has been mistaken for a live one before.

Needs the dongle exclusively for the duration, so it goes through the same
arbitration as listening. rtl_power ships with the rtl-sdr tools; when it is
absent, scanning is reported unavailable and listening is unaffected.
"""
import subprocess

from services.nwr.common import listener

BAND_LOW = 162390000
BAND_HIGH = 162560000
STEP_HZ = 5000
DEFAULT_SECONDS = 10


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
    """(hz, dbm) of the strongest channel, or (None, None) when nothing was read."""
    if not powers:
        return (None, None)
    hz = max(powers, key=lambda k: powers[k])
    return (hz, powers[hz])


def run(gain=listener.DEFAULT_GAIN, ppm=listener.DEFAULT_PPM, device_serial=None,
        seconds=DEFAULT_SECONDS, runner=None):
    """Execute a sweep. {"ok", "powers", "best_hz", "best_dbm", "error"}.

    `runner` is injectable for tests; it is NOT named `run`, which would shadow
    this function's own name inside its body.
    """
    runner = runner or subprocess.run
    try:
        r = runner(scan_command(gain, ppm, device_serial, seconds),
                   capture_output=True, text=True, timeout=seconds + 30)
    except FileNotFoundError:
        return {"ok": False, "error": "rtl_power is not installed",
                "code": "NWR_NO_RTL_POWER", "powers": {}}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e), "code": "NWR_SCAN_FAILED",
                "powers": {}}
    powers = channel_powers(r.stdout or "")
    hz, dbm = best_channel(powers)
    return {"ok": True, "error": None, "powers": powers,
            "best_hz": hz, "best_dbm": dbm}
