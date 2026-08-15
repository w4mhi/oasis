"""Pure freshness model for the OASIS auto-update loop.

PURE BY DESIGN: no I/O, no network, no clock of its own — every time value is
passed in. The whole feature is about elapsed time, so the state rules must be
testable without a network, without a Pi, and without waiting three days for a
TLE to age. Same shape as _cache_plan in the satellites pass cache.

THE FIVE STATES MUST NEVER COLLAPSE INTO ONE ANOTHER. "stale", "broken", and
"not set up" are different facts, and a UI that renders them identically teaches
the operator to ignore all three. This is the `probe capability, not artifact`
lesson: permission-denied and not-present both became False and hid real faults.
"""

FRESH = "fresh"                # age < max_age
STALE = "stale"                # past max_age, will refresh when the net returns
DEFERRED = "deferred"          # stale + large + metered/unknown -> needs a tap
UNCONFIGURED = "unconfigured"  # credentialed source, no token — OFF, not broken
MISSING = "missing"            # never fetched at all

_BACKOFF_BASE = 1800   # 30 min after the first failure
_BACKOFF_CAP = 86400   # never wait more than a day


def age_days(mtime_epoch, now_epoch):
    """Age of a dataset in days, or None if it has never been fetched.

    Clamped at zero: a future mtime (bad clock, restored backup, a box that
    booted weeks stale before the RTC took over) must not read as fresh forever
    via a negative age.
    """
    if mtime_epoch is None:
        return None
    return max(0.0, (now_epoch - mtime_epoch) / 86400.0)


def verdict(age, max_age_days, *, has_credential, needs_credential, tier,
            metered):
    """Resolve one source to exactly one state. Order matters.

    UNCONFIGURED wins over everything: a source we are not allowed to fetch has
    no meaningful age, and calling it stale would imply an action that cannot
    succeed. Then MISSING/STALE decide whether attention is needed at all, and
    only then does the metered gate downgrade a large source to DEFERRED.
    """
    if needs_credential and not has_credential:
        return UNCONFIGURED

    if age is None:
        needs_attention, base = True, MISSING
    elif age >= max_age_days:
        needs_attention, base = True, STALE
    else:
        needs_attention, base = False, FRESH

    if not needs_attention:
        return base
    if tier == "large" and metered:
        return DEFERRED
    return base


def is_due(state):
    """Should the pass attempt this source now?

    DEFERRED is deliberately NOT due — it is waiting for an operator tap, not
    for the network. UNCONFIGURED is not due — there is nothing to try.
    """
    return state in (STALE, MISSING)


def backoff_seconds(consecutive_failures):
    """Exponential back-off, capped at a day.

    Mandatory rather than hygiene: RepeaterBook's terms require immediate
    back-off on 429 and their limits are deliberately unpublished. This also
    stops a station parked offline for a month from retrying every tick.
    """
    if consecutive_failures <= 0:
        return 0
    return min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** (consecutive_failures - 1)))
