"""NOAA Weather Radio route blueprint.

Every route is on the API contract (docs/api-contract.md): `ok` means the
request succeeded, not that the news is good. "Nothing is listening" and
"multimon-ng is not installed" are ANSWERS, not failures.

/listen/stream returns audio, which contract §10 puts out of scope on the
success path; its error paths stay fully conformant.
"""
import logging
import os
import time

from flask import Blueprint, Response, jsonify, request, send_from_directory

from common import hardware as HW
from common.api_shape import clamp_limit
from common.web_guard import require_oasis_request
from services.nwr.common import alerts, bell, counties, listener, scan, settings

log = logging.getLogger(__name__)

bp = Blueprint("nwr", __name__)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_ALERTS_DEFAULT_LIMIT = 50
_ALERTS_MAX_LIMIT = 500


def _root():
    from app import SUITE_ROOT
    return SUITE_ROOT


def _inventory():
    try:
        return HW.load(_root())
    except Exception:                      # noqa: BLE001
        return None


@bp.route("/server/nwr/")
@bp.route("/server/nwr/<path:filename>")
def nwr_static(filename="weather.html"):
    return send_from_directory(_STATIC, filename)


@bp.route("/api/nwr/status")
def api_nwr_status():
    inv = _inventory()
    return jsonify({
        "ok": True,
        "preconditions": listener.preconditions(inv=inv),
        "capture": listener.status(),
        "config": settings.load(_root()),
        "channels": [{"label": n, "hz": h} for n, h in listener.CHANNELS],
    })


@bp.route("/api/nwr/listen", methods=["POST"])
@require_oasis_request
def api_nwr_listen():
    root = _root()
    cfg = settings.load(root)
    body = request.get_json(silent=True) or {}
    hz = body.get("channel_hz", cfg["channel_hz"])
    inv = _inventory()

    pre = listener.preconditions(inv=inv)
    if pre["busy"]:
        return jsonify({"ok": False,
                        "error": f"the dongle is held by {pre['holder']}",
                        "code": "NWR_DONGLE_BUSY"}), 409

    def _on_header(parsed):
        added, rec = alerts.record(root, parsed, cfg["watch_fips"], time.time())
        if not added:
            return
        ok, why = bell.should_speak(cfg, rec, root)
        if ok:
            # `why` is only non-empty here for the override case (see
            # bell.should_speak) -- a human turned the bell back on for the
            # night, and that is the one state most worth a trail: an
            # override that fires is exactly the thing nobody should be
            # surprised by later.
            if why:
                log.info("nwr: bell override in effect for %s (%s)",
                         rec.get("event"), why)
            _announce(rec)
        else:
            log.info("nwr: bell silent for %s (%s)", rec.get("event"), why)

    result = listener.start(hz, gain=cfg["gain"], ppm=cfg["ppm"],
                            device_serial=listener.device_serial(inv),
                            on_header=_on_header)
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["error"],
                        "code": result.get("code", "NWR_START_FAILED")}), 409
    return jsonify({"ok": True, "capture": listener.status()})


def _announce(rec):
    """Speak a matched alert on this box's own speaker."""
    from services.nwr.common import announce
    announce.speak(_root(), rec)


@bp.route("/api/nwr/listen/stop", methods=["POST"])
@require_oasis_request
def api_nwr_listen_stop():
    listener.stop()
    return jsonify({"ok": True, "capture": listener.status()})


@bp.route("/api/nwr/listen/stream")
def api_nwr_stream():
    """Live audio as MP3 while a session runs.

    The subscriber MUST be removed in a finally: — a tab that closed and left
    its queue behind is a slow leak and a dongle held past its usefulness.
    `subscribe()` and the encoder `Popen` both live INSIDE that finally's
    try:, on purpose: a dongle exhausting file descriptors or losing /bin/sh
    — both more plausible on a Pi 3 than a workstation — must not leave the
    queue registered with no one left to drain it.

    Contract note (do not re-litigate this): the success path here is an
    audio stream, not JSON, so unlike a status probe it has no `ok: True`
    shape to carry an answer in. "Nothing is listening" and "no audio
    encoder" are therefore request FAILURES — `ok: False` with 409/503 — not
    answers dressed as `ok: True`. A client asked for audio and got none.
    `/api/nwr/status` is the probe for "is anything listening"; ITS success
    path is a status object with room to report that as an answer, which is
    where the contract's ok-means-the-request-succeeded rule applies to that
    question. The two routes answer different questions and are not
    inconsistent with each other.
    """
    import queue
    import subprocess
    import threading

    if not listener.is_listening():
        return jsonify({"ok": False, "error": "nothing is listening",
                        "code": "NWR_NOT_LISTENING"}), 409
    from common import sdr_rx
    enc, mime = sdr_rx.stream_encoder(listener.SAMPLE_RATE)
    if not enc:
        return jsonify({"ok": False,
                        "error": "no audio encoder (install ffmpeg or sox)",
                        "code": "NWR_NO_ENCODER"}), 503

    def _generate():
        # Writing to the encoder's stdin and reading its stdout used to
        # happen on this one generator thread: write one queued chunk,
        # then block on read1() for output. libmp3lame needs far more than
        # one 4096-byte chunk of PCM before it emits its first MP3 frame,
        # so that read1() never returned and the generator never looped
        # back to feed more input -- both sides waited forever (proven on
        # the Pi: read1() still blocked after 8s having written one
        # chunk). The fix decouples the two: a dedicated thread pumps the
        # subscriber queue into stdin, so this generator's blocking read
        # of stdout is safe -- something else keeps the encoder fed.
        q = listener.subscribe()
        proc = None
        writer = None
        try:
            proc = subprocess.Popen(enc, shell=True, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)

            def _pump_stdin():
                try:
                    while True:
                        chunk = q.get(timeout=30)
                        if not chunk:
                            break               # our own sentinel, or stop()'s
                        try:
                            proc.stdin.write(chunk)
                            proc.stdin.flush()
                        except (BrokenPipeError, OSError, ValueError):
                            break               # encoder died; nothing left to feed
                except queue.Empty:
                    pass                        # 30s of silence -- session likely ended
                finally:
                    # EOF lets the encoder flush its tail and exit on its
                    # own instead of waiting on terminate()'s SIGTERM.
                    try:
                        proc.stdin.close()
                    except Exception:            # noqa: BLE001
                        pass

            writer = threading.Thread(target=_pump_stdin, daemon=True,
                                      name="nwr-stream-writer")
            writer.start()

            # A slow or stalled client blocks HERE, at yield, not in the
            # writer. The writer keeps draining the queue into the
            # encoder, but once nothing is reading the encoder's stdout,
            # its OS pipe buffer fills, the encoder stops reading its own
            # stdin, and the writer's write() blocks too -- backpressure
            # through two bounded OS pipes (plus the bounded subscriber
            # queue), not unbounded growth.
            while True:
                out = proc.stdout.read1(8192)
                if not out:
                    break
                yield out
        except Exception:                  # noqa: BLE001 — client went away,
            pass                            # or the encoder never spawned
        finally:
            listener.unsubscribe(q)
            if writer is not None:
                # Wake a writer parked in q.get(): unsubscribe() already
                # means no more real audio will reach this queue, so hand
                # it the same poison pill listener.stop() uses rather than
                # making it wait out its own 30s timeout. Only reachable
                # once a writer exists, since only then is `q` guaranteed
                # to be a real Queue rather than an injected test double.
                listener.deliver_sentinel(q)
            # Same terminate-then-wait as listener.terminate: a killed
            # process nobody wait()s on is still a zombie. None-safe, so a
            # Popen that never spawned costs nothing here. Killing the
            # encoder also breaks a writer blocked mid-write() on a dead
            # process's stdin (EPIPE), so the join below is bounded too.
            listener.terminate(proc)
            if writer is not None:
                writer.join(timeout=5)

    return Response(_generate(), mimetype=mime)


@bp.route("/api/nwr/alerts")
def api_nwr_alerts():
    limit = clamp_limit(request.args.get("limit"), _ALERTS_DEFAULT_LIMIT,
                        _ALERTS_MAX_LIMIT)
    now = time.time()
    recs = alerts.load(_root())
    return jsonify({
        "ok": True,
        "alerts": recs[:limit],
        "active": [r["id"] for r in alerts.active(recs, now)],
        "count": len(recs),
    })


@bp.route("/api/nwr/config", methods=["GET"])
def api_nwr_config_get():
    return jsonify({"ok": True, "config": settings.load(_root())})


@bp.route("/api/nwr/config", methods=["POST"])
@require_oasis_request
def api_nwr_config_set():
    try:
        cfg = settings.save(_root(), request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e),
                        "code": "NWR_BAD_CONFIG"}), 400
    return jsonify({"ok": True, "config": cfg})


@bp.route("/api/nwr/scan", methods=["POST"])
@require_oasis_request
def api_nwr_scan():
    inv = _inventory()
    pre = listener.preconditions(inv=inv)
    if listener.is_listening():
        return jsonify({"ok": False,
                        "error": "stop listening before scanning",
                        "code": "NWR_BUSY"}), 409
    if pre["busy"]:
        return jsonify({"ok": False,
                        "error": f"the dongle is held by {pre['holder']}",
                        "code": "NWR_DONGLE_BUSY"}), 409
    cfg = settings.load(_root())
    result = scan.run(gain=cfg["gain"], ppm=cfg["ppm"],
                      device_serial=listener.device_serial(inv))
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["error"],
                        "code": result.get("code", "NWR_SCAN_FAILED")}), 503
    return jsonify({"ok": True, "powers": result["powers"],
                    "best_hz": result["best_hz"],
                    "best_dbm": result["best_dbm"]})


@bp.route("/api/nwr/counties")
def api_nwr_counties():
    return jsonify({"ok": True,
                    "counties": counties.all_counties(counties.load(_root()))})
