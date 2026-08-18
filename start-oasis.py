#!/usr/bin/env python3
"""
start-oasis.py
---------------
Launch the OASIS Flask/gunicorn server. Ensures the venv has Flask/gunicorn/
psutil, frees port 8083 if a previous instance is still bound, then starts
gunicorn (falling back to the Flask dev server if gunicorn isn't available)
as a DETACHED background process and returns — same "fire it and hand the
console back" feel as setup-oasis.py's enable-autostart-pi.py step (which
starts the systemd service and returns immediately), just without requiring
systemd. Unlike scripts/start-server.sh (which `exec`s into gunicorn and
blocks the shell/SSH session that ran it), this script exits on its own once
the server is confirmed listening — you get your terminal back.

Before starting the server, on Linux it also makes sure the Setup
Orchestrator's two one-time privilege grants are in place:
scripts/enable-service-controls.py (dashboard start/stop/restart/reboot
buttons) and scripts/enable-oasis-installer.py (the root worker daemon that
performs privileged installs — Winlink, GrayWolf, WebSSH, ...). Both are
idempotent: the service-controls grant re-runs whenever the installed
sudoers rule no longer covers every unit we grant today (asked of sudo, not
inferred from the file's existence — an upgraded box keeps the rule it was
first granted with), the installer worker whenever its unit is absent. Each
otherwise runs once, and asks for your sudo
password right here in this terminal if it hasn't been granted yet — after
that, the dashboard never needs a password again. Skipped entirely on
non-Linux dev machines, where both scripts refuse to run anyway.

It also makes sure WebSSH (the browser terminal, ttyd) is installed — the
remote-admin lifeline a headless operator needs before they can do anything
else, and what the Setup page's own permission instructions tell you to "run
in". The install runs once (only when the unit is absent), then `webssh` is
recorded in installed-services.json so the dashboard shows its card.

Server output goes to oasis-server.log (repo root) since nothing stays
attached to this process's stdout after it exits. Re-running restarts: any
previous instance bound to the port is stopped first (same as before).

Does NOT reinstall anything beyond the venv/wheel bootstrap already handled
by scripts/setup-server.py — if the venv is missing entirely, this creates
it and installs from server/wheels/ (offline-first), matching
scripts/start-server.sh's own behavior.

Usage:
  python3 start-oasis.py
"""

import os
import platform
import socket
import subprocess
import sys
import time

REPO_ROOT   = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR  = os.path.join(REPO_ROOT, "server")
WHEELS_DIR  = os.path.join(SERVER_DIR, "wheels")
VENV_DIR    = os.path.join(REPO_ROOT, ".venv")
LOG_FILE    = os.path.join(REPO_ROOT, "oasis-server.log")
PORT        = 8083

sys.path.insert(0, REPO_ROOT)
from common.oasis_lib import _hr, _ok, _info, _warn, _fail, ensure_scripts_executable


def _venv_python():
    for name in ("python", "python3"):
        candidate = os.path.join(VENV_DIR, "bin", name)
        if os.path.exists(candidate):
            return candidate
    return None


def _venv_pip():
    return os.path.join(VENV_DIR, "bin", "pip")


def _pkg_importable(venv_python, pkg):
    return subprocess.run([venv_python, "-c", f"import {pkg}"],
                          capture_output=True).returncode == 0


def _tail(text, n=6):
    """Last n non-empty lines of captured output, for a diagnosable error."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def _platform_tag():
    """Short 'os machine cpXY' tag for diagnostics. The usual cause of an offline
    wheel-install failure is the bundle lacking a wheel for THIS platform/Python
    (e.g. 32-bit ARM: 'linux armv7l cp311' — the bundle targets 64-bit Pi OS)."""
    return (f"{sys.platform} {platform.machine()} "
            f"cp{sys.version_info.major}{sys.version_info.minor}")


def _wheel_failure_message(still_missing, stderr):
    """Diagnostic shown when the offline wheel install fails: which packages,
    the pip stderr tail, this box's platform tag, and the two ways out (rebuild
    the bundle for this target, or install online)."""
    lines = [
        "Could not install required packages from server/wheels/: "
        + ", ".join(still_missing),
        f"  platform: {_platform_tag()}",
    ]
    tail = _tail(stderr)
    if tail:
        lines.append("  pip said:")
        lines += [f"    {ln}" for ln in tail.splitlines()]
    lines += [
        "  The offline bundle may not include a wheel for this platform/Python.",
        "  Fix: rebuild the bundle for this target, or (with internet) run:",
        f"    .venv/bin/pip install {' '.join(still_missing)}",
    ]
    return "\n".join(lines)


def ensure_venv():
    """Create .venv and install flask/gunicorn/psutil from bundled wheels if
    they aren't already present — mirrors scripts/start-server.sh's
    self-sufficient bootstrap, so this script works even before
    scripts/setup-server.py has ever been run."""
    if _venv_python() is None:
        _info("No .venv found — creating one...")
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)

    venv_python = _venv_python()
    if venv_python is None:
        _fail(f"Could not create a virtual environment at {VENV_DIR}.")

    missing = [pkg for pkg in ("flask", "gunicorn", "psutil")
               if not _pkg_importable(venv_python, pkg)]
    if missing:
        _info(f"Installing missing packages from bundled wheels: {', '.join(missing)}")
        result = subprocess.run(
            [_venv_pip(), "install", "--no-index",
             "--find-links", WHEELS_DIR, *missing],
            capture_output=True, text=True,
        )
        # Never swallow a failed install: verify the packages actually import now,
        # and if not, surface pip's real error (with a platform hint) and stop —
        # a silent failure here only resurfaces as a confusing crash at startup.
        still_missing = [pkg for pkg in missing if not _pkg_importable(venv_python, pkg)]
        if result.returncode != 0 or still_missing:
            _fail(_wheel_failure_message(still_missing or missing, result.stderr or result.stdout))
        _ok(f"Installed from bundled wheels: {', '.join(missing)}")
    return venv_python


def lan_ip():
    """Best-effort primary LAN IP — same UDP-connect trick as
    server/app.py's _lan_ip() (no packets sent, works without internet).
    Kept self-contained (stdlib socket only) since this runs before the
    venv's psutil is confirmed importable."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        s.close()
    return "127.0.0.1"


def _pids_on_port(port):
    """Best-effort list of PIDs LISTENing on `port` — tries lsof, then
    fuser, then ss, matching whichever tool the host has (same fallback
    chain as scripts/start-server.sh's _pids_on_port)."""
    for cmd in (
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        ["fuser", f"{port}/tcp"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            continue
        pids = [p for p in r.stdout.replace(",", " ").split() if p.isdigit()]
        if pids:
            return pids
    try:
        r = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"],
                           capture_output=True, text=True)
        import re as _re
        return sorted(set(_re.findall(r"pid=(\d+)", r.stdout)))
    except FileNotFoundError:
        return []


def free_port(port):
    """Restart, don't just start: stop whatever already holds `port` (a
    previous OASIS instance) so this launch doesn't fail with 'address
    already in use'. Graceful TERM first, then KILL for stragglers."""
    pids = _pids_on_port(port)
    if not pids:
        return
    _info(f"Port {port} in use (PID: {' '.join(pids)}) — stopping it to restart...")
    for pid in pids:
        subprocess.run(["kill", pid], capture_output=True)
    for _ in range(10):
        time.sleep(0.5)
        if not _pids_on_port(port):
            return
    remaining = _pids_on_port(port)
    if remaining:
        _warn(f"Still running — forcing stop (PID: {' '.join(remaining)})...")
        for pid in remaining:
            subprocess.run(["kill", "-9", pid], capture_output=True)
        time.sleep(0.5)


def systemd_oasis_active():
    """True if systemd is already running the server as an active
    'oasis.service'. When it is, a manual gunicorn launch can never win: systemd
    respawns the unit the instant free_port() kills it, so the manual bind fails
    with 'address already in use'. In that case start-oasis should restart the
    UNIT, not race it. Linux only; False if systemctl or the unit is absent."""
    if sys.platform != "linux":
        return False
    try:
        r = subprocess.run(["systemctl", "is-active", "oasis.service"],
                           capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return r.stdout.strip() == "active"


def restart_systemd_oasis():
    """Restart the systemd-managed server (picks up code changes) instead of
    launching a competing gunicorn. Exits via _fail if the restart itself
    fails, so the operator gets a clear next step rather than an opaque bind
    error later."""
    _info(f"oasis.service is managing the server under systemd — restarting that "
          f"unit instead of launching a second gunicorn (which would only fight "
          f"it for port {PORT}).")
    r = subprocess.run(["sudo", "systemctl", "restart", "oasis.service"])
    if r.returncode != 0:
        _fail("`sudo systemctl restart oasis.service` failed. Run it yourself, "
              "or `sudo systemctl stop oasis` first if you really want the "
              "manual launcher.")
    _ok(f"Restarted oasis.service — open http://{lan_ip()}:{PORT}")
    _info("Logs: journalctl -u oasis -f")


# No SUDOERS_PATH constant here on purpose — the service-controls grant is
# checked by asking sudo what it permits, not by looking for the file. See
# _service_controls_current() below.
INSTALLER_PATH_UNIT = "/etc/systemd/system/oasis-installer.path"


def _run_enable_script(rel_path, already_granted):
    """Run one of the one-time enable-*.py privilege-grant scripts if it
    hasn't been applied yet. Both are idempotent and handle their own `sudo`
    call internally (see their docstrings) — run with inherited stdio so
    the password prompt shows up right here, in the terminal the operator
    is already watching. Best-effort: a failure here must not stop the
    server from starting (same tolerant style as free_port() below) — the
    dashboard's own Permissions banner will keep reporting what's missing."""
    if already_granted:
        return
    script = os.path.join(REPO_ROOT, rel_path)
    _info(f"Granting permissions: {rel_path} (may ask for your sudo password)...")
    r = subprocess.run([sys.executable, script])
    if r.returncode != 0:
        _warn(f"{rel_path} did not complete (exit {r.returncode}) — "
              f"re-run it yourself later: python3 {rel_path}")


def _service_controls_current():
    """Whether the INSTALLED sudoers grant covers the units we grant today.

    Not os.path.exists(SUDOERS_PATH). That is an artifact check, and the file
    is the artifact of *some* past grant, not of the current one — a box
    upgraded in place keeps the rule it was first granted with, so a unit added
    since (oasis-nwr) is simply never granted, and every start skips the fix
    because the file is there. Nothing surfaces it either: the dashboard's own
    Permissions banner probes a unit the old rule already covers, so it stays
    green while the console's NWR cell sits on 'assigned, stopped'.

    So ask the grant writer, which owns the unit list, whether sudo will
    actually run its newest unit's commands (see grant_is_current there — a
    `sudo -n -l` policy lookup, no execution, never prompts). Probe capability,
    not artifact: this codebase has shipped that bug four times now.

    A failure to load the script answers False, which re-runs a grant that is
    idempotent and safe. The other default — assume granted — is precisely the
    silent-healthy state this exists to end."""
    try:
        import importlib.util
        path = os.path.join(REPO_ROOT, "scripts", "enable-service-controls.py")
        spec = importlib.util.spec_from_file_location("_oasis_enable_svc", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.grant_is_current()
    except Exception:            # noqa: BLE001 — best-effort, like everything
        return False             # else in this startup path


def ensure_permissions():
    """One-time root-level grants the Setup Orchestrator needs: a narrow
    sudoers rule for the dashboard's service-control buttons, and the
    systemd worker that performs privileged installs on the dashboard's
    behalf. Both are Linux/systemd-only, opt-in, reversible, and safe to
    re-run — skipped on non-Linux dev machines, where they refuse to run
    anyway.

    The service-controls grant is re-run whenever its CONTENT is out of date,
    not merely when its file is missing; the installer unit is a single
    artifact with nothing inside it to drift, so existence still answers for
    it."""
    if sys.platform != "linux":
        return
    _run_enable_script("scripts/enable-service-controls.py",
                        already_granted=_service_controls_current())
    _run_enable_script("scripts/enable-oasis-installer.py",
                        already_granted=os.path.exists(INSTALLER_PATH_UNIT))


def ensure_webssh():
    """Make sure the browser SSH terminal (ttyd) is installed — OASIS's
    remote-admin lifeline. The Setup page's own permission-grant instructions
    read "run in WebSSH: …", so a headless operator needs it before they can do
    anything else remotely; on a freshly-imaged / factory-reset Pi it should be
    there the first time the dashboard comes up, without a manual install step.

    Idempotent: the ttyd install runs only when the unit file is absent (the
    installer is offline-first — it uses the bundled binary when present). Either
    way we then record `webssh` in installed-services.json (with its removal
    record, so it stays uninstallable) — this also back-fills the ledger on a box
    where the unit exists but was never recorded, which is exactly the
    null-manifest state that makes the dashboard show every card. Linux/systemd
    only and best-effort: a failure here must not stop the server from starting
    (same tolerant style as ensure_permissions / free_port)."""
    if sys.platform != "linux":
        return
    try:
        from common import webssh as W
        from common import installed_services
    except Exception as e:                       # pragma: no cover - import guard
        _warn(f"Could not load the WebSSH helpers ({e}) — skipping WebSSH bootstrap.")
        return

    if not os.path.exists(W.SERVICE_FILE):
        script = os.path.join(REPO_ROOT, "services/webssh/install.py")
        _info("Installing WebSSH (browser terminal) for remote admin "
              "(may ask for your sudo password)...")
        r = subprocess.run([sys.executable, script])
        if r.returncode != 0:
            _warn(f"WebSSH install did not complete (exit {r.returncode}) — "
                  f"re-run it yourself later: python3 services/webssh/install.py")

    if os.path.exists(W.SERVICE_FILE):
        try:
            installed_services.add_installed(
                REPO_ROOT, {"webssh"}, {"webssh": W.removal_record(REPO_ROOT)})
            _ok("WebSSH present and recorded in installed-services.json")
        except Exception as e:
            _warn(f"WebSSH is installed but could not be recorded in the ledger "
                  f"({e}) — the dashboard card may stay hidden until Setup runs.")


def ensure_map_downloader():
    """Make sure the go-pmtiles binary is present for the map-download feature
    (maps/routes.py: "Get more maps…"). Prefer a bundled binary from
    oasis-offline/maps/ for this platform; on a platform without one, fall back
    to fetching it (mapctl.install_pmtiles) when online. Idempotent and
    best-effort — never blocks the launch; if it can't be placed, the feature
    just reports "re-run setup" when an operator tries to download a state."""
    try:
        from maps import mapctl                      # stdlib-only module
    except Exception as e:                           # pragma: no cover - import guard
        _warn(f"Could not load mapctl ({e}) — skipping the map-downloader check.")
        return

    state_dir = os.path.join(REPO_ROOT, "maps", "tiles", "state")
    if mapctl.resolve_pmtiles(state_dir) is not None:
        return                                       # already installed / on PATH

    dest = mapctl.local_pmtiles(state_dir)           # maps/tiles/state/.bin/pmtiles
    bundled = {
        ("Linux", "aarch64"): "pmtiles-linux-arm64",
        ("Linux", "arm64"):   "pmtiles-linux-arm64",
        ("Linux", "x86_64"):  "pmtiles-linux-x86_64",
    }.get((platform.system(), platform.machine()))
    if bundled:
        src = os.path.join(REPO_ROOT, "oasis-offline", "maps", bundled)
        if os.path.isfile(src):
            try:
                import shutil
                import stat
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)
                os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                _ok(f"Map downloader ready (go-pmtiles) at {dest}")
                return
            except Exception as e:
                _warn(f"Could not place the bundled go-pmtiles binary ({e}).")

    # No bundled binary for this platform (e.g. macOS dev box) — try an online fetch.
    try:
        for _ in mapctl.install_pmtiles(state_dir):
            pass
        if mapctl.resolve_pmtiles(state_dir) is not None:
            _ok("Map downloader (go-pmtiles) fetched online.")
            return
    except Exception:
        pass
    _info("Map downloader (go-pmtiles) not installed — 'Get more maps' will be "
          "unavailable until it's bundled for this platform or fetched online.")


def main():
    print("\n  OASIS — start-oasis")
    _hr()

    # If systemd already runs the server, delegate to it rather than launching a
    # second gunicorn that would lose the race for PORT (systemd respawns the
    # unit the moment free_port kills it — the 'address already in use' trap).
    if systemd_oasis_active():
        restart_systemd_oasis()
        return

    ensure_scripts_executable(REPO_ROOT)
    ensure_permissions()
    ensure_webssh()
    ensure_map_downloader()

    venv_python = ensure_venv()
    _ok(f"Using venv Python: {venv_python}")

    free_port(PORT)

    if sys.platform == "darwin":
        # gunicorn's fork() clashes with the Objective-C runtime when any
        # ObjC class is being initialised in another thread at fork time —
        # this tells the ObjC runtime not to abort in the child process.
        os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

    if _pkg_importable(venv_python, "gunicorn"):
        # Invoke via `python -m gunicorn` rather than the .venv/bin/gunicorn
        # console-script wrapper directly — that wrapper's shebang bakes in
        # an absolute path chosen when the venv was first created, which
        # breaks if the checkout is ever moved/renamed afterward
        # (venv_python itself has no such problem — it's resolved fresh from
        # VENV_DIR on every run).
        # --workers 1: multiple gunicorn workers are separate OS processes with
        # no shared memory, but the Setup Orchestrator's plan/job state in
        # server/app.py lives in plain in-process dicts (see _setup_plans /
        # _setup_jobs) — a plan created on one worker is invisible to another,
        # causing intermittent 404 "unknown planId". Setup work already runs in
        # a background thread that doesn't block the request, so this doesn't
        # cost responsiveness.
        # --threads: diagnostics /api/diagnostics makes in-process self-HTTP
        # calls; a threaded worker serves them concurrently (sync single worker
        # would deadlock/time them out).
        cmd = [venv_python, "-m", "gunicorn", "--workers", "1", "--threads", "4",
               "--bind", f"0.0.0.0:{PORT}", "--access-logfile", "-", "app:app"]
    else:
        cmd = [venv_python, "app.py"]

    log_fh = open(LOG_FILE, "a")
    # start_new_session=True detaches the server into its own session (no
    # controlling terminal) — it keeps running after this script exits and
    # won't be killed by a SIGHUP/SIGINT sent to this console/SSH session.
    # That's the whole point: hand the console back, the way setup-oasis.py's
    # systemd step does, without requiring systemd.
    subprocess.Popen(
        cmd, cwd=SERVER_DIR, stdout=log_fh, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    log_fh.close()

    url = f"http://{lan_ip()}:{PORT}"
    _info("Waiting for it to come up...")
    for _ in range(20):
        if _pids_on_port(PORT):
            print()
            _ok(f"OASIS is up — open {url}")
            _info(f"Logs: {LOG_FILE}")
            return
        time.sleep(0.5)
    _warn(f"Started, but nothing is listening on {PORT} yet. Check {LOG_FILE} for errors.")


if __name__ == "__main__":
    main()
