"""RepeaterBook API client — the full US book as per-state JSON.

WHY THE WHOLE COUNTRY, not just the local state: OASIS is an offline emergency
tool. An operator may deploy from WA to TX and have no connectivity on arrival.
Repeaters for where you are going are only useful if they are already on the box.

WHY PER-STATE FILES, not one combined file:
  * Pi 3 memory. The full book is ~30-45k repeaters, tens of MB. One json.load()
    of that becomes hundreds of MB of Python objects on a 2 GB box.
  * It matches the API, which exports per state — one file per call.
  * Partial failure survives: a failed Texas costs Texas only, not the other 50.
  * It serves the ICS-205 goal: structured fields drop into a 205 row, and the
    picker loads only the state being operated in.

LICENSING: this fetches with the OPERATOR'S OWN token. That is the operator
exercising their own access, not redistribution. The resulting files must never
be committed or handed to another operator. Attribution "Data courtesy of
RepeaterBook.com" is required wherever the data is displayed.

RATE LIMITS are deliberately unpublished; 429 means back off immediately. The
caller therefore fetches only a few states per pass, and the first full build
completes incrementally over several passes rather than bursting 51 requests.

OBSERVED SCHEMA: not yet recorded. A live probe on 2026-08-15 returned
401 auth_invalid ("Unknown app token") for the token then on hand — the same
response a deliberately bogus token produces, so the header format is right and
the token simply is not a RepeaterBook API app token. Registration at
https://www.repeaterbook.com/api/token_request.php issues a per-user, per-app
`rbuapp_` token. Once one exists, run the probe in
specs/plans/2026-08-15-auto-update.md (Task 5, Step 1) and paste the real
top-level key and record field names here. Nothing in THIS module depends on
those names — records are stored opaquely — but the viewer does.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from common import config_paths
from common.atomic_json import read_json, write_json

# RepeaterBook rejects generic User-Agents (curl, browser defaults) and requires
# an application identifier plus a valid contact email. They approve an EXACT
# value or pattern and DENY access when the sent header does not match it.
#
# CLIENT_VERSION IS THE API-CLIENT VERSION, NOT THE OASIS RELEASE VERSION.
# Never wire it to version.json: OASIS bumps that on every commit, so an
# approved string would go stale within a day and API calls would start failing
# with 401/403 for a reason nothing in the logs would explain. Bump this only
# when the way THIS module talks to the API changes, and re-register the new
# string with RepeaterBook first if their approval is an exact match.
#
# Registered value (submitted 2026-08-15):
#   OASIS/1.0 (https://github.com/W4MHI/oasis; w4mhi@yahoo.com)
CLIENT_VERSION = "1.0"
USER_AGENT = (f"OASIS/{CLIENT_VERSION} (https://github.com/W4MHI/oasis; "
              "w4mhi@yahoo.com)")

API_BASE = "https://www.repeaterbook.com/api/export.php"

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
STATES = sorted(STATE_NAMES)


class RateLimited(Exception):
    """HTTP 429 — back off immediately, per RepeaterBook's terms."""


class AuthRejected(Exception):
    """HTTP 401/403 — the token is missing, unknown, or not an API app token.

    Distinct from a transport failure: retrying will not help, and the operator
    needs to be told to fix the token rather than watching silent retries.
    """


def fetch_state(token, state, *, opener=None):
    """Fetch one state. Returns a list of opaque record dicts.

    Raises RateLimited on 429, AuthRejected on 401/403, and ValueError on a
    non-JSON body — a captive portal answers 200 with an HTML login page, and
    that must never reach disk as if it were data.
    """
    opener = opener or urllib.request.urlopen
    qs = urllib.parse.urlencode({"country": "United States",
                                 "state": STATE_NAMES[state]})
    req = urllib.request.Request(f"{API_BASE}?{qs}", headers={
        "User-Agent": USER_AGENT,
        "X-RB-App-Token": token,
        "Accept": "application/json",
    })
    try:
        with opener(req, timeout=120) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited(f"{state}: rate limited")
        if exc.code in (401, 403):
            raise AuthRejected(f"{state}: token rejected (HTTP {exc.code})")
        raise
    try:
        data = json.loads(body)
    except ValueError:
        raise ValueError(f"{state}: response was not JSON "
                         f"(captive portal or error page?)")
    if isinstance(data, dict):
        if data.get("ok") is False:
            raise AuthRejected(f"{state}: {data.get('message') or 'refused'}")
        data = data.get("results") or data.get("data") or []
    if not isinstance(data, list):
        raise ValueError(f"{state}: unexpected payload shape")
    return data


# ── storage ──────────────────────────────────────────────────────────────────
def state_path(repo_root, state):
    return os.path.join(config_paths.repeaterbook_dir(repo_root),
                        f"{state}.json")


def _index_path(repo_root):
    return os.path.join(config_paths.repeaterbook_dir(repo_root), "index.json")


def read_index(repo_root):
    data = read_json(_index_path(repo_root), default={})
    return data if isinstance(data, dict) else {}


def write_index(repo_root, index):
    write_json(_index_path(repo_root), index)


def write_state_file(repo_root, state, records):
    """Persist one state, then update the index.

    Refuses an empty list: a US state with zero repeaters is far less likely
    than a malformed request, and replacing good data with nothing is the worst
    outcome available.
    """
    if not records:
        raise ValueError(f"{state}: refusing to write an empty record set")
    os.makedirs(config_paths.repeaterbook_dir(repo_root), exist_ok=True)
    write_json(state_path(repo_root, state), records)
    idx = read_index(repo_root)
    idx[state] = {"count": len(records), "fetched_at": time.time()}
    write_index(repo_root, idx)


def next_states(repo_root, now, max_age_days, limit):
    """Which states to fetch this pass: never-fetched first, then stalest.

    Bounded by `limit` so a pass makes steady progress instead of bursting 51
    requests at an unpublished rate limit.
    """
    idx = read_index(repo_root)
    never = [s for s in STATES if s not in idx]
    stale = [s for s in STATES
             if s in idx
             and (now - float(idx[s].get("fetched_at") or 0)) / 86400.0
             >= max_age_days]
    stale.sort(key=lambda s: float(idx[s].get("fetched_at") or 0))
    return (never + stale)[:limit]
