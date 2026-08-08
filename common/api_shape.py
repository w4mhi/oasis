"""
Shared response-shaping helpers for the OASIS API — the pieces of
docs/api-contract.md that every endpoint needs and no endpoint should
re-implement.

These lived as private copies in services/adsb/routes.py and
services/aprs/routes.py. Two copies of a timestamp formatter is how an API ends
up with two timestamp formats, which is the specific defect the contract exists
to remove; a third copy was about to appear in server/routes/system.py. One
implementation, imported everywhere, is the only version of §4 and §6 that can
stay true.

Pure functions — no Flask, no I/O, no globals.
"""

import datetime

_UTC = datetime.timezone.utc
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def iso_utc(epoch):
    """Epoch seconds -> ISO-8601 UTC (contract §6). None for an unusable value.

    Sub-second precision is deliberately dropped: nothing OASIS reports —
    aircraft positions, station beacons, boot time — is meaningful below a
    second, and the digits are pure noise in a response a model has to read.
    """
    try:
        return datetime.datetime.fromtimestamp(float(epoch), _UTC).strftime(_ISO)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def iso_utc_from_text(value):
    """A textual timestamp -> ISO-8601 UTC 'Z' (contract §6), or None.

    Written for GrayWolf, which emits Go's default format —
    '2026-08-07 20:08:04.45893926-07:00': space separated, nanosecond
    precision, local offset. That is why common/js/traffic-list.js's
    lastHeardEpoch() ran three string replaces plus a Date parse PER ROW over
    ~1600 rows on every render. Normalising once on the server deletes that
    work from every consumer.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" ", "T")
    # fromisoformat handles at most microseconds; GrayWolf sends nanoseconds.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        for ch in tail:
            if not ch.isdigit():
                break
            digits += ch
        text = head + tail[len(digits):]        # drop sub-second entirely
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC).strftime(_ISO)


def clamp_limit(raw, default, maximum):
    """Contract §4: a nonsense `limit` degrades to the default, never 500s and
    never quietly returns everything. An unbounded list is an unbounded response
    on a Pi 3 serving a browser over Wi-Fi."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return min(value, maximum)
