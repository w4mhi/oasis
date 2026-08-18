"""Sweep the NWR band and report which of the seven channels is actually there.

rtl_power is the instrument. Measuring RMS on demodulated audio is NOT a
substitute — an empty channel demodulates to full-scale noise and reads as a
strong signal, which is how a dead band has been mistaken for a live one before.

Needs the dongle exclusively for the duration, so it goes through the same
arbitration as capturing. rtl_power ships with the rtl-sdr tools; when it is
absent, scanning is reported unavailable and the watch runs on its configured
channel.

run() blocks its caller — the watch daemon's supervisor thread — for the
duration of the sweep (bounded by `seconds + 30`, the timeout ceiling; see
run()). The watch is DEAF for that whole time: rtl_power holds the tuner, so
there is no rtl_fm and no decoder. listener.scan_begin()/scan_end() bracket
the block so anything arbitrating over the dongle sees the claim for as long
as it is held, not just once a capture starts.
"""
import statistics
import subprocess

from common import sdr_rx
from services.nwr.common import listener

BAND_LOW = 162390000
BAND_HIGH = 162560000
STEP_HZ = 5000
# 6 s of `-e` still gives multiple 1 s (-i 1) integration passes over all 34
# bins in the 170 kHz band -- plenty to tell a live channel from noise.
#
# THE CONSTRAINT IS DEAD AIR, not a blocked web worker (it was one, while the
# capture lived in Flask). Every second of sweep is a second the watch is not
# decoding SAME, and a sweep runs at startup, after every retune to Auto, and
# on each scheduled rescan. An alert that arrives inside the gap is simply not
# heard -- there is no buffer and no second receiver. Raising this buys sweep
# confidence with deafness; lowering it below a few integration passes buys
# nothing, because a sweep that cannot tell a channel from noise sends the
# watch to the wrong frequency for the whole rescan interval (15 min, doubling
# to 6 h -- daemon.rescan_delay).
DEFAULT_SECONDS = 6


def scan_command(gain=listener.DEFAULT_GAIN, ppm=listener.DEFAULT_PPM,
                 device_serial=None, seconds=DEFAULT_SECONDS, step_hz=STEP_HZ):
    """argv for one bounded rtl_power sweep of the NWR band, CSV on stdout."""
    argv = ["rtl_power"]
    if device_serial:
        argv += ["-d", str(device_serial)]
    argv += ["-f", f"{BAND_LOW}:{BAND_HIGH}:{int(step_hz)}"]
    # sdr_rx.gain_flag: rtl_power takes -g the same broken way rtl_fm does --
    # "-g auto" is 0 dB, not AGC. See that function's docstring. This bug
    # meant every channel reading the scan ever took was at 0 dB gain, which
    # could pick the wrong "strongest" channel.
    argv += sdr_rx.gain_flag(gain)
    argv += ["-p", str(ppm), "-i", "1", "-e", str(int(seconds)), "-"]
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


# How far the winner has to stand above the rest of the band before the sweep
# counts as a real signal. See channel_margin() for where the number comes from
# and what would change it.
WEAK_MARGIN_DB = 2.0


def channel_margin(powers, best_hz):
    """dB the winning channel stands above the median of the other six, or None
    when the sweep cannot answer.

    THE ABSOLUTE LEVEL IS NOT THE SIGNAL. rtl_power's dBm are relative to
    whatever the dongle's front end was doing, and on this band the empty
    channels do not sit anywhere near a textbook noise floor. Measured on
    pi5draws, dongle 00000031, against a live NWS transmitter, through this
    very code path:

        WX1 -5.71   WX2 -5.56   WX3 -5.64   WX4 -5.75
        WX5 -5.72   WX6 -5.68   WX7 -1.95   <- the transmitter

    Best -1.95, median of the other six -5.695, margin +3.75 dB. The six empty
    channels span 0.19 dB end to end, so the band's own noise is far tighter
    than the margin a transmitter opens up.

    That measurement is why the old absolute floor (WEAK_DBM = -50 dBm) was
    dead code: at -5.7 dBm even an EMPTY channel sat 44 dB above it, so `weak`
    could never be true, the amber card state was unreachable and the weak-band
    rescan back-off never armed. With no antenna all seven channels read the
    same noise and the margin collapses toward 0 dB -- which an absolute floor
    cannot see at all, and this can.

    WEAK_MARGIN_DB = 2.0 sits an order of magnitude above that 0.19 dB spread,
    so noise alone cannot cross it, and about half way to the +3.75 dB a good
    antenna produced, so a station further from its transmitter than this one
    still reads healthy.

    Be clear about what this is: ONE bench observation, one station, one dongle,
    one strong local transmitter. It is not a characterised curve. A station
    that reads healthy on the air but amber on the card -- a distant or fringe
    transmitter, a different tuner, a different gain setting -- is evidence to
    lower the number; a station whose card stays green with the antenna
    unplugged is evidence to raise it. Record the seven readings the way this
    docstring does before moving it.

    Returns None, not a number, when the question is unanswerable: no sweep,
    fewer than two channels read, or a `best_hz` that is not in `powers`. A
    caller cannot tell "no margin" from "did not measure" out of a float, and
    the two mean opposite things.
    """
    if not powers or best_hz is None:
        return None
    try:
        best = float(powers[best_hz])
    except (KeyError, TypeError, ValueError):
        return None
    others = []
    for hz, dbm in powers.items():
        if hz == best_hz:
            continue
        try:
            others.append(float(dbm))
        except (TypeError, ValueError):
            continue
    if not others:
        return None
    return round(best - statistics.median(others), 2)


def margin_is_weak(margin_db, threshold=WEAK_MARGIN_DB):
    """Whether a margin reads as an empty band. Split from channel_margin() so
    the measurement and the policy can be read, and tested, apart.

    An unmeasurable margin is NOT weak. Weak is a claim about the band, and
    this is the branch where nothing was measured -- claiming it would paint
    the card amber and arm the rescan back-off on the strength of no evidence,
    which is exactly what the old absolute floor did in reverse. It matches
    choose_channel()'s failed-sweep branch, which reports weak False too.
    """
    if margin_db is None:
        return False
    try:
        return float(margin_db) < threshold
    except (TypeError, ValueError):
        return False


def run(gain=listener.DEFAULT_GAIN, ppm=listener.DEFAULT_PPM, device_serial=None,
        seconds=DEFAULT_SECONDS, runner=None):
    """Execute a sweep. {"ok", "error", "code", "powers", "best_hz", "best_dbm"}.

    Every return carries all six keys, `None`/`{}` where there is no answer,
    so a caller can read `best_hz` without checking `ok` first.

    `runner` is injectable for tests; it is NOT named `run`, which would shadow
    this function's own name inside its body.

    listener.scan_begin()/scan_end() bracket the ENTIRE sweep, not just the
    subprocess call, and always via try/finally — a claim that outlives an
    exception (a timeout, a missing binary) would leave every arbitration
    surface reporting NWR busy for the life of the daemon.
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
