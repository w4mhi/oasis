"""NOAA Weather Radio route blueprint.

Flask owns no capture. The watch is services/nwr/common/daemon.py -- its own
systemd unit, serving 127.0.0.1:8089 -- because a capture that dies with a
web-server restart, or does not come back after a reboot, is not a watch. What
is left here is a reader and a byte relay: preconditions and config are local
files Flask owns, everything about the capture is asked of the daemon, and the
alert store is READ-ONLY here (the daemon is its single writer; see its module
docstring for why two writers would drop alerts).

Every route is on the API contract (docs/api-contract.md): `ok` means the
request succeeded, not that the news is good. "The watch is not running" and
"multimon-ng is not installed" are ANSWERS on /status, not failures.

/listen/stream returns audio, which contract §10 puts out of scope on the
success path; its refusal paths stay fully conformant.
"""
import json
import os
import time

from flask import Blueprint, Response, jsonify, request, send_from_directory

from common import hardware as HW
from common.api_shape import clamp_limit
from common.web_guard import require_oasis_request
from services.nwr.common import alerts, counties, daemon, listener, settings

bp = Blueprint("nwr", __name__)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_ALERTS_DEFAULT_LIMIT = 50
_ALERTS_MAX_LIMIT = 500
# A watch list is a handful of counties, so the picker only ever needs enough
# rows to choose from -- 25 is a list you read, not one you scroll.
_COUNTIES_DEFAULT_LIMIT = 25
_COUNTIES_MAX_LIMIT = 200

WATCH_BASE = f"http://127.0.0.1:{daemon.API_PORT}"


def _root():
    from app import SUITE_ROOT
    return SUITE_ROOT


def _inventory():
    try:
        return HW.load(_root())
    except Exception:                      # noqa: BLE001
        return None


def _watch_error_text(err):
    """The daemon's own human-readable reason for a refusal, or None.

    An HTTPError IS a response: its body is a live socket, and this repo's rule
    is that every response body is consumed or discarded on every path -- an
    unread one pins its connection (on the Pi, a 2 MiB /dev/shm pipe) until the
    garbage collector happens to get to it. So read it, and close it in a
    finally: that survives a read which itself raises.
    """
    try:
        body = err.read()
    except Exception:                           # noqa: BLE001
        body = b""
    finally:
        try:
            err.close()
        except Exception:                       # noqa: BLE001
            pass
    try:
        return (json.loads(body) or {}).get("error")
    except Exception:                           # noqa: BLE001
        return None


def _watch_json(path, timeout=5):
    """(payload, error) from the watch daemon. Exactly one is non-None.

    The daemon ships as its own unit and is NOT restarted in lockstep with
    Flask, so every read is treated as potentially unavailable -- the same
    reasoning services/adsb/routes.py records for adsb-api.
    """
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(WATCH_BASE + path, timeout=timeout) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        # _watch_error_text drains and closes the body; dropping the HTTPError
        # here instead would be the one unread response in this file.
        detail = _watch_error_text(e)
        return None, (f"watch returned HTTP {e.code}"
                      + (f" ({detail})" if detail else ""))
    except Exception as e:                      # noqa: BLE001
        return None, f"watch unavailable ({getattr(e, 'reason', e)})"


def _watch_post(path, timeout=3):
    """(payload, error, reachable) from a POST to the watch daemon.

    Exactly one of payload/error is non-None. `reachable` is the answer to a
    different question than `error`: a daemon that answered 500 IS reachable
    and refused, which reads nothing like a daemon that is not running, and
    collapsing the two would tell the operator to go looking in the wrong
    place.

    Body discipline is _watch_json()'s -- urlopen()'s response is closed by the
    context manager, and an HTTPError body is drained and closed by
    _watch_error_text(). `data=b""` is what makes this a POST, and it sends
    Content-Length: 0 with it.

    The timeout is shorter than a status read's: this one is on the operator's
    click, and the daemon answers it off a lock it holds for microseconds.
    """
    import urllib.error
    import urllib.request
    req = urllib.request.Request(WATCH_BASE + path, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), None, True
    except urllib.error.HTTPError as e:
        detail = _watch_error_text(e)
        return None, (f"the watch refused the request (HTTP {e.code})"
                      + (f": {detail}" if detail else "")), True
    except Exception as e:                      # noqa: BLE001
        return None, f"the watch is not reachable ({getattr(e, 'reason', e)})", False


@bp.route("/server/nwr/")
@bp.route("/server/nwr/<path:filename>")
def nwr_static(filename="weather.html"):
    return send_from_directory(_STATIC, filename)


@bp.route("/api/nwr/status")
def api_nwr_status():
    """Local preconditions and config, plus the watch's own account of itself.

    A watch that is down is an ANSWER (contract §2), not a failure: `reachable:
    false` with a `detail` saying why, and every other field at its empty value
    so the shape never changes (§5). A station whose daemon has crashed must
    still render a dashboard that says so, rather than a 500.
    """
    inv = _inventory()
    w, err = _watch_json("/status")
    w = w or {}
    return jsonify({
        "ok": True,
        "preconditions": listener.preconditions(inv=inv),
        "watch": {
            "reachable": err is None,
            "detail": err,
            "phase": w.get("phase"),
            "listening": bool(w.get("listening")),
            "channel": w.get("channel"),
            "channel_hz": w.get("channel_hz"),
            "alerts_seen": w.get("alerts_seen") or 0,
            "last_decode": w.get("last_decode"),
            "last_error": w.get("last_error"),
            "scan": w.get("scan"),
            "scan_weak": bool(w.get("scan_weak")),
            # A retune the operator asked for is a deliberate gap, not a
            # decode failure; it stays true until the new capture is running.
            "retune_pending": bool(w.get("retune_pending")),
            "retry_in_s": w.get("retry_in_s") or 0,
            "streams": w.get("streams") or 0,
            "elapsed_s": w.get("elapsed_s") or 0,
        },
        "config": settings.load(_root()),
        "channels": [{"label": n, "hz": h} for n, h in listener.CHANNELS],
    })


@bp.route("/api/nwr/listen/stream")
def api_nwr_stream():
    """Relay the watch daemon's audio.

    Flask holds no encoder and no subscriber queue -- both live in the daemon,
    which is what keeps the v1 deadlock impossible here by construction. This is
    a byte relay and nothing more.

    Contract note (do not re-litigate this): the success path here is an audio
    stream, not JSON, so unlike a status probe it has no `ok: True` shape to
    carry an answer in. A refusal is therefore a request FAILURE -- `ok: False`
    with 409/503 -- not an answer dressed as `ok: True`. /api/nwr/status is the
    probe for "is the watch running", and ITS success path has room to report
    that as an answer.
    """
    import urllib.error
    import urllib.request
    try:
        upstream = urllib.request.urlopen(WATCH_BASE + "/stream", timeout=10)
    except urllib.error.HTTPError as e:
        # urlopen RAISES on every non-2xx, so the daemon's own refusals -- "the
        # watch is not running" (409), "no audio encoder" and "too many audio
        # streams" (503) -- all arrive here, never as a response object with a
        # status to test. Forward its reason so the page shows the real one.
        detail = _watch_error_text(e)
        if e.code == 409:
            return jsonify({"ok": False,
                            "error": detail or "the watch is not running",
                            "code": "NWR_NOT_LISTENING"}), 409
        return jsonify({"ok": False,
                        "error": detail or f"the watch refused the stream (HTTP {e.code})",
                        "code": "NWR_STREAM_UNAVAILABLE"}), 503
    except Exception as e:                      # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"the weather watch is not reachable ({getattr(e, 'reason', e)})",
                        "code": "NWR_WATCH_UNAVAILABLE"}), 503

    mime = upstream.headers.get("Content-Type", "audio/mpeg")

    def _relay():
        try:
            while True:
                try:
                    chunk = upstream.read(8192)
                except OSError:
                    # This socket gives up after 10 s of silence; the daemon's
                    # writer tolerates 30 (daemon.py's _feed). An audio gap in
                    # between must end the relay as a clean end-of-stream --
                    # letting the TimeoutError out of a WSGI iterator truncates
                    # the audio AND leaves a traceback for the operator to
                    # interpret, which says nothing they can act on.
                    break
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()                    # a client that hangs up must not
                                                # leave a subscriber on the daemon

    resp = Response(_relay(), mimetype=mime)
    # The finally: above only runs if the generator is ever STARTED, and a
    # response can be built without its body being iterated -- Werkzeug swaps in
    # an empty iterable for a HEAD. That path leaves the daemon writing into a
    # socket nobody reads: an ffmpeg, a subscriber queue and one of its three
    # stream slots, held until refcounting happens to finalise the connection.
    # call_on_close runs after iteration, or instead of it, and
    # HTTPResponse.close() is idempotent, so the two compose.
    resp.call_on_close(upstream.close)
    return resp


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
    """Write the operator's settings, then tell the watch if its channel moved.

    A pin the daemon only reads when it next happens to choose a channel is a
    dropdown that does nothing: while a capture is healthy the supervisor
    interrupts it for a due rescan and nothing else, and a healthy scan leaves
    no rescan due. So a write that touches `pinned_channel` asks the daemon to
    re-read its configuration.

    Flask ASKS. It sends no frequency and holds no radio: the daemon re-reads
    the file this route just wrote, decides for itself whether the request is
    worth a gap in the watch, and does all the starting and stopping. Selecting
    the channel already tuned comes back `accepted: false` and interrupts
    nothing.

    Contract §2: a watch that is down is an ANSWER on a config write that
    succeeded, not a failure. The file is on disk either way, and losing the
    operator's choice because a daemon is not running would be the worse of the
    two outcomes by a distance. §5: `reachable` is null -- not false -- when
    this write had no reason to ask, because "we did not ask" and "nobody
    answered" are different worlds.
    """
    patch = request.get_json(silent=True) or {}
    try:
        cfg = settings.save(_root(), patch)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e),
                        "code": "NWR_BAD_CONFIG"}), 400
    requested = "pinned_channel" in patch
    payload, err, reachable = None, None, None
    if requested:
        payload, err, reachable = _watch_post("/retune")
    return jsonify({
        "ok": True,
        "config": cfg,
        "retune": {
            "requested": requested,
            "reachable": reachable,
            "accepted": bool((payload or {}).get("retuning")),
            "detail": err or (payload or {}).get("detail"),
        },
    })


@bp.route("/api/nwr/counties")
def api_nwr_counties():
    """The SAME county table, filtered and bounded — the watch-list picker.

    Unpaginated this was the one nwr route with no `clamp_limit`: 3234 entries
    and 174,986 bytes on EVERY request, on a box whose supported minimum is a
    Pi 3, from a control that types into it. So `q` narrows (a county-name
    prefix, a name substring, a two-letter state, "king, wa", or a FIPS prefix)
    and `limit` bounds the answer.

    `fips` asks the other question — name the codes a stored watch list already
    holds — and takes precedence over `q`, because resolving a known set and
    searching for an unknown one are different requests and answering half of
    each would serve neither. Codes the table does not carry come back in
    `unknown` rather than being dropped: the four Alaskan codes this Gazetteer
    vintage lost, and every marine zone, cannot be named or plotted but are
    legitimate things to watch.

    §4: `counties` is always an array, ordered deterministically (prefix
    matches, then substring matches, each by state then name), with `total`
    counting the matches before the limit.
    """
    limit = clamp_limit(request.args.get("limit"), _COUNTIES_DEFAULT_LIMIT,
                        _COUNTIES_MAX_LIMIT)
    table = counties.load(_root())
    asked = (request.args.get("fips") or "").strip()
    if asked:
        # Bounded before it reaches resolve(): the query string is operator
        # input, and "name these 40,000 codes" is a request this route has no
        # reason to honour.
        codes = [c for c in asked.replace(" ", "").split(",") if c][:_COUNTIES_MAX_LIMIT]
        rows, unknown = counties.resolve(codes, table)
    else:
        rows, unknown = counties.search(request.args.get("q"), table), []
    return jsonify({"ok": True,
                    "counties": rows[:limit],
                    "unknown": unknown,
                    "total": len(rows),
                    "truncated": len(rows) > limit,
                    "limit": limit})
