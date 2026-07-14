#!/usr/bin/env python3
"""
setup-server.py
---------------
Create the Python virtual environment and install all OASIS server
dependencies. Package list is read from scripts/offline-manifest.json
(server feature, type=pypi).

Install source is chosen automatically — wheels-first:
  • server/wheels/ populated -> install from wheels (PyPI as fallback per-package).
  • server/wheels/ empty, internet present -> install from PyPI.
  • server/wheels/ empty, no internet -> hard fail with instructions.

Dependency installs are best-effort: if a package is missing or fails to
install, the error is logged to the console and setup continues with the rest.

What this does:
  1. Verifies Python 3.9+
  2. Creates .venv in the repo root (if not already present)
  3. Installs the dependencies from the manifest (server feature)
  4. Installs system emoji + mono fonts (Raspberry Pi / Linux, online only)

Usage:
  python3 scripts/setup-server.py
  python3 scripts/setup-server.py --check     # report component status
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import server as S
from common import setup_engine as SE

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Set up the OASIS Python server environment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/setup-server.py         # local wheels if present, else PyPI\n"
      "  python3 scripts/setup-server.py --check # report what is installed / missing\n"
      "  python3 scripts/setup-server.py --plan --features server,service-controls\n"
      "  python3 scripts/setup-server.py --json --features server\n"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether all components are ready; prompt to fix missing ones.",
    )
    parser.add_argument(
        "--features",
        help="Comma-separated feature keys to run (default: server).",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print resolved execution order and blockers; do not execute.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit NDJSON events for machine-readable progress and summary.",
    )
    parser.add_argument(
        "--job-id",
        help="Optional correlation id included in emitted events.",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Accepted for parity; setup installs/enables but does not start services.",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Accepted for parity; execution is always one feature at a time.",
    )
    return parser.parse_args(argv)


def _run_server_install():
    try:
        S.run(check_mode=False, repo_root=REPO_ROOT)
        return {"ok": True}
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": f"server setup exited with code {code}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": str(exc),
        }


def _run_service_controls_install():
    script = os.path.join(REPO_ROOT, "scripts", "enable-service-controls.py")
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True)
    except OSError as exc:
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": str(exc),
        }
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "service-controls install failed").strip()
        return {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": err.splitlines()[-1][:300] if err else "service-controls install failed",
            "stderr_tail": (r.stderr or "").strip()[-300:] or None,
            "stdout_tail": (r.stdout or "").strip()[-300:] or None,
        }
    return {"ok": True}


def build_cli_registry(repo_root):
    # First slice: foundation path only.
    del repo_root
    return {
        "server": SE.FeatureSpec(
            key="server",
            dependencies=[],
            install_fn=_run_server_install,
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
        "service-controls": SE.FeatureSpec(
            key="service-controls",
            dependencies=["server"],
            install_fn=_run_service_controls_install,
            verify_fn=lambda: {"ok": True},
            enable_policy="none",
        ),
    }


def _iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_human_event(event):
    kind = event.get("event")
    if kind == "job_started":
        feats = ", ".join(event.get("orderedFeatures", []))
        print(f"[setup] job started: {feats}")
    elif kind == "feature_started":
        print(f"[setup] feature: {event.get('feature')}")
    elif kind == "stage_started":
        print(f"  - {event.get('stage')}")
    elif kind == "feature_terminal":
        print(f"  -> {event.get('status')}")
    elif kind == "feature_skipped":
        print(f"[setup] skipped {event.get('feature')}: {event.get('reasonText')}")
    elif kind == "job_finished":
        print(f"[setup] finished (ok={event.get('ok')}, exit={event.get('exitCode')})")


def emit_json_event(event, stream=None):
    out = dict(event)
    out.setdefault("schemaVersion", "1.0")
    out.setdefault("ts", _iso_now())
    if stream is None:
        stream = sys.stdout
    stream.write(json.dumps(out, sort_keys=True) + "\n")


def _parse_features(raw):
    if not raw:
        return ["server"]
    return [p.strip() for p in raw.split(",") if p.strip()]


def main(argv=None):
    args = parse_args(argv)

    if args.check:
        S.run(check_mode=True, repo_root=REPO_ROOT)
        return 0

    registry = build_cli_registry(REPO_ROOT)
    selected = _parse_features(args.features)
    plan = SE.resolve_plan(selected, registry)

    if args.plan:
        if args.json:
            emit_json_event({
                "event": "plan",
                "jobId": args.job_id or "setup-plan-cli",
                "selectedFeatures": plan.selected_features,
                "orderedFeatures": plan.ordered_features,
                "blocked": plan.blocked,
            })
        else:
            print("Selected:", ", ".join(plan.selected_features) or "(none)")
            print("Order:   ", ", ".join(plan.ordered_features) or "(none)")
            if plan.blocked:
                print("Blocked:")
                for b in plan.blocked:
                    print(f"  - {b.get('reason_text')}")
        return 1 if plan.blocked else 0

    run_opts = SE.RunOptions(
        sequential=True,
        auto_start_services=False,
        job_id=args.job_id or "setup-job-cli",
    )

    sink = (lambda e: emit_json_event(e)) if args.json else emit_human_event
    _states, _blocked, summary = SE.run_plan(plan, run_opts, registry, event_sink=sink)
    return SE.terminal_exit_code(summary)



if __name__ == "__main__":
    sys.exit(main())
