#!/usr/bin/env python3
"""
enable-oasis-installer.py
--------------------------
Enables the privileged half of the Setup Orchestrator's install flow: a
root-run, oneshot worker (scripts/oasis_installer_worker.py) that wakes up
whenever configuration/installer-queue/ has a pending job, and performs the
actual privileged install (apt/GPG, /etc writes, systemd units, sudoers, ...)
that the web dashboard cannot do itself (it has no TTY and never runs as
root).

Installs two systemd units:
  - oasis-installer.path     watches configuration/installer-queue/ and starts
                              the .service the moment a job file appears.
  - oasis-installer.service  Type=oneshot; runs oasis_installer_worker.py as
                              root, processes every pending job, then exits.

No password is ever handled by the web layer: this script asks for sudo once,
interactively, to install the units — after that, the worker runs as root via
systemd, and any `sudo ...` calls already inside the existing install scripts
succeed instantly because the caller is already root.

Opt-in and reversible. Run as your normal user; it refuses to run on
non-Linux.

Usage:
  python3 scripts/enable-oasis-installer.py            # install + enable
  python3 scripts/enable-oasis-installer.py --check    # report status
  python3 scripts/enable-oasis-installer.py --disable  # remove the units

Requires: Linux, systemd, sudo.
"""

import argparse
import getpass
import os
import pwd
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import config_paths
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
WORKER_SCRIPT = os.path.join(REPO_ROOT, "scripts", "oasis_installer_worker.py")
QUEUE_DIR = config_paths.installer_queue_dir(REPO_ROOT)

SERVICE_NAME = "oasis-installer.service"
PATH_NAME = "oasis-installer.path"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE_NAME}"
PATH_FILE = f"/etc/systemd/system/{PATH_NAME}"


def _worker_python():
    python = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "/usr/bin/python3"
    if python != VENV_PYTHON:
        _warn(f"{VENV_PYTHON} not found — using system python3.")
        _warn("If the worker fails to import common/, run first: python3 scripts/setup-server.py")
    return python


def _operator_user():
    """The real operator (not root) — same 'explicit > $SUDO_USER > current user'
    convention every install script already follows (target_user_home() in
    services/winlink/common/winlink.py, services/aprs/common/aprs.py, etc.).
    Verified against the local passwd db so a bogus value can never end up in
    a systemd unit file."""
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    try:
        pwd.getpwnam(user)
    except KeyError:
        _fail(f"Could not resolve a real system user for '{user}'.")
    return user


def _service_unit(python, operator_user):
    return (
        "[Unit]\n"
        "Description=OASIS privileged installer worker (oneshot)\n"
        "After=network.target\n"
        # Safety net: a burst of jobs (or any residual queue churn) must never
        # trip systemd's default start-limit (5 starts / 10 s) and wedge the
        # worker — that silently drops every later install job.
        "StartLimitIntervalSec=0\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={REPO_ROOT}\n"
        # This worker runs as root via systemd, not via `sudo`, so nothing sets
        # $SUDO_USER for it — every install script's 'explicit > $SUDO_USER >
        # current user' fallback would otherwise resolve to root and write
        # config (e.g. Winlink's pat config.json, with the operator's
        # password/locator) under /root/ instead of the operator's home,
        # where the web dashboard actually looks for it. Setting it here
        # restores the same behavior as the old interactive-sudo flow.
        f"Environment=SUDO_USER={operator_user}\n"
        f"ExecStart={python} {WORKER_SCRIPT}\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        # Deliberately no User= line — this worker MUST run as root so the
        # privileged install scripts it calls (apt/GPG, /etc writes, sudoers,
        # systemd units, ...) can succeed unattended.
    )


def _path_unit():
    return (
        "[Unit]\n"
        "Description=Watch OASIS installer queue for pending jobs\n"
        "\n"
        "[Path]\n"
        # Trigger ONLY on pending *.job.json files — NOT on the *.result.json the
        # worker writes back into the same dir. DirectoryNotEmpty re-fired the
        # oneshot in a tight loop on those lingering result files until systemd's
        # start-limit killed the unit, after which later jobs were never picked up.
        f"PathExistsGlob={QUEUE_DIR}/*.job.json\n"
        f"Unit={SERVICE_NAME}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _validate_unit(content, suffix):
    """Best-effort validation with systemd-analyze verify before installing.
    Skipped (not fatal) if systemd-analyze isn't available."""
    if _run(["which", "systemd-analyze"], check=False, capture_output=True).returncode != 0:
        return
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        r = _run(["systemd-analyze", "verify", tmp], check=False, capture_output=True, text=True)
        # systemd-analyze verify also reports unrelated warnings about units it
        # references (e.g. the .service from the .path file) that aren't
        # installed yet on a first run — only fail on a non-zero exit paired
        # with output that mentions THIS temp file.
        if r.returncode != 0 and tmp in (r.stderr or ""):
            _fail(f"Generated unit failed systemd-analyze verify:\n{r.stderr}")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _sudo_write(path, content):
    proc = subprocess.run(["sudo", "tee", path], input=content, text=True, capture_output=True)
    if proc.returncode != 0:
        _fail(f"Could not write {path}: {proc.stderr.strip()}")


def install():
    python = _worker_python()
    operator_user = _operator_user()
    _info(f"Operator user for privileged installs: {operator_user}")

    # The non-root web server DROPS job files here; the root worker CONSUMES them.
    # Create (or correct) the queue dir OWNED BY THE OPERATOR so the server can
    # write — a plain os.makedirs would inherit this script's ownership (root, when
    # setup runs it under sudo), and the server would hit EACCES queuing a job.
    # `install -d` also re-owns an already-root-owned dir from an earlier run, so
    # re-running this script fixes the permissions in place (idempotent).
    rc = _run(["sudo", "install", "-d", "-o", operator_user, "-m", "0775", QUEUE_DIR],
              check=False, capture_output=True, text=True)
    if rc.returncode != 0:
        _fail(f"Could not create the queue directory {QUEUE_DIR}: "
              f"{(rc.stderr or '').strip()}")
    _ok(f"Queue directory: {QUEUE_DIR}  (owner {operator_user}, mode 0775)")
    service = _service_unit(python, operator_user)
    path = _path_unit()

    _validate_unit(service, ".service")
    _validate_unit(path, ".path")

    _info(f"Writing {SERVICE_FILE}")
    _sudo_write(SERVICE_FILE, service)
    _ok(f"Wrote {SERVICE_FILE}  (runs as root, Type=oneshot)")

    _info(f"Writing {PATH_FILE}")
    _sudo_write(PATH_FILE, path)
    _ok(f"Wrote {PATH_FILE}  (watches {QUEUE_DIR})")

    _run(["sudo", "systemctl", "daemon-reload"])
    _ok("systemctl daemon-reload")
    _run(["sudo", "systemctl", "enable", "--now", PATH_NAME])
    _ok(f"systemctl enable --now {PATH_NAME}")

    r = _run(["sudo", "systemctl", "is-active", PATH_NAME], check=False, capture_output=True, text=True)
    active = (r.stdout or "").strip()
    if active == "active":
        _ok(f"{PATH_NAME} is active and watching the queue.")
    else:
        _warn(f"{PATH_NAME} status: {active}")
        _info(f"Check logs with:  journalctl -u {SERVICE_NAME} -f")


def disable():
    _run(["sudo", "systemctl", "disable", "--now", PATH_NAME], check=False)
    for f in (PATH_FILE, SERVICE_FILE):
        if os.path.exists(f):
            if _run(["sudo", "rm", "-f", f], check=False).returncode == 0:
                _ok(f"Removed {f}")
            else:
                _warn(f"Could not remove {f}")
        else:
            _ok(f"Already absent: {f}")
    _run(["sudo", "systemctl", "daemon-reload"], check=False)
    _ok("systemctl daemon-reload")


def status():
    for name, path in ((PATH_NAME, PATH_FILE), (SERVICE_NAME, SERVICE_FILE)):
        present = os.path.exists(path)
        _info(f"{name}: {'present' if present else 'absent'}  ({path})")
    r = _run(["sudo", "systemctl", "is-active", PATH_NAME], check=False, capture_output=True, text=True)
    if (r.stdout or "").strip() == "active":
        _ok(f"{PATH_NAME} is active — privileged installs will run.")
    else:
        _warn(f"{PATH_NAME} is not active. Web-based privileged installs will time out. "
              "Re-run without --check to enable.")
    _info(f"Queue directory: {QUEUE_DIR}  ({'exists' if os.path.isdir(QUEUE_DIR) else 'missing'})")


def run(args):
    print("\n  OASIS — enable-oasis-installer")
    _hr()
    if sys.platform != "linux":
        _fail("The installer worker uses systemd path units — Linux only.")
    if _run(["which", "systemctl"], check=False, capture_output=True).returncode != 0:
        _fail("systemd not found. This script requires a systemd-based OS.")

    if args.check:
        status()
        print()
        return

    if args.disable:
        _step(1, "Removing the privileged installer worker")
        disable()
        print()
        return

    _step(1, "Installing the privileged installer worker (oneshot + path trigger)")
    install()
    _info("The Setup Orchestrator's privileged installs (Winlink, GrayWolf, Kiwix, "
          "OpenWebRX, ADS-B, RTL-SDR feed, GPS, RTC, WebSSH, ...) will now complete "
          "from the web dashboard without any console interaction.")
    _info("Undo with: python3 scripts/enable-oasis-installer.py --disable")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Enable the root-run, systemd-triggered worker that performs "
                    "privileged Setup Orchestrator installs on behalf of the web dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 scripts/enable-oasis-installer.py            # install + enable\n"
                "  python3 scripts/enable-oasis-installer.py --check    # report status\n"
                "  python3 scripts/enable-oasis-installer.py --disable  # remove\n"),
    )
    ap.add_argument("--disable", action="store_true", help="Remove the units.")
    ap.add_argument("--check", action="store_true", help="Report whether the worker is enabled.")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
