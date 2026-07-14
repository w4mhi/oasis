#!/usr/bin/env python3
"""
start-oasis.py
---------------
Launch the OASIS Flask/gunicorn server. Ports the launch sequence from
scripts/start-server.sh (the bash launcher setup-oasis.py points users at)
into a single top-level Python entry point — same behavior: ensure the venv
has Flask/gunicorn/psutil, free port 8083 if a previous instance is still
bound, then run gunicorn (falling back to the Flask dev server if gunicorn
isn't available), printing the URL to open before the server takes over the
process.

Does NOT reinstall anything beyond the venv/wheel bootstrap already handled
by scripts/setup-server.py — if the venv is missing entirely, this creates
it and installs from server/wheels/ (offline-first), matching
scripts/start-server.sh's own behavior.

Usage:
  python3 start-oasis.py
"""

import os
import socket
import subprocess
import sys
import time

REPO_ROOT   = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR  = os.path.join(REPO_ROOT, "server")
WHEELS_DIR  = os.path.join(SERVER_DIR, "wheels")
VENV_DIR    = os.path.join(REPO_ROOT, ".venv")
PORT        = 8083

sys.path.insert(0, REPO_ROOT)
from common.oasis_lib import _hr, _ok, _info, _warn, _fail


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
        subprocess.run(
            [_venv_pip(), "install", "--quiet", "--no-index",
             "--find-links", WHEELS_DIR, *missing],
            capture_output=True,
        )
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


def main():
    print("\n  OASIS — start-oasis")
    _hr()

    venv_python = ensure_venv()
    _ok(f"Using venv Python: {venv_python}")

    free_port(PORT)

    if sys.platform == "darwin":
        # gunicorn's fork() clashes with the Objective-C runtime when any
        # ObjC class is being initialised in another thread at fork time —
        # this tells the ObjC runtime not to abort in the child process.
        os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

    url = f"http://{lan_ip()}:{PORT}"
    print()
    _ok(f"Starting OASIS — open {url} once it's up")
    print()
    # os.execv() below replaces this process image without flushing Python's
    # buffered stdout first — without this, every message above (including
    # the URL, the actual point of this script) silently vanishes whenever
    # stdout isn't a TTY (e.g. redirected to a log file).
    sys.stdout.flush()

    os.chdir(SERVER_DIR)
    if _pkg_importable(venv_python, "gunicorn"):
        # Invoke via `python -m gunicorn` rather than exec'ing the
        # .venv/bin/gunicorn console-script wrapper directly — that wrapper's
        # shebang bakes in an absolute path chosen when the venv was first
        # created, which breaks if the checkout is ever moved/renamed
        # afterward (venv_python itself has no such problem — it's resolved
        # fresh from VENV_DIR on every run).
        os.execv(venv_python, [
            venv_python, "-m", "gunicorn", "--workers", "2",
            "--bind", f"0.0.0.0:{PORT}", "--access-logfile", "-", "app:app",
        ])
    else:
        os.execv(venv_python, [venv_python, "app.py"])


if __name__ == "__main__":
    main()
