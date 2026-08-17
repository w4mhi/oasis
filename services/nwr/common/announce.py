"""Speak a matched alert on this box's own speaker.

Server-side, not browser-side: the sat-alert path plays through an open page's
AudioContext, which means nothing is said when no tab is open. A weather warning
that only reaches a screen somebody is already looking at is not worth building.

There is no quiet-hours gate. common/js/quiet-hours.js is client-side only, so
there is no server-side mechanism to respect, and a tornado warning arguably
should not have one. The `speak` flag in configuration/nwr.json is the off
switch.

KNOWN CONSEQUENCE, carried deliberately into v1: with no per-code severity gate,
the Required Weekly Test is spoken every week when the operator's county is in
it. During bring-up that is an asset — it is the only guaranteed end-to-end
proof the whole path works. Afterwards it is an annoyance, and a per-code gate
is the obvious v2.
"""
import logging

log = logging.getLogger(__name__)

MAX_NAMED_COUNTIES = 4     # beyond this the phrase stops being listenable


def _county_phrase(areas):
    names = []
    for a in areas or []:
        n = a.get("name")
        if not n:
            continue
        names.append(n if n.lower().endswith("county") else n + " County")
    if not names:
        return ""
    if len(names) <= MAX_NAMED_COUNTIES:
        return ", ".join(names)
    head = ", ".join(names[:MAX_NAMED_COUNTIES])
    return f"{head}, and {len(names) - MAX_NAMED_COUNTIES} more"


def phrase(rec):
    """The sentence to speak. Plain ASCII, short enough for the speech cache."""
    parts = [rec.get("event_name") or rec.get("event") or "Weather alert"]
    counties = _county_phrase(rec.get("areas"))
    if counties:
        parts.append(counties)
    expires = rec.get("expires")
    if expires and not rec.get("clock_suspect"):
        # time.gmtime, not datetime.utcfromtimestamp — deprecated from 3.12.
        import time
        parts.append("Until " + time.strftime("%H:%M UTC", time.gmtime(expires)))
    return ". ".join(parts) + "."


def speak(repo_root, rec):
    """Synthesise and play. True when a sound was produced.

    Best-effort by design: a station with no piper voice, no speaker, or a busy
    audio device must still log and plot the alert. Speech is an addition to the
    watch, never a precondition for it.
    """
    try:
        from common import speech as SPEECH
        from common import speech_play as PLAY
        if not SPEECH.available(repo_root):
            return False
        path = SPEECH.synthesize(repo_root, phrase(rec))
        return bool(PLAY.play(path))
    except Exception:                      # noqa: BLE001
        log.exception("nwr: could not announce %s", rec.get("event"))
        return False
