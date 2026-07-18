#!/usr/bin/env python3
"""
doctor.py
---------
Headless health check for an OASIS deployment.

Thin CLI over common/diagnostics.py's registry-driven check sweep. Mirrors
every check in server/system/setup.html — safe to run over SSH with no
browser, or as a post-deploy verification step.

CORE group checks (currently: server, webssh) set the exit code:
  0  No CORE-group check failed
  1  One or more CORE-group checks failed

Usage:
  python3 scripts/doctor.py                        # all checks (incl. backlog)
  python3 scripts/doctor.py --core                 # CORE group checks only
  python3 scripts/doctor.py --json                 # machine-readable JSON
  python3 scripts/doctor.py --host HOST            # non-default host
  python3 scripts/doctor.py --host HOST --port PORT
"""

import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, REPO_ROOT)

import common.diagnostics as D

# ── Display helpers ────────────────────────────────────────────────────────────
_RESET  = "\033[0m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_BOLD   = "\033[1m"


def _c(text, code):
    """Wrap text in ANSI colour code only when stdout is a real TTY."""
    return f"{code}{text}{_RESET}" if sys.stdout.isatty() else text


def _ok(msg):   print(f"  {_c('✓', _GREEN)}  {msg}")
def _fail(msg): print(f"  {_c('✗', _RED)}  {msg}")
def _hr():      print("─" * 62)


def _section(title):
    print(f"\n  {_c(title, _BOLD)}")
    _hr()


def _status_icon_color(status):
    if status == "ok":   return "✓", _GREEN
    if status == "fail": return "✗", _RED
    return "⚠", _YELLOW  # "warn"


def _print_result(result, width=42):
    icon, color = _status_icon_color(result["status"])
    label  = result["label"]
    badge  = result["badge"]
    detail = result["detail"]

    print(f"  {_c(icon, color)}  {label:<{width}} {_c('[' + badge + ']', color)}")
    for line in detail.splitlines():
        print(f"     {line.strip()}")


def _core_checks(payload):
    """Pull just the CORE group's checks out of a run_all() payload."""
    for g in payload["groups"]:
        if g["name"] == "CORE":
            return g["checks"]
    return []


def run(args):
    host = args.host
    port = args.port

    # Attempt to pull live port config from the server (if up) before running
    # checks, so graywolf/kiwix/pat/webssh probe the right ports.
    ok, cfg = D._http_get(f"http://{host}:{port}/server-ports.json", timeout=3)
    if ok and cfg and "ports" in cfg:
        D.PORTS.update(cfg["ports"])

    # Doctor CLI always sees the full sweep including the winlink_forms
    # backlog check.
    payload = D.run_all(host, port, include_backlog=True)
    core_ok = not any(r["status"] == "fail" for r in _core_checks(payload))

    # ── JSON output — always the raw run_all() payload ──
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if core_ok else 1

    # ── Human-readable output ──
    print()
    print(f"  {_c('OASIS Doctor', _BOLD)} — {host}:{port}")
    _hr()

    if args.core:
        _section("Core")
        for result in _core_checks(payload):
            _print_result(result)
    else:
        for group in payload["groups"]:
            if not group["checks"]:
                continue
            _section(group["name"].title())
            for result in group["checks"]:
                _print_result(result)

    print()
    _hr()
    if core_ok:
        _ok("All CORE checks passed.")
    else:
        _fail("One or more CORE checks FAILED.")
    print()

    return 0 if core_ok else 1


def main():
    ap = argparse.ArgumentParser(
        description=(
            "OASIS deployment health check.\n"
            "Mirrors server/system/setup.html — usable over SSH, no browser needed.\n\n"
            "Exit 0 = no CORE-group check failed.  Exit 1 = CORE failure."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/doctor.py\n"
            "  python3 scripts/doctor.py --core\n"
            "  python3 scripts/doctor.py --json\n"
            "  python3 scripts/doctor.py --host 192.168.1.42\n"
            "  python3 scripts/doctor.py --host 192.168.1.42 --port 8083\n"
        ),
    )
    ap.add_argument(
        "--host", default="localhost",
        help="OASIS server host (default: localhost).",
    )
    ap.add_argument(
        "--port", type=int, default=D.DEFAULT_PORT,
        help=f"OASIS server port (default: {D.DEFAULT_PORT}).",
    )
    ap.add_argument(
        "--core", action="store_true",
        help="Show CORE group checks only (server, webssh). Skip everything else.",
    )
    ap.add_argument(
        "--json", action="store_true",
        help="Output results as JSON to stdout instead of human-readable text.",
    )
    args = ap.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
