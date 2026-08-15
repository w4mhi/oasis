#!/usr/bin/env python3
"""Refresh the station's perishable datasets — exactly one pass, then exit.

The background thread in the Flask process calls the same run_pass(); this CLI
exists so the logic is manually invokable and testable without a server. Both
contend for the same lock, so running this while the thread is mid-pass skips
rather than racing.

Nothing here needs the internet to SUCCEED. Offline, a pass is a handful of
instant DNS failures and the datasets stay exactly as they were — which is the
whole point: a station in the field must fail quietly and cost nothing.

Usage:
  python3 scripts/refresh-data.py                # one pass
  python3 scripts/refresh-data.py --list         # show state, fetch nothing
  python3 scripts/refresh-data.py --dry-run      # same, with the metered verdict
  python3 scripts/refresh-data.py --source tle   # one source (repeatable)
  python3 scripts/refresh-data.py --force        # ignore freshness and back-off
  python3 scripts/refresh-data.py --json         # machine-readable result
"""
import argparse
import json
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from common import refresh as R  # noqa: E402

# Fixed-width so a column of sources scans vertically.
_STATE_LABEL = {
    "fresh": "OK   ",
    "stale": "STALE",
    "deferred": "TAP  ",
    "unconfigured": "OFF  ",
    "missing": "NONE ",
}

_STATE_NOTE = {
    "deferred": "large download held back (link looks metered)",
    "unconfigured": "no API token set in configuration/station.json",
}


def _fmt_age(days):
    if days is None:
        return "never"
    if days < 1:
        return f"{days * 24:.0f}h"
    return f"{days:.1f}d"


def main():
    ap = argparse.ArgumentParser(
        description="Refresh the station's perishable datasets (one pass).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/refresh-data.py --list\n"
            "  python3 scripts/refresh-data.py --source tle\n"
            "  python3 scripts/refresh-data.py --force --source fcc\n"))
    ap.add_argument("--dry-run", action="store_true",
                    help="Evaluate and report; never fetch.")
    ap.add_argument("--source", action="append", default=None, metavar="ID",
                    help="Limit to this source id (repeatable).")
    ap.add_argument("--force", action="store_true",
                    help="Fetch regardless of freshness or back-off.")
    ap.add_argument("--list", action="store_true",
                    help="Show current state and exit (implies --dry-run).")
    ap.add_argument("--json", action="store_true",
                    help="Emit the raw result as JSON.")
    args = ap.parse_args()

    known = {s.id for s in R.REGISTRY}
    for sid in (args.source or []):
        if sid not in known:
            ap.error(f"unknown source {sid!r}; known: "
                     f"{', '.join(sorted(known))}")

    dry = args.dry_run or args.list
    with R.pass_lock(_REPO_ROOT) as got:
        if not got:
            print("another refresh pass is already running - skipping")
            return 0
        result = R.run_pass(_REPO_ROOT, now=time.time(),
                            metered=R.is_metered(), only=args.source,
                            force=args.force, dry_run=dry)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"metered link: {'yes' if result['metered'] else 'no'}"
          f"{'   (dry run - nothing fetched)' if dry else ''}")
    for row in result["sources"]:
        note = ""
        if row["fetched"]:
            note = " -> refreshed"
        elif row["error"]:
            note = f" -> {row['error']}"
        elif row["backoff_active"]:
            note = " -> backing off after repeated failures"
        elif row["state"] in _STATE_NOTE:
            note = f" -> {_STATE_NOTE[row['state']]}"
        print(f"  {_STATE_LABEL.get(row['state'], row['state'])} "
              f"{row['label']:<34} {_fmt_age(row['age_days']):>7}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
