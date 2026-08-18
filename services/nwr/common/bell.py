"""Whether the weather bell speaks — opt-in, and quiet at night.

Modelled on the satellite pass-alert bell, deliberately: an operator who has
learned one of these has learned both. Default OFF, because a watch that starts
talking the moment you assign a dongle is a watch you turn off.

There is NO per-event severity filter. The bell is the gate: turning it on means
you want to hear the Required Weekly Test too, and that weekly announcement is
the only regular proof that demodulation, parsing, matching and speech all still
work. A station that has been silently broken for a month is worse than one that
says something you did not need on a Wednesday morning.

Pure: the time and the config come in as arguments, so night, the boundary and
an expiring override are all testable without touching the clock.
"""
import datetime

from common import quiet_hours


def override_until(now, repo_root):
    """Epoch at which an operator override expires: the NEXT quiet-window end.

    An override expires on its own because a stormy night is a night, not a
    change of policy. A permanent "quiet hours off" switch would sit forgotten
    for months and then surprise someone at 03:00 — state nobody remembers
    setting is state nobody remembers to undo.
    """
    _frm, to = quiet_hours.window(repo_root)
    d = now.replace(minute=0, second=0, microsecond=0)
    if now.hour >= to:
        d = d + datetime.timedelta(days=1)
    return int(d.replace(hour=to).timestamp())


def should_speak(cfg, rec, repo_root, now=None):
    """(speak, reason). `reason` says why not, for the daemon's log.

    Order matters: the cheapest and most decisive checks first, so the log line
    names the real reason rather than the first one that happened to fire.
    """
    now = now or datetime.datetime.now()

    if not (cfg or {}).get("bell"):
        return False, "bell disabled"

    if not (rec or {}).get("matched"):
        return False, "not on the watch list"

    if quiet_hours.quiet_now(repo_root, now):
        until = int((cfg or {}).get("bell_override_until") or 0)
        if until <= int(now.timestamp()):
            return False, "quiet hours"
        return True, "quiet hours overridden"

    return True, ""
