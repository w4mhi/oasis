#!/usr/bin/env python3
"""
common/winlink.py
-----------------
Install logic for Pat, the Winlink client (web UI + Telnet/RF transports).

NOTE: The offline-manifest.json has no 'winlink' entry as of this writing.
The OFFLINE_DIR therefore defaults to the legacy path (offline-packages/pat/).
When a manifest entry is added later, bundle_dir() will replace that path.

Public API
----------
  check_platform()                  -> deb_arch str
  resolve_release(pinned, arch)     -> (url, filename)
  install_deb(deb_path)
  write_config(port, callsign, locator, password) -> cfg_path
  create_service(port)
  run(args)                         # entry point for the thin CLI wrapper
"""

import getpass
import json
import os
import platform
import pwd
import shutil
import subprocess
import sys
import tempfile

# ── Shared helpers ───────────────────────────────────────────────────────────
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS_DIR)

from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run, has_internet,
    pat_find_local, pat_latest_release, pat_download_deb,
    deb_field, dpkg_installed_version, version_decision,
)

# Attempt to read from the manifest; the 'winlink' feature is not yet defined,
# so we fall back to legacy defaults throughout (no manifest calls will fail the
# script — they are caught at import time and the flag is set).
try:
    from common import manifest as M
    _MANIFEST_FEATURE = M.get_feature("winlink")
    _HAS_MANIFEST_ENTRY = True
except (KeyError, Exception):
    _MANIFEST_FEATURE   = None
    _HAS_MANIFEST_ENTRY = False


def _offline_dir(repo_root):
    """Return the bundle dir for Pat/Winlink.

    Uses M.bundle_dir when a manifest entry exists; otherwise falls back to the
    legacy offline-packages/pat/ path used before the manifest was introduced.
    """
    if _HAS_MANIFEST_ENTRY:
        return M.bundle_dir(os.path.join(repo_root, "offline-packages"), "winlink")
    return os.path.join(repo_root, "offline-packages", "pat")


SERVICE      = "pat"
DEFAULT_PORT = 8082          # GrayWolf owns 8080; 8081 kiwix, 8083 flask, 8085 aprs_api
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"

# The well-known Winlink CMS Telnet gateway password alias.
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


def install_pat(deb_arch, pinned_version, offline_dir):
    """Install Pat. Always resolve the target release from GitHub first so a
    re-run picks up a newer version; the bundle is used when it matches the
    target, or as a fallback if GitHub is down.
    """
    _step(3, "Installing Pat")
    local = pat_find_local(offline_dir, deb_arch)

    # Offline fallback: no internet -> install the bundle if we have one.
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


# ── Main entry point ───────────────────────────────────────────────────────────
def run(pinned_version=None, callsign="W4MHI", locator=None, password=None,
        no_password=False, port=DEFAULT_PORT, no_service=False, repo_root=None):
    """Full install sequence. Called by the thin CLI wrapper."""
    if repo_root is None:
        repo_root = os.path.dirname(_SCRIPTS_DIR)

    offline_dir = _offline_dir(repo_root)

    print()
    print("  OASIS -- Winlink (Pat) Installer")
    _hr()
    _info("Project: https://getpat.io")
    _info(f"Service port: {port}")

    if not _HAS_MANIFEST_ENTRY:
        _warn("No 'winlink' entry in offline-manifest.json — using legacy offline path.")
        _info(f"Offline bundle dir: {offline_dir}")

    deb_arch = check_platform()
    install_pat(deb_arch, pinned_version, offline_dir)

    write_config(port, callsign, locator, password)

    if no_service:
        _step(5, "Creating systemd service")
        _info("--no-service: skipped. Start the UI manually with:  pat http")
    else:
        create_service(port)

    host = _guess_host()
    print()
    print("  OASIS -- Winlink (Pat) install complete.")
    _hr()
    _info(f"Open  http://{host}:{port}  from any browser on your network.")
    if not password and not no_password:
        _info("Set your Winlink password before connecting:")
        _info("  pat configure        # opens config.json in your editor")
    _info("Send a test (Telnet/internet):  compose a message, then Action -> Connect -> telnet")
    _warn("Plain HTTP + the Winlink password is stored in config.json (mode 600).")
    _info("Keep this on your trusted LAN; do not port-forward without TLS.")
    _info("RF (packet via GrayWolf KISS) is Phase 2 — see docs/plan-winlink.md.")
    print()
