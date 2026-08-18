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

# How long to wait after spawning before trusting rtl_fm actually claimed the
# dongle. A busy dongle makes rtl_fm die on usb_claim_interface in ~100-200 ms;
# 300 ms is comfortably past that without making every successful Listen click
# feel laggy.
STARTUP_CHECK_S = 0.3

SYNTHETIC_UNIT = "nwr-listen"

_lock = threading.Lock()
_state = {"rtl": None, "mm": None, "channel_hz": None, "started": 0.0,
          "subs": [], "last_error": None, "alerts_seen": 0, "scanning": False}


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
    argv += ["-f", str(int(channel_hz)), "-M", "fm", "-s", str(SAMPLE_RATE)]
    # sdr_rx.gain_flag: DEFAULT_GAIN="auto" must NOT become literal "-g auto"
    # -- see that function's docstring for why (rtl_fm's own two stderr
    # messages are the proof).
    argv += sdr_rx.gain_flag(gain)
    argv += ["-p", str(ppm), "-"]
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
    """True while we hold the dongle for an actual capture session."""
    p = _state["rtl"]
    return p is not None and p.poll() is None


def is_claimed():
    """True whenever NWR holds the dongle for ANY reason -- a listen session
    or an in-progress channel scan.

    is_listening() alone undercounts: scan.run() blocks its Flask worker for
    up to `seconds + 30` while rtl_power owns the dongle exclusively, but
    that hold used to be invisible to every arbitration surface (is_listening(),
    the nwr-listen synthetic token, the hardware console) because none of them
    knew a scan was even running. This is the value all of those must see.
    """
    return is_listening() or bool(_state["scanning"])


def scan_begin():
    """Mark a channel scan as holding the dongle. Pairs with scan_end();
    see is_claimed()."""
    with _lock:
        _state["scanning"] = True


def scan_end():
    """Release the scan claim. MUST run even when the sweep raised — callers
    use try/finally, the same discipline stop() uses for the capture claim."""
    with _lock:
        _state["scanning"] = False


def is_active_wrapper(base_is_active=None):
    """Wrap systemctl is-active so the SYNTHETIC "nwr-listen" unit is answered
    from our own capture state — the bridge that lets the conflict engine see
    NWR as a dongle claimant even though it is not a systemd unit.

    Answers from is_claimed(), not is_listening(): a scan holds the dongle
    exactly as exclusively as a listen session does, and every consumer of
    this token (console, kiosk pill) needs to see that too.

    Chains: pass the next wrapper (or the default) as `base_is_active`.
    """
    if base_is_active is None:
        from common.hardware import _default_is_active
        base_is_active = _default_is_active

    def _wrapped(unit):
        if unit == SYNTHETIC_UNIT:
            return is_claimed()
        return base_is_active(unit)
    return _wrapped


def device_serial(inv):
    """The serial of the dongle assigned to nwr, or None.

    One definition, read by both callers: routes.py has an inventory in hand
    already, and daemon.py loads one per supervisor pass. It lived verbatim in
    both, which is one copy too many for a lookup that decides whether rtl_fm
    gets `-d` -- and rtl_fm without `-d` takes device index 0, which on a
    multi-dongle Pi is usually somebody else's radio.
    """
    if not inv:
        return None
    dev_id = inv.assignments.get("nwr")
    dev = inv.devices.get(dev_id) if dev_id else None
    return (dev or {}).get("serial")


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
          on_header=None, popen=subprocess.Popen, startup_check_s=STARTUP_CHECK_S):
    """Start a listening session. {"ok": bool, "error": str|None}.

    `popen` is injectable so the lifecycle can be tested without a radio.
    `startup_check_s` is injectable so tests don't pay STARTUP_CHECK_S in
    real wall-clock time.

    rtl_fm and multimon-ng are spawned as a pair. If rtl_fm comes up but
    multimon-ng then fails to spawn, the already-running rtl_fm is killed
    and reaped before this returns — an orphaned rtl_fm would hold the
    dongle forever, invisible to stop()/status() because it was never
    recorded in _state.

    A spawn that succeeds is not a claim that succeeded: a dongle already
    held by another service makes rtl_fm print usb_claim_interface and exit
    in ~100-200 ms. Popen() returning is silent about that, so this also
    waits `startup_check_s` and checks rtl_fm is still alive before
    declaring victory (Finding 3).
    """
    rtl = mm = None
    error = None
    with _lock:
        if is_claimed():
            if is_listening():
                return {"ok": False, "error": "already listening",
                        "code": "NWR_BUSY"}
            return {"ok": False, "error": "a channel scan is in progress",
                    "code": "NWR_BUSY"}
        missing = sdr_rx.missing_deps(REQUIRED_BINARIES)
        if missing:
            return {"ok": False, "error": f"not installed: {', '.join(missing)}",
                    "code": "NWR_MISSING_DEPS"}
        try:
            # stderr=PIPE, not DEVNULL: a busy-dongle failure is otherwise
            # thrown away, leaving the operator staring at a start that
            # silently reverted with no reason given anywhere.
            rtl = popen(rtl_command(channel_hz, gain, ppm, device_serial),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # NEITHER text=True NOR bufsize=1: pump() writes rtl_fm's raw
            # bytes into mm.stdin, and text=True turns that into a
            # TextIOWrapper that raises TypeError on the first chunk (an
            # exception pump() does not catch, which silently kills the
            # decode thread and leaves the page reading "LISTENING" forever
            # with no audio and no error — Finding 1). bufsize=0 would trade
            # that for a raw FileIO.write() that can short-write and
            # truncate audio into the demodulator; the default buffered
            # writer (bufsize omitted) loops on partial pipe writes on its
            # own, which is what's wanted here.
            mm = popen(multimon_command(), stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
        terminate(rtl)
        terminate(mm)
        return {"ok": False, "error": error, "code": "NWR_START_FAILED"}

    rtl_stderr = {"text": ""}
    threading.Thread(target=_drain_stderr, args=(rtl, rtl_stderr), daemon=True,
                     name="nwr-rtl-stderr").start()

    # Outside the lock, same reasoning as the error-path terminate above:
    # this is a deliberate wait, and nothing else needs _lock held for it.
    time.sleep(startup_check_s)
    if rtl.poll() is not None:
        with _lock:
            if _state["rtl"] is rtl:
                _state.update({"rtl": None, "mm": None, "channel_hz": None,
                               "started": 0.0})
        terminate(rtl)
        terminate(mm)
        summary = sdr_rx.stderr_summary(rtl_stderr["text"])
        with _lock:
            _state["last_error"] = summary
        return {"ok": False, "error": summary, "code": "NWR_START_FAILED"}

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


def _drain_stderr(proc, buf, keep=4096):
    """Continuously drain a process's stderr into `buf["text"]` (bounded to
    the last `keep` chars).

    Nobody reading a process's stderr pipe eventually fills the OS pipe
    buffer (64 KB on Linux) and blocks every future write to it — rtl_fm
    would then look "running" forever (poll() is None) while producing no
    audio, which is worse than the dongle-busy failure this exists to
    surface. Runs as a daemon thread; ends on its own once `proc`'s stderr
    closes (the process exited).
    """
    try:
        for chunk in iter(lambda: proc.stderr.read(1024) or b"", b""):
            buf["text"] = (buf["text"] + chunk.decode("ascii", "replace"))[-keep:]
    except (OSError, ValueError):
        pass


def terminate(proc, timeout=5):
    """Best-effort stop-and-reap of one process. SIGTERM first, SIGKILL if
    that doesn't land in time, and a wait() after either path — a killed
    process that is never wait()ed on is still a zombie. Safe to call with
    None (nothing was spawned) or an already-dead process.

    Public (not `_terminate`): called across modules by routes.py's stream
    handler and daemon.py's — a prior review flagged calling a private
    helper from outside its own module.
    """
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


def deliver_sentinel(q):
    """Put the b"" poison pill on a subscriber queue, evicting one buffered
    chunk first if the queue is already full.

    Public, like terminate() and for the same reason: both stream handlers
    (routes.py's and daemon.py's) call it from their teardown, and a private
    name called from two other modules is not private -- it is an unenforced
    convention that a reader has to re-derive.

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
        # mm.stdout is a raw byte stream (see start() — no text=True), so
        # decode each line here, at the point of ingestion. same.parse_header()
        # and decode_lines() stay pure str functions unchanged, and every
        # existing test that feeds them str lists still exercises the same
        # grammar code this decodes into.
        lines = (raw.decode("ascii", "replace") for raw in mm.stdout)
        decode_lines(lines, on_header)
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
        terminate(proc)
    for q in subs:
        deliver_sentinel(q)   # unblock any generator waiting on this queue
    return {"ok": True}
