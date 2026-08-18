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

_state = {
    "phase": "starting",     # starting | scanning | listening | retrying | stopped
    "channel_hz": None,
    "scan": None,            # last scan result, or None when pinned
    "scan_weak": False,
    "consecutive_weak": 0,
    "next_rescan": 0,
    "started": 0.0,
    "alerts_seen": 0,
    "last_decode": None,     # {station, event, at}
    "last_error": None,
}
_lock = threading.Lock()


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


# ── Decode handling ──────────────────────────────────────────────────────────

def _make_handler(repo_root):
    """The on_header callback: store, then maybe speak. Never raises -- the
    decode loop must survive an unwritable disk or a missing voice."""
    def _on_header(parsed):
        cfg = settings.load(repo_root)
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
        speak, why = bell.should_speak(cfg, rec, repo_root)
        if speak:
            announce.speak(repo_root, rec)
        else:
            log.info("nwr: not speaking %s (%s)", rec.get("event"), why)
    return _on_header


def _device_serial(repo_root):
    """The serial of the dongle assigned to nwr, or None."""
    try:
        from common import hardware as HW
        inv = HW.load(repo_root)
        dev_id = inv.assignments.get("nwr")
        dev = inv.devices.get(dev_id) if dev_id else None
        return (dev or {}).get("serial")
    except Exception:                          # noqa: BLE001
        return None


# ── Supervisor ───────────────────────────────────────────────────────────────

def supervise(repo_root, stop_event=None, tick=SUPERVISE_TICK_S):
    """Keep a capture running for as long as this process lives.

    Restarts the capture if it dies, and re-scans on a back-off while the band
    reads weak -- an antenna that gets reconnected should be picked up without
    the operator remembering to intervene.
    """
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        if listener.is_listening():
            with _lock:
                _state["phase"] = "listening"
            due = _state["next_rescan"]
            if due and time.time() >= due:
                log.info("nwr: scheduled rescan")
                listener.stop()
            stop_event.wait(tick)
            continue

        cfg = settings.load(repo_root)
        serial = _device_serial(repo_root)

        with _lock:
            _state["phase"] = "scanning"
        hz, result = choose_channel(repo_root, cfg, serial)

        with _lock:
            _state["scan"] = result
            weak = bool(result and result.get("weak"))
            _state["scan_weak"] = weak
            _state["consecutive_weak"] = (_state["consecutive_weak"] + 1) if weak else 0
            _state["next_rescan"] = (
                time.time() + rescan_delay(_state["consecutive_weak"])
                if weak else 0)

        res = listener.start(hz, gain=cfg.get("gain"), ppm=cfg.get("ppm"),
                             device_serial=serial,
                             on_header=_make_handler(repo_root))
        with _lock:
            _state["channel_hz"] = hz if res.get("ok") else None
            _state["last_error"] = res.get("error")
            _state["phase"] = "listening" if res.get("ok") else "retrying"
            if res.get("ok"):
                _state["started"] = time.time()
        if not res.get("ok"):
            log.warning("nwr: capture did not start: %s", res.get("error"))
        stop_event.wait(tick)


def status():
    """What the card and Flask read. Never raises."""
    with _lock:
        s = dict(_state)
    label = None
    for name, hz in listener.CHANNELS:
        if hz == s.get("channel_hz"):
            label = name
    live = listener.status()
    s.update({
        "channel": label,
        "listening": live.get("listening"),
        "subscribers": live.get("subscribers"),
        "elapsed_s": live.get("elapsed_s"),
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

        q = listener.subscribe()
        proc = None
        writer = None
        try:
            proc = subprocess.Popen(enc, shell=True, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
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
            listener.unsubscribe(q)
            if proc is not None:
                listener.terminate(proc)
            if writer is not None:
                writer.join(timeout=5)


def serve(repo_root):
    """Entry point for `install.py --serve` / the systemd unit."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    threading.Thread(target=supervise, args=(repo_root,), daemon=True,
                     name="nwr-supervisor").start()
    log.info("nwr: serving on 127.0.0.1:%d", API_PORT)
    ThreadingHTTPServer(("127.0.0.1", API_PORT), _Handler).serve_forever()
