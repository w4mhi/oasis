"""When the shack is asleep — the Python half.

The window itself lives in common/quiet-hours.json, NOT here. The JavaScript
half (common/js/quiet-hours.js) reads the same file, and its docstring explains
why that matters: two copies of "22 to 07" is how one alarm goes silent at 06:00
while another is still chiming.

LOCAL time, deliberately, for the reason that file gives: quiet hours are about
when the operator is asleep, which no other clock knows. Read off UTC they would
silence the shack from 05:00 to 14:00 local at UTC-7 — an inversion that reads
as a timezone bug rather than the design mistake it would be.

Pure enough to test: `quiet_at` takes the hour, `quiet_now` takes the time.
"""
import json
import os

# Used only when the shared file is missing or unreadable. A damaged install
# must still decide something sane rather than raise inside an alert handler.
FALLBACK = (22, 7)

_REL = os.path.join("common", "quiet-hours.json")


def window(repo_root):
    """(from_hour, to_hour) from the shared definition, or FALLBACK."""
    try:
        with open(os.path.join(repo_root, _REL), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return (int(data["from"]), int(data["to"]))
    except (OSError, ValueError, KeyError, TypeError):
        return FALLBACK


def quiet_at(local_hour, span=None):
    """True when `local_hour` falls inside the quiet window.

    `span` (not `window`: the module already has a function of that name, and
    a same-named parameter would shadow it inside this body) is None or an
    explicit (from_hour, to_hour) pair. That is checked with `is None`, not
    truthiness — `span or FALLBACK` would quietly read a malformed or empty
    argument as 22:00-07:00 instead of raising, and a caller passing a bad
    span deserves a loud unpacking error, not a fallback it never asked for.

    The window spans midnight, so this is an OR and not a range test.
    """
    frm, to = FALLBACK if span is None else span
    return local_hour >= frm or local_hour < to


def quiet_now(repo_root, now=None):
    """True when it is currently quiet hours, local time."""
    import datetime
    now = now or datetime.datetime.now()
    return quiet_at(now.hour, window(repo_root))
