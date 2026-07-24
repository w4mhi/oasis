#!/usr/bin/env python3
"""
oasis_installer_worker.py
--------------------------
The privileged half of the Setup Orchestrator's install flow.

The dashboard's web server never runs as root and has no TTY, so it cannot
run privileged installers (apt/GPG, /etc writes, systemd units, sudoers, ...)
in-process. Instead, server/app.py drops a small job file into
configuration/installer-queue/ for any privileged feature and waits for this
worker to pick it up.

This script is meant to run ONCE per invocation, triggered by the
oasis-installer.path systemd unit whenever the queue directory becomes
non-empty (see scripts/enable-oasis-installer.py). It always runs as root, so
any `sudo ...` call already inside the existing install scripts succeeds
instantly (sudo is a no-op when the caller is already root) — the exact same
install_fn used elsewhere runs completely unmodified here.

It reuses common/setup_registry.py's build_registry() as the single source of
truth for what each feature's install step does — this worker never
reimplements or duplicates that logic.

Per-job failures are reported via a `<job_id>.result.json` file, NOT via this
process's exit code — only a genuine worker crash (bad queue dir, not running
as root, etc.) should make systemd mark the oasis-installer.service unit as
failed.

Usage (root, normally via systemd — see scripts/enable-oasis-installer.py):
  sudo python3 scripts/oasis_installer_worker.py
"""

import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import config_paths
from common import installed_services
from common import removal
from common import removal_backfill
from common import setup_registry as SETUP_REGISTRY
from common.oasis_lib import _hr, _ok, _info, _warn, _fail

# Features never removable from the web/worker (see the design carve-outs).
_UNREMOVABLE = {"server", "wikipedia"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_DIR = config_paths.installer_queue_dir(REPO_ROOT)


def _write_result(job_id, result):
    result = dict(result)
    result["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result_path = os.path.join(QUEUE_DIR, f"{job_id}.result.json")
    tmp_path = result_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    os.rename(tmp_path, result_path)  # atomic on the same filesystem


def _process_remove(job_id, feature):
    """Uninstall one feature: run common/removal.py's apply() as root over the
    feature's removal record (from the manifest, regenerated if a legacy box never
    stored one), and report the changes + advisory. The route (web user) — not this
    root process — subtracts the feature from installed-services.json afterwards."""
    reg = SETUP_REGISTRY.build_registry(REPO_ROOT, payload={})
    spec = reg.get(feature)
    if feature in _UNREMOVABLE or spec is None or spec.removal_record_fn is None:
        _warn(f"{job_id}: refusing to remove non-removable feature '{feature}'")
        _write_result(job_id, {
            "ok": False,
            "reason_code": "REMOVE_FAILED",
            "reason_text": f"'{feature}' is not a removable feature",
        })
        return

    record = installed_services.removal_map(REPO_ROOT).get(feature)
    if record is None:
        record = removal_backfill.record_for(REPO_ROOT, feature) or {}

    log_path = os.path.join(QUEUE_DIR, f"{job_id}.log")

    def _log(line):
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    _info(f"{job_id}: removing '{feature}' ...")
    try:
        res = removal.apply(record, apply=True)
    except Exception as exc:
        _write_result(job_id, {"ok": False, "reason_code": "REMOVE_FAILED", "reason_text": str(exc)})
        return

    for line in res.get("changes", []):
        _log(line)
    for line in res.get("advisory", []):
        _log("advisory: " + line)

    _ok(f"{job_id}: '{feature}' removed.")
    _write_result(job_id, {
        "ok": True,
        "advisory": res.get("advisory", []),
        "changes": res.get("changes", []),
        "requires_reboot": bool(res.get("requires_reboot")),
    })


def _process_job(job_path):
    job_id = os.path.basename(job_path)[: -len(".job.json")]

    # Delete-then-run: minimize how long any credential-bearing payload (e.g. a
    # Winlink password) sits on disk, and accept at-most-once semantics — a
    # crash mid-job means the job is lost, not retried, which is safer than
    # silently re-running a partially-applied install.
    try:
        with open(job_path, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except Exception as exc:
        try:
            os.remove(job_path)
        except OSError:
            pass
        _warn(f"{job_id}: unreadable job file ({exc})")
        _write_result(job_id, {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": f"unreadable job file: {exc}"})
        return

    try:
        os.remove(job_path)
    except OSError:
        pass

    feature = data.get("feature")
    action = data.get("action") or "install"
    payload = data.get("payload") or {}

    if action == "remove":
        _process_remove(job_id, feature)
        return

    if feature not in SETUP_REGISTRY.PRIVILEGED_FEATURES:
        _warn(f"{job_id}: refusing non-privileged/unknown feature '{feature}'")
        _write_result(job_id, {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": f"'{feature}' is not a recognized privileged feature",
        })
        return

    registry = SETUP_REGISTRY.build_registry(REPO_ROOT, payload)
    spec = registry.get(feature)
    if spec is None or spec.install_fn is None:
        _warn(f"{job_id}: no install_fn for feature '{feature}'")
        _write_result(job_id, {
            "ok": False,
            "reason_code": "INSTALL_FAILED",
            "reason_text": f"no install_fn registered for '{feature}'",
        })
        return

    _info(f"{job_id}: installing '{feature}' ...")
    # Stream the install's live output to a per-job log file so the web wait-loop
    # can tail it into the setup log window (the worker's own stdout only reaches
    # the systemd journal, invisible to the browser).
    log_path = os.path.join(QUEUE_DIR, f"{job_id}.log")

    def _log_sink(line):
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    SETUP_REGISTRY.set_log_sink(_log_sink)
    try:
        result = spec.install_fn() or {"ok": True}
    except Exception as exc:
        result = {"ok": False, "reason_code": "INSTALL_FAILED", "reason_text": str(exc)}
    finally:
        SETUP_REGISTRY.set_log_sink(None)

    if result.get("ok"):
        _ok(f"{job_id}: '{feature}' installed.")
    else:
        _warn(f"{job_id}: '{feature}' failed — {result.get('reason_code')}: {result.get('reason_text')}")
    _write_result(job_id, result)


def main():
    print("\n  OASIS — installer worker (privileged)")
    _hr()
    if sys.platform != "linux":
        _fail("The installer worker only runs on Linux (systemd-triggered).")
    if os.geteuid() != 0:
        _fail("The installer worker must run as root (see scripts/enable-oasis-installer.py).")

    os.makedirs(QUEUE_DIR, exist_ok=True)
    jobs = sorted(glob.glob(os.path.join(QUEUE_DIR, "*.job.json")))
    if not jobs:
        _info("No pending install jobs.")
        return

    for job_path in jobs:
        _process_job(job_path)


if __name__ == "__main__":
    main()
