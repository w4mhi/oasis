"""SAME/EAS header grammar — pure, no I/O, no clock, no subprocess.

The wire format is fixed:

    ZCZC-ORG-EEE-PSSCCC[-PSSCCC...]+TTTT-JJJHHMM-LLLLLLLL-

  ORG      originator (WXR = National Weather Service, CIV, EAS, PEP)
  EEE      event code (TOR, SVR, RWT, ...)
  PSSCCC   location: P = county subdivision (0 = whole county), SS = state
           FIPS, CCC = county FIPS (000 = the entire state). SIX digits.
  TTTT     purge time, HHMM of VALIDITY (a duration, not a wall clock)
  JJJHHMM  issue time: Julian day of year + UTC HH MM. NO YEAR — see
           derive_times().
  LLLLLLLL originating station, up to 8 chars, e.g. KSEW/NWS

Names come from the vendored dsame3 tables (services/nwr/vendor/dsame3/defs.py).
This module supplies the grammar; that file supplies the vocabulary.

Everything here is a pure function of its arguments, which is why the whole
decoder can be tested from header STRINGS with no radio, no WAV and no mocking
of the system clock.
"""
import calendar
import datetime
import os
import re
import sys
import time

# The vendored tables are a plain data module with no package of their own.
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "vendor", "dsame3")
if _VENDOR not in sys.path:
    sys.path.append(os.path.normpath(_VENDOR))
import defs  # noqa: E402  — vendored data tables, see vendor/dsame3/defs.py

# multimon-ng prefixes every decoded header with this.
MULTIMON_PREFIX = "EAS: "

# How far the derived issue time may sit from "now" before we stop trusting it.
# Three days is comfortably wider than any real propagation delay and far
# narrower than the weeks-stale clock both Pis have actually booted with.
CLOCK_TOLERANCE_S = 3 * 24 * 3600

_HEADER_RE = re.compile(
    r"ZCZC-(?P<org>[A-Z]{3})-(?P<event>[A-Z]{3})"
    r"(?P<fips>(?:-\d{6})+)"
    r"\+(?P<purge>\d{4})"
    r"-(?P<jjjhhmm>\d{7})"
    r"-(?P<station>[^-]{1,8})-?"
)


def parse_header(line):
    """Parse one raw line into a field dict, or None if it is not a SAME header.

    Accepts the line with or without multimon-ng's "EAS: " prefix. `raw` in the
    result is the header itself, never the prefix — it is what gets stored and
    what the operator sees in the decode log.
    """
    if not line:
        return None
    text = line.strip()
    if text.startswith(MULTIMON_PREFIX):
        text = text[len(MULTIMON_PREFIX):].strip()
    m = _HEADER_RE.search(text)
    if not m:
        return None
    return {
        "org":      m.group("org"),
        "event":    m.group("event"),
        "fips":     m.group("fips").lstrip("-").split("-"),
        "purge":    m.group("purge"),
        "jjjhhmm":  m.group("jjjhhmm"),
        "station":  m.group("station"),
        "raw":      m.group(0),
    }


def event_name(eee):
    """Human name for an event code, or the code itself when unknown.

    Unknown codes are normal — SAME grows — and an unrecognised warning must
    still reach the operator with its raw code rather than vanish.
    """
    return defs.SAME__EEE.get(eee) or eee


def org_name(org):
    """Human name for an originator code, or the code itself when unknown."""
    entry = (defs.SAME__ORG.get("EN") or {}).get(org)
    if isinstance(entry, dict):
        return (entry.get("NAME") or {}).get("US") or org
    return org


def _sscc(fips6):
    """PSSCCC (6) -> SSCCC (5): drop the county-subdivision digit.

    The vendored US_SAME_CODE table and our own county coordinate table are both
    keyed by the 5-digit form. This is the single place the widths are reconciled.
    """
    s = str(fips6 or "")
    return s[1:] if len(s) == 6 else s


def county_name(fips6):
    """County name for a PSSCCC code, or None.

    None is the honest answer for marine and coastal-waters pseudo-state codes
    (57/58/61/73/91 and similar): they are real alerts with no county, and the
    caller must say so rather than drop them.
    """
    return defs.US_SAME_CODE.get(_sscc(fips6))


def state_name(fips6):
    """State/territory name for a PSSCCC code, or None."""
    return defs.US_SAME_AREA.get(_sscc(fips6)[:2])


# SAME reuses the two-digit state-FIPS slot for the Great Lakes, their
# connecting rivers and coastal-waters zones (see US_SAME_AREA: "Pacific
# Coast...", "Alaskan Coast", "...Waters", "Gulf of Mexico", "Lake ...",
# "... River ..."). Every one of those entries names a body of water; every
# real state or territory entry names a place. That is the distinguishing
# fact this checks, not a guess about which codes "look" marine.
_MARINE_STATE_CODES = frozenset(
    code for code, name in defs.US_SAME_AREA.items()
    if code not in ("LOCATION", "XX")
    and any(w in name for w in ("Coast", "Waters", "Gulf of", "Lake", "River"))
)


def is_marine_state(fips6):
    """True when a PSSCCC code's state digits name a marine/coastal-waters
    zone (Great Lakes, connecting rivers, coastal waters) rather than a real
    state or territory.

    These codes structurally have no county — a decode log that reports them
    the same way as a genuine Gazetteer gap would tell the operator to doubt
    a perfectly legitimate alert.
    """
    return _sscc(fips6)[:2] in _MARINE_STATE_CODES


def _candidate_epoch(year, jjj, hh, mm):
    """UTC epoch for day-of-year `jjj` at hh:mm in `year`, or None if invalid
    (day 366 of a non-leap year)."""
    try:
        d = datetime.datetime(year, 1, 1) + datetime.timedelta(days=jjj - 1,
                                                               hours=hh, minutes=mm)
    except (ValueError, OverflowError):
        return None
    if d.year != year:
        return None
    return calendar.timegm(d.timetuple())


def derive_times(jjjhhmm, purge, now_epoch):
    """(issued, expires, clock_suspect) in UTC epoch seconds.

    JJJHHMM carries NO YEAR, so the year is inferred by picking the candidate
    (previous / current / next) whose timestamp lands nearest to `now_epoch`.
    Trying the neighbours is what makes a day-001 header heard at 23:55 on
    31 December resolve to January of the FOLLOWING year instead of the one
    just ending.

    `clock_suspect` is True when even the best candidate is more than
    CLOCK_TOLERANCE_S from now. Both OASIS Pis have booted weeks stale with
    every health check green, so the derived times are still returned and
    flagged — never silently trusted, and never dropped. A caller must not let a
    suspect `expires` retire a live warning or resurrect a dead one.

    `now_epoch` is a parameter, not a call to time.time(), so this is testable
    without mocking the clock.
    """
    jjj = int(jjjhhmm[0:3])
    hh = int(jjjhhmm[3:5])
    mm = int(jjjhhmm[5:7])
    # time.gmtime, not datetime.utcfromtimestamp — the latter is deprecated
    # from Python 3.12 and Trixie ships 3.13, so it would warn on every alert.
    now_year = time.gmtime(now_epoch).tm_year
    best = None
    for year in (now_year - 1, now_year, now_year + 1):
        cand = _candidate_epoch(year, jjj, hh, mm)
        if cand is None:
            continue
        if best is None or abs(cand - now_epoch) < abs(best - now_epoch):
            best = cand
    if best is None:                       # only reachable on a nonsense day number
        return (None, None, True)
    valid_s = int(purge[0:2]) * 3600 + int(purge[2:4]) * 60
    return (best, best + valid_s, abs(best - now_epoch) > CLOCK_TOLERANCE_S)
