#!/usr/bin/env python3
"""
install-webssh.py
-----------------
Install a browser-based SSH/terminal on Linux (Debian/Ubuntu/Raspberry Pi OS),
exposing a login shell in a browser over WebSocket. This installs `ttyd`, which
wraps a login shell in a pseudo-terminal and streams it to the browser over a
WebSocket (xterm.js front-end) — no PuTTY or SSH client needed.

The page prompts for a username and hands off to `ssh user@localhost`, so users
authenticate with their normal Raspberry Pi credentials (PAM, via sshd). We use
ssh rather than `/bin/login` because login's vhangup() breaks ttyd's pty and the
password prompt never shows. Requires sshd running. Optionally add an extra HTTP
Basic Auth gate in front with --basic-auth.

ttyd is NOT in the Debian/Raspberry Pi OS stable repos, so this installs the
upstream prebuilt static binary (a single self-contained file per architecture)
to /usr/local/bin/ttyd — no apt, no libwebsockets/libuv dependencies.

What this does:
  1. Detects platform/architecture
  2. Installs the `ttyd` static binary to /usr/local/bin/ttyd
     (from offline-packages/webssh/ttyd.<arch> if bundled, else downloads from GitHub)
  3. Creates a systemd service on port 7681 (ttyd default port)
  4. Enables and starts the service

After install, open http://<pi-ip>:7681 and log in.

Usage:
  python3 scripts/install-webssh.py
  python3 scripts/install-webssh.py --port 7681
  python3 scripts/install-webssh.py --basic-auth          # prompt for extra gate
  python3 scripts/install-webssh.py --basic-auth admin:s3cret
  python3 scripts/install-webssh.py --bind 127.0.0.1      # localhost only

Requires: Linux, sudo. Internet optional if bundled binary is present.
Security: serves plain HTTP. Keep it on your trusted LAN/Wi-Fi side, not on an
          open RF link or the public internet without a TLS reverse proxy.
Project:  https://github.com/tsl0922/ttyd
"""

import argparse
import getpass
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

# ── Shared library ───────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run,
    binary_version, version_decision,
)

SERVICE      = "webssh"
DEFAULT_PORT = 7681
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"
LOGIN_SCRIPT = "/usr/local/bin/oasis-webssh-login"
INSTALL_BIN  = "/usr/local/bin/ttyd"
REPO_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFLINE_DIR  = os.path.join(REPO_ROOT, "offline-packages", "webssh")

# ttyd is NOT in Debian/Raspberry Pi OS stable repos, so we install the
# upstream prebuilt static binary (single self-contained file per arch —
# no libwebsockets/libuv runtime deps). See github.com/tsl0922/ttyd/releases.
TTYD_VERSION = "1.7.7"
TTYD_ARCH = {
    "aarch64": "aarch64",   # Pi 3/4/5, Zero 2 W (64-bit OS)
    "arm64":   "aarch64",
    "armv7l":  "armhf",     # Pi 2/3/4 (32-bit OS)
    "armhf":   "armhf",
    "armv6l":  "arm",       # Pi 1 / Zero / Zero W
    "x86_64":  "x86_64",
    "amd64":   "x86_64",
    "i686":    "i686",
    "i386":    "i686",
}





def _ttyd_suffix():
    """Return the ttyd release asset suffix for this machine, or None."""
    return TTYD_ARCH.get(platform.machine())


def find_local_ttyd_binary(suffix):
    """
    Return the path to a bundled static ttyd binary for this arch, or None.
    Expected filename: offline-packages/webssh/ttyd.<suffix> (e.g. ttyd.aarch64).
    macOS AppleDouble sidecars (._*) are ignored.
    """
    if not suffix or not os.path.isdir(OFFLINE_DIR):
        return None
    cand = os.path.join(OFFLINE_DIR, f"ttyd.{suffix}")
    if os.path.isfile(cand) and not os.path.basename(cand).startswith("._"):
        return cand
    return None


# ── Step 1: Platform check ─────────────────────────────────────────────────────
def check_platform():
    _step(1, "Checking platform")

    if sys.platform != "linux":
        _fail("This installs the Linux web terminal (ttyd).\n"
              "       For macOS: brew install ttyd")

    machine = platform.machine()
    _info(f"Architecture: {machine}")
    if not _ttyd_suffix():
        _warn(f"No prebuilt ttyd binary is published for '{machine}'.")
        _warn("See https://github.com/tsl0922/ttyd/releases for available builds.")
    else:
        _ok(f"Architecture → ttyd asset: ttyd.{_ttyd_suffix()}")


# ── Step 2: Install ttyd ───────────────────────────────────────────────────────
def _download_ttyd(suffix, dest):
    """Download the ttyd static binary to dest. Returns True on success."""
    url = f"https://github.com/tsl0922/ttyd/releases/download/{TTYD_VERSION}/ttyd.{suffix}"
    _info(f"Downloading ttyd {TTYD_VERSION} ({suffix}) ...")
    _info(f"  {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "oasis-emcomm"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        return os.path.getsize(dest) > 0
    except Exception as exc:
        _warn(f"Download failed: {exc}")
        return False


def _install_binary(src):
    """Install src to /usr/local/bin/ttyd (mode 755) via sudo. Returns bool."""
    return _run(
        ["sudo", "install", "-m", "755", src, INSTALL_BIN], check=False
    ).returncode == 0


def install_ttyd():
    """Install ttyd best-effort: bundled binary first, then GitHub download."""
    _step(2, "Installing ttyd")

    existing = _run(["which", "ttyd"], check=False, capture_output=True, text=True)
    if existing.returncode == 0:
        path = existing.stdout.strip()
        inst = binary_version([path, "--version"])
        # Only re-install if our bundled/downloaded version is newer.
        if version_decision("ttyd", TTYD_VERSION, inst) == "skip":
            return path
        # else fall through and install the newer binary to /usr/local/bin

    suffix = _ttyd_suffix()
    if not suffix:
        _warn("No prebuilt ttyd binary for this architecture — cannot install.")
        return None

    local     = find_local_ttyd_binary(suffix)
    installed = False

    if local:
        _info(f"Using bundled binary: {os.path.relpath(local)}")
        installed = _install_binary(local)
    else:
        _info(f"No bundled binary in {os.path.relpath(OFFLINE_DIR)}/ — downloading from GitHub ...")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_bin = os.path.join(tmp, f"ttyd.{suffix}")
            if _download_ttyd(suffix, tmp_bin):
                installed = _install_binary(tmp_bin)

    if not installed:
        _fail(
            f"ttyd could not be installed from the bundled binary or GitHub.\n"
            f"       Manual install: download ttyd.{suffix} from\n"
            f"         https://github.com/tsl0922/ttyd/releases\n"
            f"       then: sudo install -m755 ttyd.{suffix} {INSTALL_BIN}"
        )

    _ok(f"ttyd installed → {INSTALL_BIN}")
    ver = _run([INSTALL_BIN, "--version"], check=False, capture_output=True, text=True)
    line = (ver.stdout or ver.stderr).strip()
    if line:
        _ok(f"Version: {line.splitlines()[0]}")
    return INSTALL_BIN


# ── Step 3: Resolve optional Basic Auth credential ─────────────────────────────
def resolve_basic_auth(arg):
    """
    arg is:
      None             -> no extra HTTP Basic Auth (login program is the only gate)
      "" (flag, empty) -> prompt interactively for user:pass
      "user:pass"      -> use as given
    Returns a "user:pass" string or None.
    """
    if arg is None:
        return None

    if arg:
        if ":" not in arg:
            _fail("--basic-auth must be in the form USER:PASSWORD")
        return arg

    # Flag passed with no value → prompt.
    user = input("    HTTP Basic Auth username [admin]: ").strip() or "admin"
    pw1  = getpass.getpass("    HTTP Basic Auth password: ")
    if not pw1:
        _fail("Empty password. Re-run without --basic-auth to skip this gate.")
    pw2 = getpass.getpass("    Confirm password: ")
    if pw1 != pw2:
        _fail("Passwords did not match.")
    return f"{user}:{pw1}"


# ── Step 4: Create systemd service ─────────────────────────────────────────────
# The login helper and unit are built by pure functions so they can be shown
# verbatim by --dry-run and re-derived by verify_service() — no hidden state.
def build_login_script():
    """Return the /usr/local/bin login helper text. Pure (no side effects).

    Auth is ssh-to-localhost in its own script because: (1) /bin/login's
    vhangup() tears down ttyd's pty so its password prompt never shows, and
    (2) putting the command inline in ExecStart lets systemd expand "$u" (it
    does $-expansion even inside single quotes) to empty → ssh as root.
    """
    return (
        "#!/bin/bash\n"
        "# OASIS Web SSH login — generated by install-webssh.py\n"
        "# Prompt for a username, then hand off to ssh on localhost (PAM auth).\n"
        "read -rp 'login: ' u\n"
        'exec ssh -o StrictHostKeyChecking=accept-new "$u@localhost"\n'
    )


def build_unit(ttyd_bin, port, bind, basic_auth):
    """Return (exec_start, unit_text) for the systemd service. Pure."""
    cmd = [ttyd_bin, "-W", "-p", str(port)]
    if bind:
        cmd += ["-i", bind]
    if basic_auth:
        cmd += ["-c", basic_auth]
    cmd += [LOGIN_SCRIPT]
    exec_start = " ".join(cmd)
    unit = f"""[Unit]
Description=Web SSH terminal (ttyd) — OASIS
After=network.target ssh.service
Wants=ssh.service

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    return exec_start, unit


def _sudo_write(path, content, mode):
    proc = subprocess.Popen(["sudo", "tee", path],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(content.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {path}")
    _run(["sudo", "chmod", mode, path], check=False)


def create_service(ttyd_bin, port, bind, basic_auth):
    _step(4, "Creating systemd service")

    _sudo_write(LOGIN_SCRIPT, build_login_script(), "755")
    _ok(f"Login helper: {LOGIN_SCRIPT}")
    _info("Auth: ssh to localhost — your normal Pi username + password")
    if basic_auth:
        _info("Extra HTTP Basic Auth gate: enabled")

    _, unit = build_unit(ttyd_bin, port, bind, basic_auth)
    _sudo_write(SERVICE_FILE, unit, "640")   # 640: ExecStart may hold credentials
    _ok(f"Service file: {SERVICE_FILE}")

    _run(["sudo", "systemctl", "daemon-reload"])
    _ok("systemctl daemon-reload")
    _run(["sudo", "systemctl", "enable", "--now", SERVICE])
    _ok(f"systemctl enable --now {SERVICE}")

    verify_service(ttyd_bin, port, bind, basic_auth)


# ── Step 5: Verify the LIVE install matches what we intended ───────────────────
def verify_service(ttyd_bin, port, bind, basic_auth):
    """Read back the running system and confirm it matches our generated config.

    This catches the exact failure we hit by hand: a stale webssh.service still
    running bare `login` while the repo/script had moved on.
    """
    _step(5, "Verifying installation")
    ok = True

    # 1. Helper script present + executable.
    if _run(["test", "-x", LOGIN_SCRIPT], check=False).returncode == 0:
        _ok(f"Login helper present & executable: {LOGIN_SCRIPT}")
    else:
        _warn(f"Login helper missing or not executable: {LOGIN_SCRIPT}"); ok = False

    # 2. The LIVE unit's ExecStart actually runs the helper (not bare `login`).
    live = _run(["systemctl", "show", "-p", "ExecStart", "--value", SERVICE],
                check=False, capture_output=True, text=True).stdout
    if LOGIN_SCRIPT in live:
        _ok("Live ExecStart runs the login helper (not bare `login`)")
    else:
        _warn("Live ExecStart does NOT reference the helper — unit is stale!")
        _info(f"  expected to contain: {LOGIN_SCRIPT}")
        _info(f"  systemctl reports:   {live.strip()[:160] or '(empty)'}")
        ok = False

    # 3. Service active.
    status = _run(["systemctl", "is-active", SERVICE],
                  check=False, capture_output=True, text=True).stdout.strip()
    if status == "active":
        _ok(f"{SERVICE} is active")
    else:
        _warn(f"{SERVICE} status: {status}   (journalctl -u {SERVICE} -e)"); ok = False

    # 4. sshd active — login depends on it.
    if _run(["systemctl", "is-active", "ssh"], check=False,
            capture_output=True, text=True).stdout.strip() == "active":
        _ok("sshd is active (required for login)")
    else:
        _warn("sshd is not active — enable with:  sudo systemctl enable --now ssh"); ok = False

    # 5. ttyd actually listening on the port.
    listening = _run(["ss", "-ltnH", f"sport = :{port}"],
                     check=False, capture_output=True, text=True).stdout.strip()
    if listening:
        _ok(f"ttyd is listening on :{port}")
    else:
        _warn(f"Nothing is listening on :{port}"); ok = False

    print()
    if ok:
        _ok("ALL CHECKS PASSED — Web SSH is correctly installed and running.")
    else:
        _warn("SOME CHECKS FAILED — see the warnings above before relying on it.")
    return ok


# ── Helper: best-guess LAN address for the closing hint ────────────────────────
def _guess_host():
    try:
        out = _run(["hostname", "-I"], check=False, capture_output=True, text=True)
        ip = out.stdout.split()[0] if out.stdout.split() else None
        if ip:
            return ip
    except Exception:
        pass
    return "<pi-ip>"


# ── Dry run: show exactly what would be written, change nothing ────────────────
def _dry_run(args):
    ttyd_bin = _run(["which", "ttyd"], check=False,
                    capture_output=True, text=True).stdout.strip() or INSTALL_BIN
    basic_auth = args.basic_auth if (args.basic_auth and ":" in args.basic_auth) else None
    _, unit = build_unit(ttyd_bin, args.port, args.bind, basic_auth)

    print()
    print("  OASIS — Web SSH installer  (DRY RUN — no changes made)")
    _hr()
    _info(f"ttyd binary : {ttyd_bin}  ('which ttyd' or default)")
    _info(f"Service port: {args.port}")

    print(f"\n  Would write {LOGIN_SCRIPT}  (chmod 755):")
    _hr()
    print(build_login_script())
    print(f"  Would write {SERVICE_FILE}  (chmod 640):")
    _hr()
    print(unit)
    print("  Would then run:")
    _info("sudo systemctl daemon-reload")
    _info(f"sudo systemctl enable --now {SERVICE}")
    _info("…and verify the live unit, helper, sshd, and port (Step 5).")
    print()
    _info("Re-run without --dry-run to apply, or --verify to check the live install.")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Install a browser SSH/terminal (ttyd) on Raspberry Pi OS / Debian.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/install-webssh.py                       # bundled binary if present, else download\n"
            "  python3 scripts/install-webssh.py --dry-run             # preview helper + unit, change nothing\n"
            "  python3 scripts/install-webssh.py --verify              # check an existing install, change nothing\n"
            "  python3 scripts/install-webssh.py --port 8090           # use a different port\n"
            "  python3 scripts/install-webssh.py --bind 127.0.0.1      # localhost only\n"
            "  python3 scripts/install-webssh.py --basic-auth          # prompt for user:pass gate\n"
            "  python3 scripts/install-webssh.py --basic-auth admin:s3cret\n"
        ),
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, metavar="N",
        help=f"Port to serve the web terminal on (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--bind", default=None, metavar="ADDR",
        help="Interface to bind (e.g. 127.0.0.1 for localhost-only). "
             "Default: all interfaces, reachable on your LAN.",
    )
    parser.add_argument(
        "--basic-auth", nargs="?", const="", default=None, metavar="USER:PASS",
        help="Add an HTTP Basic Auth gate in front of the login prompt. "
             "Pass USER:PASS, or use the flag alone to be prompted.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show exactly what would be written (login helper + systemd unit) "
             "and the commands that would run. Makes no changes.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Only verify an existing install (helper, live unit, services, "
             "port) against what this script would generate. Makes no changes.",
    )
    args = parser.parse_args()

    if args.dry_run:
        _dry_run(args)
        return
    if args.verify:
        ttyd_bin = _run(["which", "ttyd"], check=False,
                        capture_output=True, text=True).stdout.strip() or INSTALL_BIN
        ba = args.basic_auth if (args.basic_auth and ":" in args.basic_auth) else None
        verify_service(ttyd_bin, args.port, args.bind, ba)
        return

    print()
    print("  OASIS — Web SSH / Terminal Installer")
    _hr()
    _info("Browser-based shell via ttyd.")
    _info(f"Service port: {args.port}")

    check_platform()
    ttyd_bin   = install_ttyd()
    basic_auth = resolve_basic_auth(args.basic_auth)
    if ttyd_bin:
        create_service(ttyd_bin, args.port, args.bind, basic_auth)
    else:
        _step(4, "Creating systemd service")
        _warn("Skipped — ttyd is not installed. Install ttyd, then re-run.")

    host = _guess_host()
    print()
    _hr()
    if not ttyd_bin:
        print("  Web terminal NOT installed — see the logged errors above.")
        suffix = _ttyd_suffix() or "<arch>"
        _info("Re-run with internet access, or drop the static binary")
        _info(f"offline-packages/webssh/ttyd.{suffix} in place, then run again.")
        _hr()
        print()
        return
    print("  Web terminal installed and running.")
    _info(f"Open  http://{host}:{args.port}  from any browser on your network.")
    _info("Log in with your normal Raspberry Pi username and password.")
    if basic_auth:
        _info("You'll also be asked for the HTTP Basic Auth credential first.")
    _info("")
    _warn("Served over plain HTTP — keep this on your trusted LAN.")
    _info("Do not port-forward it to the internet without a TLS reverse proxy,")
    _info("and do not expose it over an encrypted-prohibited RF link.")
    _info("")
    _info("Service management:")
    _info(f"  sudo systemctl status {SERVICE}")
    _info(f"  sudo systemctl restart {SERVICE}")
    _info(f"  journalctl -u {SERVICE} -f")
    _hr()
    print()


if __name__ == "__main__":
    main()
