"""Speak a matched alert on this box's own speaker.

Server-side, not browser-side: the sat-alert path plays through an open page's
AudioContext, which means nothing is said when no tab is open. A weather
warning that only reaches a screen somebody is already looking at is not worth
building. Best-effort throughout — see speak()'s own docstring — because a
missing voice must never cost an alert its place in the log or on the plot.

The quiet-hours gate lives ahead of this module, in services.nwr.common.bell:
by the time speak() runs, bell.should_speak() has already decided the bell may
sound, in local time (common/quiet_hours.py), against the `bell` flag in
configuration/nwr.json. (`speak` was the v1 name; settings.load() migrates it
to `bell` on read and nothing here consults it any more.) This module has
nothing left to gate on.

There is NO per-event severity filter, here or in bell.py, and none is coming:
the bell is the only gate. Turning it on means wanting to hear the Required
Weekly Test too — the one regular proof that demodulation, parsing, matching
and speech all still work end to end. A station silently broken for a month is
worse than one that says something unwanted on a Wednesday morning. A test
guards this decision (tests/test_nwr_bell.py); do not narrow it with a
per-code filter.
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
        # region_type is None (key absent) for the two cases that carry no
        # region-type information at all: the dsame3 fallback table and a
        # bare FIPS digit string, both used for codes the Gazetteer doesn't
        # carry. "County" is a deliberate default there — correct for the
        # vast majority of the lower 48, wrong only for the rare LA/AK/PR
        # code that both falls outside the Gazetteer AND isn't a county, an
        # edge case not worth widening this fallback's shape for.
        #
        # region_type == "" (explicitly bare, e.g. "District of Columbia")
        # means the stored name is already complete — say nothing more.
        #
        # Anything else is a real, looked-up region word: "Parish", "Borough",
        # "Municipality", etc. — Louisiana has no counties and Alaska's
        # boroughs and census areas aren't counties either.
        t = a.get("region_type")
        if t is None:
            names.append(n + " County")
        elif t:
            names.append(f"{n} {t}")
        else:
            names.append(n)
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
