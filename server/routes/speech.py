"""Speech route blueprint — synthesis over HTTP for any OASIS page.

Two endpoints, deliberately not one with a mode flag. /status is JSON under the
contract; /say returns audio, which §10 puts out of contract scope for the
success path while its ERROR paths stay fully conformant.

Server-side callers (guardian, Winlink) use common/speech.py directly. There is
no POST here because nothing needs to trigger speech remotely yet, and adding
one would pull in the CSRF guard for no benefit.
"""

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
    browser an ETag so a repeated phrase costs one 304."""
    from app import SUITE_ROOT

    try:
        path = SPEECH.synthesize(SUITE_ROOT, request.args.get("text", ""))
    except SPEECH.SpeechRejected as e:
        return jsonify({"ok": False, "error": str(e), "code": e.code}), 400
    except SPEECH.SpeechUnavailable:
        # Never leak the engine's stderr to a browser (§3).
        return jsonify({"ok": False,
                        "error": "this station has no speech engine installed",
                        "code": "SPEECH_UNAVAILABLE"}), 503
    return send_file(path, mimetype="audio/wav", conditional=True)
