#!/usr/bin/env python3
"""
doctor.py
---------
Headless health check for an OASIS deployment.

Thin CLI over common/diagnostics.py's registry-driven check sweep. Mirrors
every check in server/system/setup.html — safe to run over SSH with no
browser, or as a post-deploy verification step.

Exit code reflects the *critical* checks (registry `critical=True` — the
comms-essential set), not merely the CORE display group:
  0  No critical check failed
  1  One or more critical checks failed

--core narrows what's *displayed* (and, with --json, what's in the emitted
"groups" list) to the CORE group only; it never changes the exit code.

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


def _critical_fail_ids(payload, critical_by_id):
    """Ids of every check that came back "fail" AND is registry-critical.

    Scans the *full* sweep (every group in payload["groups"]), not just the
    CORE display group -- this is what backs the exit-code contract: "is
    anything comms-essential broken?", independent of --core (which only
    narrows what gets printed/emitted).
    """
    return [
        r["id"]
        for group in payload["groups"]
        for r in group["checks"]
        if r["status"] == "fail" and critical_by_id.get(r["id"], False)
    ]


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

    # ── Exit-code contract ──────────────────────────────────────────────
    # doctor's exit code is a provisioning gate: nonzero iff any check the
    # registry marks critical=True (comms-essential) failed, anywhere in the
    # full sweep. This is independent of --core, which only affects what's
    # displayed/emitted below -- a critical fail outside the CORE group
    # (e.g. rtl_sdr, pat, graywolf) must still fail the gate.
    critical_by_id = {check.id: check.critical for check in D.REGISTRY}
    critical_fails = _critical_fail_ids(payload, critical_by_id)
    healthy = not critical_fails

    # ── JSON output ──
    if args.json:
        out = payload
        if args.core:
            out = dict(payload)
            out["groups"] = [g for g in payload["groups"] if g["name"] == "CORE"]
        print(json.dumps(out, indent=2))
        return 0 if healthy else 1

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
    if healthy:
        _ok("All critical checks passed.")
    else:
        _fail(f"Critical check(s) FAILED: {', '.join(critical_fails)}.")
    print()

    return 0 if healthy else 1


def main():
    ap = argparse.ArgumentParser(
        description=(
            "OASIS deployment health check.\n"
            "Mirrors server/system/setup.html — usable over SSH, no browser needed.\n\n"
            "Exit 0 = no critical (comms-essential) check failed.  Exit 1 = one did."
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
        help=(
            "Show CORE group checks only (server, webssh). Skip everything else. "
            "Display-only -- does not affect the exit code."
        ),
    )
    ap.add_argument(
        "--json", action="store_true",
        help="Output results as JSON to stdout instead of human-readable text.",
    )
    args = ap.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
