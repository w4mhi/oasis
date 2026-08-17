"""The NWR capture pipeline — manual only, Flask-managed, one session at a time.

    rtl_fm -f <channel> -M fm -s 22050 -
      |
      +--> multimon-ng -t raw -a EAS -   -> SAME headers   (the DECODE branch)
      |
      +--> per-subscriber queue -> ffmpeg -> mp3           (the AUDIO branch)

Two consumers of one rtl_fm stream, fanned out in Python rather than with shell
`tee` and process substitution. That choice buys the two properties the feature
is worthless without:

  * the DECODE branch survives the browser closing its audio connection — an
    alert must be caught whether or not a tab is open;
  * the AUDIO branch cannot kill the decoder — a stalled or aborted HTTP
    response must not propagate a broken pipe into the decode path.

A subscriber whose queue is full LOSES AUDIO and is never waited on. Dropping
sound for a tab that stopped reading is correct; blocking the decoder for it is
not.

NO BARE SIBLING IMPORTS in this package. services/satellites/ must be imported
bare because its modules do `import demod` at top level, which makes
`services.satellites.listen` a SECOND module object with its own state. Every
import here is `from services.nwr.common import X`, so that trap cannot arise —
keep it that way.
"""
import logging
import queue
import shutil
import signal
import subprocess
import threading
import time

from common import sdr_rx
from services.nwr.common import same

log = logging.getLogger(__name__)

# The seven NOAA Weather Radio channels, 25 kHz apart.
CHANNELS = (
    ("WX1", 162400000), ("WX2", 162425000), ("WX3", 162450000),
    ("WX4", 162475000), ("WX5", 162500000), ("WX6", 162525000),
    ("WX7", 162550000),
)

SAMPLE_RATE = 22050          # what multimon-ng's EAS demodulator expects
DEFAULT_GAIN = "auto"
DEFAULT_PPM = 0
CHUNK = 4096                 # ~11 reads/second at 44 KB/s — negligible on a Pi 3
SUB_QUEUE_MAX = 64           # ~6 s of audio; past that a slow client just loses it
REQUIRED_BINARIES = ("rtl_fm", "multimon-ng")

SYNTHETIC_UNIT = "nwr-listen"

_lock = threading.Lock()
_state = {"rtl": None, "mm": None, "channel_hz": None, "started": 0.0,
          "subs": [], "last_error": None, "alerts_seen": 0}


# ── Command construction (pure) ──────────────────────────────────────────────

def rtl_command(channel_hz, gain=DEFAULT_GAIN, ppm=DEFAULT_PPM, device_serial=None):
    """argv for rtl_fm. A LIST, not a shell string: there is no shell in this
    pipeline, so there is no quoting to get right.

    device_serial pins rtl_fm to the assigned dongle. Without it rtl_fm takes
    device index 0, which on a multi-dongle Pi is often another service's
    dongle — it cannot claim it and dies immediately.
    """
    argv = ["rtl_fm"]
    if device_serial:
        argv += ["-d", str(device_serial)]
    argv += ["-f", str(int(channel_hz)), "-M", "fm", "-s", str(SAMPLE_RATE),
             "-g", str(gain), "-p", str(ppm), "-"]
    return argv


def multimon_command():
    """argv for multimon-ng reading raw s16le on stdin with only EAS enabled."""
    return ["multimon-ng", "-t", "raw", "-a", "EAS", "-"]


# ── The fan-out (pure enough to test with BytesIO) ───────────────────────────

def pump(source, sink, subscribers, chunk=CHUNK):
    """Read `source` to exhaustion, writing every chunk to `sink` first and then
    offering it to each subscriber queue.

    Order matters: the decoder is fed BEFORE the subscribers, and a subscriber
    failure of any kind is swallowed. A write failure on `sink` ends the pump —
    the decoder is gone, so there is nothing left worth reading.
    """
    while True:
        try:
            data = source.read(chunk)
        except (OSError, ValueError):
            return
        if not data:
            return
        try:
            sink.write(data)
            sink.flush()
        except (BrokenPipeError, OSError, ValueError):
            return
        for q in list(subscribers):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass          # a tab that stopped reading loses audio, nothing more
            except Exception:  # noqa: BLE001
                pass


def decode_lines(lines, on_header):
    """Feed multimon-ng's stdout to `on_header` for each valid SAME header.

    A handler that raises is logged and skipped: an unwritable alert store must
    lose a record, not the watch.
    """
    for line in lines:
        parsed = same.parse_header(line)
        if not parsed:
            continue
        try:
            on_header(parsed)
        except Exception:      # noqa: BLE001
            log.exception("nwr: alert handler failed for %s", parsed.get("raw"))


# ── State ────────────────────────────────────────────────────────────────────

def is_listening():
    """True while we hold the dongle."""
    p = _state["rtl"]
    return p is not None and p.poll() is None


def is_active_wrapper(base_is_active=None):
    """Wrap systemctl is-active so the SYNTHETIC "nwr-listen" unit is answered
    from our own capture state — the bridge that lets the conflict engine see
    NWR as a dongle claimant even though it is not a systemd unit.

    Chains: pass the next wrapper (or the default) as `base_is_active`.
    """
    if base_is_active is None:
        from common.hardware import _default_is_active
        base_is_active = _default_is_active

    def _wrapped(unit):
        if unit == SYNTHETIC_UNIT:
            return is_listening()
        return base_is_active(unit)
    return _wrapped


def preconditions(inv=None, which=shutil.which, run=None, is_active=None):
    """Everything the page needs to decide whether listening is possible now.
    Pure of side effects; every check independently injectable."""
    missing = sdr_rx.missing_deps(REQUIRED_BINARIES, which=which)
    eff_is_active = is_active if is_active is not None else is_active_wrapper()
    busy, holder = sdr_rx.dongle_busy(inv, eff_is_active, "nwr")
    dev_id = inv.assignments.get("nwr") if inv else None
    dev = inv.devices.get(dev_id) if (inv and dev_id) else None
    enc, _mime = sdr_rx.stream_encoder(SAMPLE_RATE, which=which)
    return {
        "missing_deps":   missing,
        "dongle_present": (not missing) and sdr_rx.dongle_present(run),
        "assigned":       bool(dev_id),
        "device":         (dev.get("label") if dev else None),
        "busy":           busy,
        "holder":         holder,
        "can_stream":     bool(enc),
        "can_scan":       bool(which("rtl_power")),
    }


def status():
    """Live capture state for the page and the health check."""
    label = None
    for name, hz in CHANNELS:
        if hz == _state["channel_hz"]:
            label = name
    return {
        "listening":   is_listening(),
        "channel_hz":  _state["channel_hz"],
        "channel":     label,
        "started":     _state["started"] or None,
        "elapsed_s":   int(time.time() - _state["started"]) if is_listening() else 0,
        "subscribers": len(_state["subs"]),
        "alerts_seen": _state["alerts_seen"],
        "last_error":  _state["last_error"],
    }


# ── Subscribers ──────────────────────────────────────────────────────────────

def subscribe():
    """A bounded queue receiving raw s16le audio while a session runs."""
    q = queue.Queue(maxsize=SUB_QUEUE_MAX)
    with _lock:
        _state["subs"].append(q)
    return q


def unsubscribe(q):
    """Drop a subscriber. MUST be called in a finally: on the streaming route —
    a tab that closed and left its queue behind is a slow memory leak and a
    dongle held past its usefulness."""
    with _lock:
        try:
            _state["subs"].remove(q)
        except ValueError:
            pass


# ── Session lifecycle ────────────────────────────────────────────────────────

def start(channel_hz, gain=DEFAULT_GAIN, ppm=DEFAULT_PPM, device_serial=None,
          on_header=None, popen=subprocess.Popen):
    """Start a listening session. {"ok": bool, "error": str|None}.

    `popen` is injectable so the lifecycle can be tested without a radio.

    rtl_fm and multimon-ng are spawned as a pair. If rtl_fm comes up but
    multimon-ng then fails to spawn, the already-running rtl_fm is killed
    and reaped before this returns — an orphaned rtl_fm would hold the
    dongle forever, invisible to stop()/status() because it was never
    recorded in _state.
    """
    rtl = mm = None
    error = None
    with _lock:
        if is_listening():
            return {"ok": False, "error": "already listening",
                    "code": "NWR_BUSY"}
        missing = sdr_rx.missing_deps(REQUIRED_BINARIES)
        if missing:
            return {"ok": False, "error": f"not installed: {', '.join(missing)}",
                    "code": "NWR_MISSING_DEPS"}
        try:
            rtl = popen(rtl_command(channel_hz, gain, ppm, device_serial),
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            mm = popen(multimon_command(), stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True, bufsize=1)
        except OSError as e:
            error = str(e)
            _state["last_error"] = error
        else:
            _state.update({"rtl": rtl, "mm": mm, "channel_hz": int(channel_hz),
                           "started": time.time(), "last_error": None,
                           "alerts_seen": 0})

    if error is not None:
        # Outside the lock: killing and reaping a process can block for
        # seconds, and nothing above needs _lock held while it happens.
        _terminate(rtl)
        _terminate(mm)
        return {"ok": False, "error": error, "code": "NWR_START_FAILED"}

    def _count(parsed):
        with _lock:
            _state["alerts_seen"] += 1
        if on_header:
            on_header(parsed)

    threading.Thread(target=_reader, args=(rtl, mm), daemon=True,
                     name="nwr-pump").start()
    threading.Thread(target=_decoder, args=(mm, _count), daemon=True,
                     name="nwr-decode").start()
    return {"ok": True, "error": None}


def _terminate(proc, timeout=5):
    """Best-effort stop-and-reap of one process. SIGTERM first, SIGKILL if
    that doesn't land in time, and a wait() after either path — a killed
    process that is never wait()ed on is still a zombie. Safe to call with
    None (nothing was spawned) or an already-dead process."""
    if proc is None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=timeout)
        return
    except Exception:      # noqa: BLE001
        pass
    try:
        proc.kill()
        proc.wait(timeout=timeout)
    except Exception:      # noqa: BLE001
        pass


def _deliver_sentinel(q):
    """Put the b"" poison pill on a subscriber queue, evicting one buffered
    chunk first if the queue is already full.

    A full queue is exactly the stalled-reader case this sentinel exists to
    unblock: the consumer is behind, so dropping one more already-buffered
    chunk to make room for the sentinel costs nothing it wasn't already
    going to lose, and it is strictly better than the consumer hanging on
    its own read timeout instead of ending promptly."""
    try:
        q.put_nowait(b"")
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(b"")
    except queue.Full:
        pass    # a producer refilled it between get and put; give up quietly


def _reader(rtl, mm):
    try:
        pump(rtl.stdout, mm.stdin, _state["subs"])
    finally:
        try:
            mm.stdin.close()
        except Exception:      # noqa: BLE001
            pass


def _decoder(mm, on_header):
    try:
        decode_lines(mm.stdout, on_header)
    finally:
        with _lock:
            if _state["mm"] is mm:
                _state["mm"] = None


def stop():
    """Stop the session and release the dongle. Idempotent."""
    with _lock:
        rtl, mm = _state["rtl"], _state["mm"]
        _state.update({"rtl": None, "mm": None, "channel_hz": None,
                       "started": 0.0})
        subs, _state["subs"] = _state["subs"], []
    for proc in (rtl, mm):
        _terminate(proc)
    for q in subs:
        _deliver_sentinel(q)   # unblock any generator waiting on this queue
    return {"ok": True}
