"""The always-on weather watch.

Assigning a dongle to `nwr` is the whole action: this daemon scans the band,
picks the strongest of the seven NOAA channels, and decodes SAME continuously
until it is stopped. That is a reversal of v1's manual-only design, made because
an alert you miss because nobody pressed a button is worth very little.

It lives outside Flask for one reason: a watch that dies with a web-server
restart, or does not return after a reboot, is not a watch. The shape is the one
already running on every ADS-B box (services/adsb/common/adsb.py) -- a worker
thread plus a ThreadingHTTPServer bound to loopback, with Flask proxying.

THIS PROCESS IS THE ONLY WRITER OF THE ALERT STORE. alerts.record() takes a
module lock, which means nothing across processes, and common/atomic_json
documents that two read-modify-write callers lose updates even with atomic
replacement -- which would drop alerts under exactly the conditions that matter:
several counties, several messages, close together. Flask reads; it never writes.
"""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import sdr_rx
from services.nwr.common import alerts, announce, bell, listener, scan, settings

log = logging.getLogger(__name__)

API_PORT = 8089          # NOT 8087 (oasis-ai) or 8088 (rtl_433) - see specs/PORT-MAP.md
SERVICE = "oasis-nwr"

# Below this the band reads as empty. Chosen from bench measurement on a live
# transmitter: a real NWR signal sits far above it, and an antenna that fell off
# sits far below. It is a reporting threshold, never a refusal to start.
WEAK_DBM = -50.0

RESCAN_START_S = 15 * 60
RESCAN_MAX_S = 6 * 3600
SUPERVISE_TICK_S = 5
RETRY_MAX_S = 5 * 60

# Concurrent /stream connections. One operator at one browser needs exactly
# one; the extra two are headroom for a reload whose new request arrives
# before the old connection's teardown has run. Anything past that is a
# client reconnecting in a loop, and every stream costs an ffmpeg -- on the
# 2 GB Pi 3 this project supports, an unbounded count is the whole budget.
MAX_STREAMS = 3

# Failures a band sweep cannot get past either: rtl_power claims the tuner
# exactly as exclusively as rtl_fm does, so retrying through choose_channel()
# would spend six seconds re-proving what listener.start() just said. The
# first two are matched against the operator-facing text sdr_rx.stderr_summary()
# produces; "no dongle assigned" is supervise()'s own literal last_error when
# _device_serial() returns None -- there is no sweep to run without a dongle
# either.
DONGLE_UNAVAILABLE = (
    "another service is already using",
    "no supported devices found",
    "no dongle assigned",
)

_state = {
    # retuning is the deliberate gap an accepted /retune costs: the capture is
    # down on purpose, exactly like the one a scheduled rescan costs, and a
    # page that cannot tell those from a dead decoder reports a fault.
    "phase": "starting",     # starting | scanning | listening | retuning | retrying | stopped
    "channel_hz": None,
    "pinned_hz": None,       # the pin in force at the last channel decision
    "scan": None,            # last scan result, or None when pinned
    "scan_weak": False,
    "consecutive_weak": 0,
    "next_rescan": 0,
    "retry_failures": 0,     # consecutive listener.start() failures
    "next_retry": 0,         # epoch of the next start attempt, 0 when running
    # A retune request is a SEQUENCE, not a flag. The supervisor reads the
    # counter at the top of a pass and only acks that value once a capture has
    # actually started from a configuration read after it, so a request that
    # lands while a stale config is already in flight is not lost -- it is
    # serviced on the following pass. A boolean cleared on consumption would
    # drop exactly that request, which is the one an operator clicking twice
    # produces.
    "retune_seq": 0,         # bumped by every accepted request_retune()
    "retune_ack": 0,         # the seq a running capture's config already covers
    "streams": 0,            # live /stream connections; see MAX_STREAMS
    "started": 0.0,
    "alerts_seen": 0,
    "last_decode": None,     # {station, event, at}
    "last_error": None,
}
_lock = threading.Lock()

# The station root, for the HTTP handler. supervise() is handed one; _Handler
# is constructed per connection by ThreadingHTTPServer and cannot be, so serve()
# records it here. Read-only after that.
REPO_ROOT = None


# ── Channel choice ───────────────────────────────────────────────────────────

def choose_channel(repo_root, cfg, device_serial, scan_fn=None):
    """(channel_hz, scan_result_or_None).

    A pinned channel is an explicit operator override and skips the sweep
    entirely -- sweeping would take the dongle for six seconds to re-derive an
    answer already given.

    A weak best still starts. Refusing would leave nothing running, and silence
    meaning "no transmitter" is indistinguishable from silence meaning "broken";
    the weak flag is what makes the card amber instead of green.
    """
    pinned = (cfg or {}).get("pinned_channel")
    if pinned:
        return int(pinned), None

    scan_fn = scan_fn or scan.run
    result = scan_fn(gain=(cfg or {}).get("gain", listener.DEFAULT_GAIN),
                     ppm=(cfg or {}).get("ppm", listener.DEFAULT_PPM),
                     device_serial=device_serial)
    result = dict(result or {})

    if not result.get("ok") or not result.get("best_hz"):
        # No sweep is not the same as no signal. Fall back to whatever channel
        # is configured so the watch still runs, and say the scan failed.
        result["weak"] = False
        return int((cfg or {}).get("channel_hz")
                   or settings.DEFAULTS["channel_hz"]), result

    result["weak"] = float(result.get("best_dbm", WEAK_DBM)) < WEAK_DBM
    return int(result["best_hz"]), result


def rescan_delay(consecutive_weak):
    """Seconds until the next sweep, doubling from 15 min to a 6 h ceiling.

    Zero when the last scan was healthy: a good channel is not re-derived on a
    timer, because every sweep is a gap in the watch.
    """
    if consecutive_weak <= 0:
        return 0
    return min(RESCAN_START_S * (2 ** (consecutive_weak - 1)), RESCAN_MAX_S)


def retry_delay(consecutive_failures, tick=SUPERVISE_TICK_S):
    """Seconds before the next start attempt, doubling from one tick to a
    five-minute ceiling.

    Distinct from rescan_delay(), which governs a weak SCAN and is only ever
    consulted while a capture is running. This one governs a capture that will
    not start AT ALL -- almost always the dongle held by another service, which
    on a real station lasts for days, not seconds. Without it the whole cycle
    (rtl_power sweep, rtl_fm, multimon-ng) respawned every tick forever:
    measured on a live box at 9 attempts in 43 s, which is roughly 17,000
    journal lines a day and real CPU on a Pi 3.

    Zero failures returns one tick, NOT zero -- the opposite of its neighbour
    above, deliberately. The two answer different questions: rescan_delay(0)
    means "no rescan is scheduled at all", which is a real state, while this
    one is always "how long before the next pass", and a zero there would be
    the very hot-spin this function exists to stop. supervise() only ever
    calls it with a positive count.
    """
    if consecutive_failures <= 0:
        return tick
    return min(tick * (2 ** (consecutive_failures - 1)), RETRY_MAX_S)


def sweep_is_pointless(error):
    """True when a failed listener.start() named a dongle rtl_power could not
    claim either, so the next attempt should skip choose_channel()'s sweep."""
    text = (error or "").lower()
    return any(m in text for m in DONGLE_UNAVAILABLE)


def fallback_channel(cfg):
    """The channel to use when no sweep has ever run: the operator's pin, then
    the configured channel, then the default."""
    cfg = cfg or {}
    return int(cfg.get("pinned_channel") or cfg.get("channel_hz")
               or settings.DEFAULTS["channel_hz"])


def retry_channel(cfg, last_choice):
    """The channel to retry on when the sweep is skipped.

    A pin still wins: it is an explicit override and it can be set while we
    are retrying, so honouring it is the only way a pin ever takes effect on
    this branch. Otherwise reuse whatever the last sweep chose. Forgetting it
    and dropping back to the configured default is how a station ends up
    reading LISTENING with nothing decoded: the sweep picks WX3, the dongle is
    lost to a manual Listen or an ADS-B restart, and the retries aim at WX7 --
    where there is no transmitter, and where a healthy last scan left
    next_rescan at 0, so nothing ever re-derives it.
    """
    pinned = (cfg or {}).get("pinned_channel")
    if pinned:
        return int(pinned)
    if last_choice:
        return int(last_choice)
    return fallback_channel(cfg)


def channel_label(hz):
    """"WX3" for 162450000, or None. One lookup, read by status() and by the
    retune detail text."""
    for name, ch in listener.CHANNELS:
        if ch == hz:
            return name
    return None


# ── Retune on request ────────────────────────────────────────────────────────

def retune_plan(cfg, channel_hz, pinned_hz):
    """(should_retune, human-readable detail) for a "the pin changed" request.

    Pure, so the decision can be read without a radio in the room. Three cases:

      * a pin that differs from what is tuned -- retune, because a healthy
        capture is otherwise never interrupted (supervise() stops only for a
        due rescan, and a healthy scan leaves next_rescan at 0), so the pin
        would take effect at some unknowable later time or never;
      * a pin that IS what is tuned -- nothing to do. Interrupting the watch to
        land on the frequency it is already on is a gap bought for nothing, and
        an operator re-selecting the current channel is a normal click;
      * no pin, which is the operator choosing Auto: retune only if a pin is
        what put us here. If the scan chose this channel, clearing a pin that
        was never in force changes nothing, and re-deriving the same answer
        costs the six seconds rtl_power holds the tuner for.
    """
    pinned = (cfg or {}).get("pinned_channel")
    if pinned:
        pinned = int(pinned)
        name = channel_label(pinned) or pinned
        if pinned == channel_hz:
            return False, f"the watch is already on {name}"
        return True, f"the watch will retune to {name}"
    if pinned_hz is not None:
        return True, "the watch will scan for the strongest channel"
    return False, "the watch is already choosing its own channel"


def request_retune(repo_root):
    """Ask the supervisor to re-derive its channel. Touches no radio.

    This is the whole of the "Flask asks, the daemon acts" boundary. Flask
    writes nwr.json and then says "read it again"; the file stays the single
    source of truth for what the operator chose, and this process stays the
    only thing that starts, stops or tunes a capture. Nothing here is passed a
    frequency, so nothing here can be asked to tune to one.

    It cannot wedge the watch either: all it does is bump a counter. The
    supervisor's response is one listener.stop() -- which blocks until both
    subprocesses are reaped -- after which the ordinary not-listening path
    runs, with the ordinary retry back-off behind it if the new capture will
    not start.
    """
    cfg = settings.load(repo_root)
    with _lock:
        channel_hz = _state["channel_hz"]
        retune, detail = retune_plan(cfg, channel_hz, _state["pinned_hz"])
        if retune:
            _state["retune_seq"] += 1
        pending = _state["retune_seq"] != _state["retune_ack"]
    if retune:
        log.info("nwr: retune requested (%s)", detail)
    return {"ok": True, "retuning": retune, "pending": pending,
            "detail": detail, "channel_hz": channel_hz}


# ── Decode handling ──────────────────────────────────────────────────────────

def _make_handler(repo_root):
    """The on_header callback: store, then maybe speak. Never raises -- the
    decode loop must survive an unwritable disk or a missing voice."""
    def _on_header(parsed):
        try:
            cfg = settings.load(repo_root)
        except Exception:                      # noqa: BLE001
            # A settings file that will not load must not cost us the alert:
            # the defaults watch everything and speak nothing, which is the
            # right way to fail here.
            log.exception("nwr: could not load settings")
            cfg = dict(settings.DEFAULTS)
        try:
            added, rec = alerts.record(repo_root, parsed,
                                       cfg.get("watch_fips"), time.time())
        except Exception:                      # noqa: BLE001
            log.exception("nwr: could not store %s", parsed.get("raw"))
            return
        with _lock:
            _state["alerts_seen"] += 1
            _state["last_decode"] = {"station": rec.get("station"),
                                     "event": rec.get("event"),
                                     "at": int(time.time())}
        if not added:
            return                              # a repeat of the same message
        try:
            speak, why = bell.should_speak(cfg, rec, repo_root)
            if speak:
                announce.speak(repo_root, rec)
            else:
                log.info("nwr: not speaking %s (%s)", rec.get("event"), why)
        except Exception:                       # noqa: BLE001
            # The missing-voice case, and anything else the speech path can
            # raise. decode_lines() wraps this callback too, but containment
            # this docstring promises has to be real HERE -- the alert is
            # already stored, and losing the decode loop over a mute box would
            # cost every alert after it.
            log.exception("nwr: could not announce %s", rec.get("event"))
    return _on_header


def _device_serial(repo_root):
    """listener.device_serial() against a freshly loaded inventory, or None if
    the inventory will not load. Re-read every pass on purpose: a dongle can be
    assigned to nwr while this loop is already running."""
    try:
        from common import hardware as HW
        return listener.device_serial(HW.load(repo_root))
    except Exception:                          # noqa: BLE001
        return None


# ── Supervisor ───────────────────────────────────────────────────────────────

def supervise(repo_root, stop_event=None, tick=SUPERVISE_TICK_S, now=time.time):
    """Keep a capture running for as long as this process lives.

    Restarts the capture if it dies, and re-scans on a back-off while the band
    reads weak -- an antenna that gets reconnected should be picked up without
    the operator remembering to intervene.

    Interrupts a healthy capture for exactly two reasons: a due rescan, and an
    accepted retune request (request_retune). Both are the same act -- stop and
    let the next pass choose again -- and both are a deliberate gap in the
    watch, reported as such rather than left to look like a dead decoder.

    A capture that fails to START backs off separately (retry_delay): the
    normal reason is another service holding the dongle, which lasts for days,
    and retrying that every tick respawns three processes at a time for nothing.

    Every wait is stop_event.wait(), never time.sleep -- a five-minute back-off
    must not become a five-minute shutdown. `now` is injectable so a test can
    assert on retry cadence without spending the wall clock.

    NOTHING here may raise out of the loop. This thread IS the watch: if it
    dies the HTTP server keeps answering /status with whatever phase it froze
    at, and the only evidence is one threading.excepthook traceback in the
    journal. A watch that has silently stopped watching is worse than one that
    retries too often.
    """
    stop_event = stop_event or threading.Event()
    failures = 0
    last_error = None
    chosen_hz = None            # what the most recent sweep picked
    while not stop_event.is_set():
        try:
            delay = tick
            # Read BEFORE settings.load() below, and acked only after a capture
            # has started: a request that lands while this pass is already
            # holding a configuration read before it keeps a higher seq and is
            # serviced on the next pass instead of being silently absorbed.
            with _lock:
                seq = _state["retune_seq"]
                retune_due = seq != _state["retune_ack"]
            if listener.is_listening():
                with _lock:
                    _state["phase"] = "retuning" if retune_due else "listening"
                    due = _state["next_rescan"]
                if retune_due:
                    # The same interruption a due rescan makes, for the same
                    # reason and with the same consequence: stop(), which
                    # blocks until both subprocesses are reaped, and let the
                    # ordinary not-listening path below start the new capture.
                    # No third mechanism -- a start that then fails backs off
                    # on the retry curve like any other.
                    log.info("nwr: retuning on request")
                    listener.stop()
                elif due and now() >= due:
                    log.info("nwr: scheduled rescan")
                    listener.stop()
                stop_event.wait(delay)
                continue        # this pass's one and only wait

            cfg = settings.load(repo_root)
            serial = _device_serial(repo_root)

            if serial is None:
                # listener.rtl_command() falls back to device index 0 when
                # device_serial is falsy -- on a multi-dongle Pi that is very
                # often another service's radio. Neither a sweep (rtl_power)
                # nor a listen (rtl_fm) may run until an operator assigns us
                # one; is_claimed() guards a busy dongle, not an absent
                # assignment, so this has to be checked here.
                last_error = "no dongle assigned"
                failures += 1
                delay = retry_delay(failures, tick)
                with _lock:
                    _state["phase"] = "retrying"
                    _state["channel_hz"] = None
                    _state["last_error"] = last_error
                    _state["retry_failures"] = failures
                    _state["next_retry"] = now() + delay
                stop_event.wait(delay)
                continue        # this pass's one and only wait

            if failures and sweep_is_pointless(last_error):
                # Retrying into a dongle we could not claim: no sweep, and the
                # weak-scan bookkeeping is left exactly as it was. This branch
                # says nothing new about the band -- including which channel
                # is best, which is why it reuses the last sweep's answer.
                hz = retry_channel(cfg, chosen_hz)
            else:
                with _lock:
                    _state["phase"] = "scanning"
                hz, result = choose_channel(repo_root, cfg, serial)
                chosen_hz = hz
                with _lock:
                    _state["scan"] = result
                    weak = bool(result and result.get("weak"))
                    _state["scan_weak"] = weak
                    _state["consecutive_weak"] = (_state["consecutive_weak"] + 1) if weak else 0
                    _state["next_rescan"] = (
                        now() + rescan_delay(_state["consecutive_weak"])
                        if weak else 0)

            pin = cfg.get("pinned_channel")
            pin = int(pin) if pin else None     # outside the lock: it can raise
            res = listener.start(hz, gain=cfg.get("gain"), ppm=cfg.get("ppm"),
                                 device_serial=serial,
                                 on_header=_make_handler(repo_root))
            ok = bool(res.get("ok"))
            last_error = res.get("error")
            failures = 0 if ok else failures + 1
            delay = tick if ok else retry_delay(failures, tick)
            with _lock:
                _state["channel_hz"] = hz if ok else None
                # What retune_plan() compares "Auto" against: whether a pin is
                # what put us on this channel, or the sweep did.
                _state["pinned_hz"] = pin if ok else None
                _state["last_error"] = last_error
                _state["phase"] = "listening" if ok else "retrying"
                _state["retry_failures"] = failures
                _state["next_retry"] = 0 if ok else now() + delay
                if ok:
                    _state["started"] = now()
                    # Only on success, and only up to the seq this pass read:
                    # the retune is not done until the capture it asked for is
                    # running, so a start that fails leaves the request pending
                    # and the retry curve keeps aiming at the pinned channel
                    # (retry_channel). A later request has a higher seq and
                    # survives this.
                    _state["retune_ack"] = seq
            if not ok:
                log.warning("nwr: capture did not start: %s (next attempt in %ds)",
                            last_error, delay)
        except Exception as exc:                # noqa: BLE001
            # Operator-supplied configuration reaches int() and list() in here:
            # a hand-edited "watch_fips": 12345, or a "channel_hz": "162.550",
            # used to end the watch for good. Back off on the same curve as a
            # failed start and keep going -- the next pass re-reads the file,
            # so a corrected one recovers on its own.
            failures += 1
            delay = retry_delay(failures, tick)
            log.exception("nwr: the watch loop failed; next attempt in %ds", delay)
            with _lock:
                _state["phase"] = "retrying"
                _state["last_error"] = str(exc) or exc.__class__.__name__
                _state["retry_failures"] = failures
                _state["next_retry"] = now() + delay
        stop_event.wait(delay)


def status(now=time.time):
    """What the card and Flask read. Never raises.

    `now` matches supervise()'s injection point: next_retry is written with
    that clock, so the seconds-remaining derived from it has to be read with
    the same one or the two disagree under any injected clock.
    """
    with _lock:
        s = dict(_state)
    label = channel_label(s.get("channel_hz"))
    live = listener.status()
    # Seconds until the next start attempt, so a retrying card can say
    # "retrying in 4m" instead of sitting on "retrying" and looking stuck.
    due = s.get("next_retry") or 0
    s.update({
        "channel": label,
        "listening": live.get("listening"),
        "subscribers": live.get("subscribers"),
        "elapsed_s": live.get("elapsed_s"),
        "retry_in_s": max(0, int(due - now())) if due else 0,
        # True from the moment a retune is accepted until the capture it asked
        # for is running. `phase` alone marks the gap for one tick; this marks
        # it for its whole length, so a card polling every few seconds cannot
        # land in the middle of a deliberate retune and read it as a decoder
        # that stopped working.
        "retune_pending": s["retune_seq"] != s["retune_ack"],
        "port": API_PORT,
    })
    return s


# ── HTTP ─────────────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass                                    # journald already has the unit's log

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/status"):
            return self._json(200, status())
        if self.path.startswith("/stream"):
            return self._stream()
        self._json(404, {"error": "not found"})

    def _drain_body(self, cap=1 << 16):
        """Consume the request body before answering.

        HTTP/1.1 keep-alive frames the NEXT request immediately after this
        one's body, so bytes left in the socket are read as a request line and
        every later request on the connection is misparsed. Nothing posted here
        carries a body worth reading, so a body past `cap` is not read at all
        and the connection is closed instead -- which is the correct framing
        for a request we are declining to finish reading.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return
        if length > cap:
            self.close_connection = True
            return
        try:
            self.rfile.read(length)
        except (OSError, ValueError):
            self.close_connection = True

    def do_POST(self):
        """Notifications, never commands.

        /retune asks the supervisor to re-read its configuration and re-derive
        its channel. It carries no frequency and cannot: the caller is Flask,
        Flask holds no radio, and the daemon stays the only thing that starts,
        stops or tunes a capture. See request_retune().
        """
        self._drain_body()
        if self.path.startswith("/retune"):
            if not REPO_ROOT:
                return self._json(503, {"ok": False, "code": "NWR_NO_ROOT",
                                        "error": "the watch has no station root"})
            try:
                payload = request_retune(REPO_ROOT)
            except Exception as exc:            # noqa: BLE001
                # settings.load() reaches list() over operator-supplied data;
                # a hand-edited nwr.json raises here exactly as it does in the
                # supervisor. Answer with the reason rather than a traceback
                # and a dropped connection -- the config write that preceded
                # this one still stands.
                log.exception("nwr: retune request failed")
                return self._json(500, {
                    "ok": False, "code": "NWR_RETUNE_FAILED",
                    "error": str(exc) or exc.__class__.__name__})
            return self._json(200, payload)
        self._json(404, {"ok": False, "error": "not found", "code": "NOT_FOUND"})

    def _stream(self):
        """Live audio as MP3.

        The encoder is fed by its OWN thread while this one only reads and
        writes. Interleaving a blocking write with a blocking read on one thread
        is what deadlocked v1: libmp3lame needs far more than one chunk before
        it emits a frame, so the writer never looped back to feed it and both
        sides waited forever. Do not merge these two loops.
        """
        import subprocess

        if not listener.is_listening():
            return self._json(409, {"error": "the watch is not running"})
        enc, mime = sdr_rx.stream_encoder(listener.SAMPLE_RATE)
        if not enc:
            return self._json(503, {"error": "no audio encoder"})
        with _lock:
            # listener.subscribe() bounds each queue's DEPTH, nothing bounds
            # the subscriber COUNT, and every stream carries its own ffmpeg.
            if _state["streams"] >= MAX_STREAMS:
                return self._json(503, {"error": "too many audio streams"})
            _state["streams"] += 1

        q = None
        proc = None
        writer = None
        try:
            # subscribe() lives INSIDE the try, like routes.py's: a queue
            # registered by a call that then raised would never be dropped,
            # and the slot counted above would never be given back -- three of
            # those and /stream answers 503 for the rest of the process's life.
            q = listener.subscribe()
            proc = subprocess.Popen(enc, shell=True, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
            # Close-delimited framing (RFC 7230 3.3.3). This response has no
            # Content-Length and is not chunked -- the length of a live stream
            # is not knowable, and the two consumers (Flask's proxy and an
            # <audio> element) both handle close-delimited already. Under
            # HTTP/1.1 CPython defaults close_connection to False, so without
            # this the handler thread would return to readline() when the
            # stream ends and block on a next request that never comes.
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            def _feed():
                try:
                    while True:
                        chunk = q.get(timeout=30)
                        if not chunk:
                            break
                        proc.stdin.write(chunk)
                        proc.stdin.flush()
                except Exception:               # noqa: BLE001 — client or encoder gone
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except Exception:           # noqa: BLE001
                        pass

            writer = threading.Thread(target=_feed, daemon=True,
                                      name="nwr-stream-writer")
            writer.start()
            while True:
                out = proc.stdout.read1(8192)
                if not out:
                    break
                self.wfile.write(out)
        except Exception:                       # noqa: BLE001 — client hung up
            pass
        finally:
            if q is not None:
                listener.unsubscribe(q)
            if writer is not None:
                # Wake a writer parked in q.get(timeout=30). unsubscribe()
                # already means no more audio reaches this queue, so hand it
                # the same poison pill listener.stop() uses instead of letting
                # the join below time out while the thread waits out its own
                # 30 s. Killing the encoder does NOT wake it -- it is blocked
                # on the queue, not on the pipe.
                listener.deliver_sentinel(q)
            if proc is not None:
                listener.terminate(proc)
            if writer is not None:
                writer.join(timeout=5)
            with _lock:
                _state["streams"] -= 1


def serve(repo_root):
    """Entry point for `install.py --serve` / the systemd unit."""
    global REPO_ROOT
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    REPO_ROOT = repo_root       # before the first request can arrive
    threading.Thread(target=supervise, args=(repo_root,), daemon=True,
                     name="nwr-supervisor").start()
    log.info("nwr: serving on 127.0.0.1:%d", API_PORT)
    ThreadingHTTPServer(("127.0.0.1", API_PORT), _Handler).serve_forever()
