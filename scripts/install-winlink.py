#!/usr/bin/env python3
"""
install-winlink.py
------------------
Install the Winlink client **Pat** on Linux (Debian/Ubuntu/Raspberry Pi OS) and
expose its browser UI as an OASIS service. Pat is a cross-platform Winlink client
with a built-in web interface — compose, read, and send Winlink (radio email)
from a browser.

What this does (same workflow as the other install-* scripts):
  1. Detects architecture and picks the right Pat .deb
  2. Installs Pat — always checks GitHub for the latest release first (so a
     re-run updates, like install-graywolf), installing the bundled .deb from
     offline-packages/pat/ when it matches; if there's no internet it falls back
     to the bundle (offline), and only downloads when the bundle is stale/absent
  3. Writes a starter config (~/.config/pat/config.json) — callsign, Winlink
     password (optional prompt), and the web UI bound to the LAN on port 8082
  4. Creates + enables a systemd service that runs `pat http`

Phase 1 = Telnet (internet gateway): works as soon as your Winlink password is
set. RF transports (packet via GrayWolf's KISS TNC) are Phase 2 — see
docs/plan-winlink.md.

Pat runs on port 8082 (GrayWolf already owns 8080). After install, open
http://<pi-ip>:8082 to compose/send.

Usage:
  python3 scripts/install-winlink.py
  python3 scripts/install-winlink.py --callsign W4MHI
  python3 scripts/install-winlink.py --version 1.0.0      # pin a version (online)
  python3 scripts/install-winlink.py --no-password        # skip the password prompt
  python3 scripts/install-winlink.py --no-service         # install + config only
  python3 scripts/install-winlink.py --port 8082

Requires: Linux, apt, sudo. Internet used to check for the latest release (a
re-run updates); falls back to a bundled .deb in offline-packages/pat/ when
offline.
Security: Pat serves plain HTTP and config.json holds your Winlink password
          (mode 600). Keep it on your trusted LAN, not the public internet.
Project:  https://getpat.io  ·  https://github.com/la5nta/pat
"""

import argparse
import getpass
import json
import os
import platform
import pwd
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run, has_internet,
    pat_find_local, pat_latest_release, pat_download_deb,
    deb_field, dpkg_installed_version, version_decision,
)

SERVICE      = "pat"
DEFAULT_PORT = 8082          # GrayWolf owns 8080; 8081 kiwix, 8083 flask, 8085 aprs_api
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"
REPO_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFLINE_DIR  = os.path.join(REPO_ROOT, "offline-packages", "pat")

# The well-known Winlink CMS Telnet gateway password. Actual auth is the user's
# Winlink password (secure_login_password), negotiated separately by Pat.
TELNET_ALIAS = "telnet://{mycall}:CMSTelnet@cms.winlink.org:8772/wl2k"

# Pat .deb arches: amd64, arm64, armhf, i386.
ARCH_MAP = {
    "aarch64": "arm64",
    "arm64":   "arm64",
    "armv7l":  "armhf",
    "armhf":   "armhf",
    "armv6l":  "armhf",   # Pi 1 / Zero — armhf is the closest build
    "x86_64":  "amd64",
    "amd64":   "amd64",
    "i686":    "i386",
    "i386":    "i386",
}


# ── Target user (so config + service belong to the operator, not root) ──────────
def target_user_home():
    """Return (user, home) for the operator — honours sudo's original user."""
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        home = os.path.expanduser("~")
    return user, home


def _chown_to_user(path, user):
    """If we're root (ran under sudo), hand the file back to the operator."""
    if os.geteuid() != 0:
        return
    try:
        info = pwd.getpwnam(user)
        os.chown(path, info.pw_uid, info.pw_gid)
    except (KeyError, OSError) as exc:
        _warn(f"Could not chown {path} to {user}: {exc}")


# ── Step 1: Platform check ─────────────────────────────────────────────────────
def check_platform():
    _step(1, "Checking platform")
    if sys.platform != "linux":
        _fail("Pat's Linux packages require a Linux system.\n"
              "     For macOS: download the .pkg from https://github.com/la5nta/pat/releases")
    if _run(["which", "apt"], check=False, capture_output=True).returncode != 0:
        _fail("apt not found. This script supports Debian/Ubuntu/Raspberry Pi OS.")

    machine  = platform.machine()
    deb_arch = ARCH_MAP.get(machine)
    _info(f"Architecture: {machine}")
    if not deb_arch:
        _fail(f"No Pat .deb available for architecture \"{machine}\".")
    _ok(f"Architecture -> .deb arch: {deb_arch}")
    return deb_arch


# ── Step 2: Resolve release (online path only) ─────────────────────────────────
def resolve_release(pinned_version, deb_arch):
    _step(2, "Resolving Pat release")
    release = pat_latest_release(pinned_version)
    tag     = release["tag_name"]
    version = tag.lstrip("v")
    _ok(f"Release: {tag}")

    want  = f"pat_{version}_linux_{deb_arch}.deb"
    asset = next((a for a in release.get("assets", []) if a["name"] == want), None)
    if not asset:
        available = [a["name"] for a in release["assets"] if a["name"].endswith(".deb")]
        _fail(f"Expected asset \"{want}\" not found.\n"
              f"     Available .deb files: {available}")

    _ok(f"Asset: {asset['name']}  ({asset['size'] / 1_048_576:.1f} MB)")
    return asset["browser_download_url"], asset["name"]


# ── Step 3: Install the .deb ───────────────────────────────────────────────────
def install_deb(deb_path):
    pkg  = deb_field(deb_path, "Package") or SERVICE
    ourv = deb_field(deb_path, "Version")
    inst = dpkg_installed_version(pkg)
    if version_decision(pkg, ourv, inst) == "skip":
        return  # keep the newer/equal version already installed

    _info(f"Running: sudo apt install -y {deb_path}")
    _info("You may be prompted for your sudo password.")
    print()
    if _run(["sudo", "apt", "install", "-y", deb_path], check=False).returncode != 0:
        _fail("apt install failed. Check the output above for details.")
    _ok("Pat installed")


def install_pat(deb_arch, pinned_version):
    """Install Pat. Like install-graywolf, always resolve the target release from
    GitHub first so a re-run picks up a newer version (implicit update) and an
    outdated bundle can't silently pin an old one. The bundle is the offline
    cache — used when it matches the target, or as a fallback if GitHub is down.
    """
    _step(3, "Installing Pat")
    local = pat_find_local(OFFLINE_DIR, deb_arch)

    # Offline fallback (improvement over graywolf, which hard-fails offline):
    # no internet → install the bundle if we have one, else fail with guidance.
    if not has_internet():
        if local:
            _warn("No internet — installing the offline bundle "
                  "(can't check GitHub for a newer release).")
            install_deb(local)
            return
        _fail("No internet and no offline package in offline-packages/pat/.\n"
              "     Connect to the internet, or build a bundle first:\n"
              "       python3 scripts/create-oasis-offline.py")

    # Online: resolve latest (or pinned), reuse the bundle only if it matches.
    with tempfile.TemporaryDirectory() as tmp:
        url, filename = resolve_release(pinned_version, deb_arch)
        if local and os.path.basename(local) == filename:
            _info(f"Offline package is current ({filename}) — installing it.")
            install_deb(local)
        else:
            if local:
                _info(f"Newer target {filename} — bundled {os.path.basename(local)} "
                      "is outdated; downloading ...")
            else:
                _info("No offline package found -- downloading from GitHub ...")
                _warn("Run 'python3 scripts/create-oasis-offline.py' to bundle it for offline installs.")
            install_deb(pat_download_deb(url, filename, tmp))


# ── Step 4: Starter config ─────────────────────────────────────────────────────
def write_config(port, callsign, locator, password):
    _step(4, "Writing Pat configuration")
    user, home = target_user_home()
    cfg_dir  = os.path.join(home, ".config", "pat")
    cfg_path = os.path.join(cfg_dir, "config.json")
    addr     = f"0.0.0.0:{port}"           # bind LAN so it's reachable; plain HTTP

    os.makedirs(cfg_dir, exist_ok=True)
    _chown_to_user(cfg_dir, user)

    if os.path.exists(cfg_path):
        # Respect an existing config — only ensure the web UI is on our LAN port.
        try:
            with open(cfg_path) as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as exc:
            _warn(f"Existing config.json is not valid JSON ({exc}) — leaving it untouched.")
            return cfg_path
        if cfg.get("http_addr") != addr:
            cfg["http_addr"] = addr
            _save_config(cfg_path, cfg, user)
            _ok(f"Existing config kept; set http_addr -> {addr}")
        else:
            _ok("Existing config.json already on the right port — left as-is.")
        return cfg_path

    cfg = {
        "mycall": callsign,
        "secure_login_password": password or "",
        "locator": locator or "",
        "http_addr": addr,
        "connect_aliases": {"telnet": TELNET_ALIAS},
    }
    _save_config(cfg_path, cfg, user)
    _ok(f"Wrote {cfg_path}")
    _info(f"mycall={callsign or '(unset)'}  http_addr={addr}  telnet alias ready")
    if not password:
        _warn("No Winlink password set — add it before connecting (see next steps).")
    return cfg_path


def _save_config(path, cfg, user):
    with open(path, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.chmod(path, 0o600)          # holds the Winlink password
    _chown_to_user(path, user)


# ── Step 5: systemd service ────────────────────────────────────────────────────
def create_service(port):
    _step(5, "Creating systemd service")
    user, home = target_user_home()
    pat_bin = shutil.which("pat") or "/usr/bin/pat"

    # ExecStart relies on http_addr in config.json (set in step 4) for the port,
    # so we don't depend on the http subcommand's flag name.
    unit = f"""[Unit]
Description=Pat Winlink web UI — OASIS
After=network.target

[Service]
Type=simple
User={user}
Environment=HOME={home}
ExecStart={pat_bin} http
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    proc = subprocess.Popen(["sudo", "tee", SERVICE_FILE],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(unit.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {SERVICE_FILE}")
    _run(["sudo", "chmod", "644", SERVICE_FILE], check=False)
    _ok(f"Service file: {SERVICE_FILE}  (runs as {user})")

    _run(["sudo", "systemctl", "daemon-reload"], check=False)
    _run(["sudo", "systemctl", "enable", "--now", SERVICE], check=False)

    status = _run(["systemctl", "is-active", SERVICE],
                  check=False, capture_output=True, text=True).stdout.strip()
    if status == "active":
        _ok(f"{SERVICE} is active")
    else:
        _warn(f"{SERVICE} status: {status}")
        log = _run(["journalctl", "-u", SERVICE, "-n", "12", "--no-pager", "--no-hostname"],
                   check=False, capture_output=True, text=True)
        for line in (log.stdout or log.stderr).strip().splitlines():
            _info(line)
        _info(f"Check logs with:  journalctl -u {SERVICE} -f")


# ── Helper: LAN address for the closing hint ───────────────────────────────────
def _guess_host():
    out = _run(["hostname", "-I"], check=False, capture_output=True, text=True).stdout
    parts = out.split()
    return parts[0] if parts else "<pi-ip>"


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Install the Pat Winlink client + web UI on Raspberry Pi OS / Debian.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/install-winlink.py                      # bundled .deb if present, else download\n"
            "  python3 scripts/install-winlink.py --callsign W4MHI\n"
            "  python3 scripts/install-winlink.py --version 1.0.0      # pin a version (online)\n"
            "  python3 scripts/install-winlink.py --no-service         # install + config only\n"
        ),
    )
    parser.add_argument("--version", metavar="X.Y.Z",
                        help="Install a specific Pat version (always resolved from GitHub).")
    parser.add_argument("--callsign", default="W4MHI", metavar="CALL",
                        help="Winlink callsign for the starter config (default: W4MHI).")
    parser.add_argument("--locator", default=None, metavar="GRID",
                        help="Maidenhead grid square (e.g. FM18) — optional.")
    parser.add_argument("--password", default=None, metavar="PW",
                        help="Winlink password. Omit to be prompted; see --no-password.")
    parser.add_argument("--no-password", action="store_true",
                        help="Don't prompt for / set a Winlink password now.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, metavar="N",
                        help=f"Port for the Pat web UI (default: {DEFAULT_PORT}).")
    parser.add_argument("--no-service", action="store_true",
                        help="Install Pat and write config, but don't create the systemd service.")
    args = parser.parse_args()

    print()
    print("  OASIS -- Winlink (Pat) Installer")
    _hr()
    _info("Project: https://getpat.io")
    _info(f"Service port: {args.port}")

    deb_arch = check_platform()
    install_pat(deb_arch, args.version)

    # Resolve the Winlink password: explicit flag, interactive prompt, or skip.
    if args.no_password:
        password = None
    elif args.password is not None:
        password = args.password
    elif sys.stdin.isatty():
        password = getpass.getpass(
            f"    Winlink password for {args.callsign} (blank to skip): ") or None
    else:
        password = None

    write_config(args.port, args.callsign, args.locator, password)

    if args.no_service:
        _step(5, "Creating systemd service")
        _info("--no-service: skipped. Start the UI manually with:  pat http")
    else:
        create_service(args.port)

    host = _guess_host()
    print()
    print("  OASIS -- Winlink (Pat) install complete.")
    _hr()
    _info(f"Open  http://{host}:{args.port}  from any browser on your network.")
    if not password and not args.no_password:
        _info("Set your Winlink password before connecting:")
        _info("  pat configure        # opens config.json in your editor")
    _info("Send a test (Telnet/internet):  compose a message, then Action -> Connect -> telnet")
    _warn("Plain HTTP + the Winlink password is stored in config.json (mode 600).")
    _info("Keep this on your trusted LAN; do not port-forward without TLS.")
    _info("RF (packet via GrayWolf KISS) is Phase 2 — see docs/plan-winlink.md.")
    print()


if __name__ == "__main__":
    main()
