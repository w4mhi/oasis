#!/usr/bin/env python3
"""Bump version.json under the project's per-commit SemVer policy.

Policy (adopted 2026-08-02): **every commit bumps the version.**
  - regular commit  (fix / chore / docs / style / refactor / perf / test …)
        -> patch +1                 e.g. 2.8.1 -> 2.8.2
  - feature / major modification (feat)
        -> minor +1, patch = 0      e.g. 2.8.2 -> 2.9.0
  - breaking change
        -> major +1, minor = patch = 0

`version.json` is the single source of truth (the dashboard, /api/server-info,
doctor.py all read it); this script rewrites only its `version` field and leaves
everything else (e.g. `name`) untouched. Release tags use the `v<major>.<minor>.<patch>`
form (no dot after `v`), matching v2.8.0 / v2.8.1.

Usage:
  bump-version.py patch|minor|major        # bump, write version.json, print "old -> new"
  bump-version.py --type feat|fix|chore|…  # infer level from a conventional-commit type
  bump-version.py --show                    # print current version, change nothing
  bump-version.py <level> --tag             # also print the git tag name (v<x>.<y>.<z>)
  bump-version.py <level> --dry-run         # compute + print, but don't write the file
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(_ROOT, "version.json")

# Conventional-commit type -> bump level. Anything not listed (and any non-feat,
# non-breaking type) is a patch. `feat` is the only type that moves the minor;
# a breaking change (feat!/BREAKING) must be requested explicitly as `major`.
_TYPE_LEVEL = {
    "feat": "minor",
    "fix": "patch", "chore": "patch", "docs": "patch", "style": "patch",
    "refactor": "patch", "perf": "patch", "test": "patch", "build": "patch",
    "ci": "patch", "revert": "patch",
}


def parse_version(s):
    """'2.8.1' -> (2, 8, 1). Rejects anything that isn't three dotted integers."""
    parts = str(s).strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {s!r}")
    return tuple(int(p) for p in parts)


def bump(version, level):
    """Return the next (major, minor, patch) for a bump level."""
    major, minor, patch = parse_version(version)
    if level == "major":
        return (major + 1, 0, 0)
    if level == "minor":
        return (major, minor + 1, 0)
    if level == "patch":
        return (major, minor, patch + 1)
    raise ValueError(f"unknown bump level: {level!r}")


def level_for_type(commit_type):
    """Map a conventional-commit type (with optional scope/`!`) to a bump level.
    A trailing '!' (breaking) forces major regardless of type."""
    t = str(commit_type).strip().lower()
    breaking = t.endswith("!")
    t = t.rstrip("!").split("(", 1)[0].split(":", 1)[0].strip()
    if breaking:
        return "major"
    return _TYPE_LEVEL.get(t, "patch")


def load():
    with open(VERSION_FILE) as fh:
        return json.load(fh)


def save(data):
    # Match the existing 2-space indent + trailing newline so the diff is minimal.
    with open(VERSION_FILE, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Bump version.json per the per-commit SemVer policy.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("level", nargs="?", choices=["patch", "minor", "major"],
                    help="Bump level. Omit and use --type to infer it, or --show.")
    ap.add_argument("--type", metavar="feat|fix|…",
                    help="Infer the level from a conventional-commit type "
                         "(feat -> minor, type! -> major, everything else -> patch).")
    ap.add_argument("--show", action="store_true", help="Print current version, change nothing.")
    ap.add_argument("--tag", action="store_true", help="Also print the git tag name (v<x>.<y>.<z>).")
    ap.add_argument("--dry-run", action="store_true", help="Compute + print, but don't write.")
    args = ap.parse_args(argv)

    data = load()
    current = data.get("version", "0.0.0")

    if args.show:
        print(current)
        return 0

    level = args.level or (level_for_type(args.type) if args.type else None)
    if not level:
        ap.error("give a level (patch|minor|major), or --type <commit-type>, or --show")

    new = ".".join(str(n) for n in bump(current, level))
    if not args.dry_run:
        data["version"] = new
        save(data)
    print(f"{current} -> {new}" + ("  (dry-run)" if args.dry_run else ""))
    if args.tag:
        print(f"v{new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
