"""Speech route blueprint — synthesis over HTTP for any OASIS page.

Two endpoints, deliberately not one with a mode flag. /status is JSON under the
contract; /say returns audio, which §10 puts out of contract scope for the
success path while its ERROR paths stay fully conformant.

Server-side callers (guardian, Winlink) use common/speech.py directly. There is
no POST here because nothing needs to trigger speech remotely yet, and adding
one would pull in the CSRF guard for no benefit.
"""

import os

from flask import Blueprint, jsonify, request, send_file

from common import speech as SPEECH
from common import speech_play as PLAY

bp = Blueprint("speech", __name__)


@bp.route("/api/speech/status")
def api_speech_status():
    """What this station can say, and with what.

    §2: "Piper is not installed" is the ANSWER, not a failed request — this is
    the endpoint a model asks before deciding whether to offer to speak."""
    from app import SUITE_ROOT

    info = SPEECH.voice_info(SUITE_ROOT)
    stats = SPEECH.cache_stats(SUITE_ROOT)
    return jsonify({
        "ok":                  True,
        "available":           SPEECH.available(SUITE_ROOT),
        "voice":               info["name"],
        "model":               info["model"],
        "sample_rate_hz":      info["sample_rate_hz"],
        "player":              PLAY.player(),
        "cache_entries":       stats["entries"],
        "cache_bytes":         stats["bytes"],
        "cache_budget_bytes":  stats["budget_bytes"],
    })


@bp.route("/api/speech/say")
def api_speech_say():
    """Synthesise `text` and return the WAV.

    Cached server-side by content hash, and `conditional=True` gives the
    browser an ETag so a repeated phrase costs one 304 — but only because we
    pass an EXPLICIT etag here. Left to its default, Werkzeug derives the
    ETag from (mtime, size, filename), and SPEECH.synthesize()'s cache-hit
    path touches the file's mtime on every request (an LRU touch, so pruning
    keeps hot entries) — which would change the ETag on every request and
    defeat conditional requests entirely. The cache key already IS the
    content hash, and it's the WAV's basename without the extension, so
    reusing it costs nothing extra and is stable regardless of the touch.

    `kind` is an optional readable label for the cached filename — the hour bell
    and the greeting use it, a pass alert deliberately does not. It arrives from
    an unauthenticated LAN endpoint and becomes part of a FILENAME, so it is
    whitelisted in SPEECH.clean_kind rather than sanitised here; anything that
    fails the whitelist is dropped and the file keeps a bare hash. A bad label is
    never worth failing an announcement over.
    """
    from app import SUITE_ROOT

    try:
        path = SPEECH.synthesize(SUITE_ROOT, request.args.get("text", ""),
                                 kind=request.args.get("kind"))
    except SPEECH.SpeechRejected as e:
        return jsonify({"ok": False, "error": str(e), "code": e.code}), 400
    except SPEECH.SpeechUnavailable:
        # Never leak the engine's stderr to a browser (§3).
        return jsonify({"ok": False,
                        "error": "this station has no speech engine installed",
                        "code": "SPEECH_UNAVAILABLE"}), 503
    etag = os.path.splitext(os.path.basename(path))[0]
    return send_file(path, mimetype="audio/wav", conditional=True, etag=etag)
