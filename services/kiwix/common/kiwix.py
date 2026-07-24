#!/usr/bin/env python3
"""
services/kiwix/common/kiwix.py
------------------------------
Service-owned implementation for the Kiwix installer and service setup.
"""

import getpass
import io
import os
import platform
import pwd
import subprocess
import sys
import tarfile

# Keep the repository root importable for shared helpers.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _REPO_ROOT)

from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run,
    kiwix_find_local, kiwix_download_tarball,
    binary_version, version_decision,
)
from common import manifest as M


def _feature():
    return M.get_feature("kiwix")


def _offline_dir(repo_root):
    """Bundle dir for kiwix (non-apt, no suite)."""
    return M.bundle_dir(os.path.join(repo_root, "offline-packages"), "kiwix")


def target_user_home():
    """Return (user, home) for the operator — correct even when run as bare root.

    ZIM files (and the kiwix-start script's baked-in scan directory) must land
    under the operator's home, not /root. But privileged installs run as *real*
    root via the no-tty installer worker (scripts/oasis_installer_worker.py) —
    nothing sets $SUDO_USER for a systemd-launched process, and the worker's
    baked Environment=SUDO_USER can itself be wrong (it resolves to 'root' when
    scripts/enable-oasis-installer.py was run from a root login). A bare
    os.path.expanduser("~") or getpass.getuser() then silently yields /root, and
    kiwix-start looks in a directory the operator never fills.

    Resolve the operator in order of decreasing reliability, and only return
    root if every signal points there (a genuinely root-only box):

      1. $SUDO_USER, when it names a real non-root user (interactive `sudo`, and
         the worker's baked Environment=SUDO_USER when it was set correctly).
      2. The owner of the OASIS checkout (_REPO_ROOT) — deterministic and fully
         offline: the operator cloned/unpacked OASIS into their own home, so the
         tree is theirs regardless of which account runs the installer. This is
         what fixes a mis-baked SUDO_USER=root without any manual commands.
      3. getpass.getuser(), when it is a real non-root user.
      4. The sole human login account (uid >= 1000 with a /home directory).
    """
    def _lookup(name):
        if not name:
            return None
        try:
            pw = pwd.getpwnam(name)
        except KeyError:
            return None
        return pw.pw_name, pw.pw_dir

    # 1. sudo's original (non-root) user.
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        got = _lookup(sudo_user)
        if got:
            return got

    # 2. Owner of the OASIS checkout — the operator, whoever runs us.
    try:
        owner_uid = os.stat(_REPO_ROOT).st_uid
        if owner_uid != 0:
            pw = pwd.getpwuid(owner_uid)
            return pw.pw_name, pw.pw_dir
    except (OSError, KeyError):
        pass

    # 3. Current user, if not root.
    current = getpass.getuser()
    if current and current != "root":
        got = _lookup(current)
        if got:
            return got

    # 4. The single real login account on the box.
    try:
        humans = [p for p in pwd.getpwall()
                  if p.pw_uid >= 1000 and p.pw_dir.startswith("/home/")
                  and p.pw_shell not in ("", "/usr/sbin/nologin", "/bin/false")]
        if len(humans) == 1:
            return humans[0].pw_name, humans[0].pw_dir
    except Exception:
        pass

    # Genuinely root-only system.
    return (current or "root"), os.path.expanduser("~")


INSTALL_BIN = "/usr/local/bin/kiwix-serve"
KIWIX_START = "/usr/local/bin/kiwix-start"
SERVICE_NAME = "kiwix"
PORT = 8081
DEFAULT_VERSION = "3.8.2"
DEFAULT_ZIM_DIR = os.path.join(target_user_home()[1], "oasis-offline", "zim")
SERVICE_FILE = f"/etc/systemd/system/{SERVICE_NAME}.service"


def removal_record(repo_root=None):
    """Teardown record for the kiwix feature: the kiwix-serve unit and its two
    /usr/local/bin helpers. ZIM files (potentially many GB, hard to re-download
    offline) are advisory-only — never auto-deleted. Managing/upgrading ZIMs is a
    separate concern (see the wikipedia carve-out in the design)."""
    return {"services": [SERVICE_NAME],
            "files": [INSTALL_BIN, KIWIX_START],
            "data_paths": [DEFAULT_ZIM_DIR]}

ARCH_MAP = {
    "aarch64": "aarch64",
    "arm64":   "aarch64",
    "armv7l":  "armhf",
    "armhf":   "armhf",
    "armv6l":  "armv6",
    "i686":    "i586",
    "i386":    "i586",
    "x86_64":  "x86_64",
    "amd64":   "x86_64",
}


def check_platform():
    _step(1, "Checking platform")
    if sys.platform != "linux":
        _fail("This script installs the Linux build of kiwix-serve.\n"
              "     For macOS: brew install kiwix (via Homebrew)")

    machine = platform.machine()
    kiwix_arch = ARCH_MAP.get(machine)
    _info(f"Architecture: {machine}")
    if not kiwix_arch:
        _fail(f"No kiwix-tools build for architecture \"{machine}\".")
    _ok(f"Architecture -> kiwix suffix: linux-{kiwix_arch}")
    return kiwix_arch


def get_tarball(version, kiwix_arch, offline_dir):
    """Return tarball bytes — from bundle if current, else download."""
    _step(2, "Locating kiwix-tools tarball")

    feat = _feature()
    pattern = feat.get("asset_pattern", "kiwix-tools_linux-{arch}-{version}.tar.gz")

    expected_filename = pattern.format(arch=kiwix_arch, version=version)
    local = kiwix_find_local(offline_dir, kiwix_arch)

    if local and os.path.basename(local) == expected_filename:
        _info(f"Using offline package: {expected_filename} (up to date)")
        with open(local, "rb") as fh:
            return fh.read()

    if local:
        _info(f"Offline package {os.path.basename(local)} is outdated — downloading {expected_filename} ...")
    else:
        _info("No offline package found -- downloading from kiwix.org ...")
        _warn("Run 'python3 scripts/create-oasis-offline.py' to build a bundle with all packages.")

    tarball_path = kiwix_download_tarball(offline_dir, version, kiwix_arch)
    with open(tarball_path, "rb") as fh:
        return fh.read()


def install_kiwix_serve(data, version):
    _step(3, "Installing kiwix-serve to /usr/local/bin/")

    inst = binary_version(["kiwix-serve", "--version"])
    if version_decision("kiwix-serve", version, inst) == "skip":
        return

    _info("Extracting kiwix-serve from archive ...")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            member = next(
                (m for m in tf.getmembers() if m.name.endswith("kiwix-serve")), None
            )
            if not member:
                _fail("kiwix-serve not found in archive.")
            member.name = "kiwix-serve"
            tf.extract(member, path="/tmp")
    except Exception as exc:
        _fail(f"Extraction failed: {exc}")

    _run(["sudo", "install", "-m", "755", "/tmp/kiwix-serve", INSTALL_BIN])
    _ok(f"kiwix-serve installed -> {INSTALL_BIN}")

    result = _run([INSTALL_BIN, "--version"], capture_output=True, text=True, check=False)
    ver = result.stdout.strip() or result.stderr.strip()
    if ver:
        _ok(f"Version: {ver.splitlines()[0]}")


def create_service(zim_dir):
    _step(4, "Creating systemd service")
    os.makedirs(zim_dir, exist_ok=True)
    _info(f"ZIM directory: {zim_dir}")

    start_script = (
        "#!/bin/sh\n"
        f"ZIMS=$(find {zim_dir} -maxdepth 1 -name '*.zim' -type f 2>/dev/null | tr '\\n' ' ')\n"
        'if [ -z "$ZIMS" ]; then\n'
        f"    echo 'kiwix: no ZIM files in {zim_dir}/ — run services/kiwix/download-wikipedia.py first'\n"
        "    exit 1\n"
        "fi\n"
        f"exec {INSTALL_BIN} --port {PORT} $ZIMS\n"
    )
    proc = subprocess.Popen(
        ["sudo", "tee", KIWIX_START],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    proc.communicate(start_script.encode())
    _run(["sudo", "chmod", "+x", KIWIX_START])
    _ok(f"Start script: {KIWIX_START}")

    service_content = f"""[Unit]
Description=Kiwix offline reader (OASIS)
After=network.target

[Service]
Type=simple
ExecStart={KIWIX_START}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    proc = subprocess.Popen(
        ["sudo", "tee", SERVICE_FILE],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    proc.communicate(service_content.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {SERVICE_FILE}")

    _ok(f"Service file: {SERVICE_FILE}")
    _run(["sudo", "systemctl", "daemon-reload"])
    _ok("systemctl daemon-reload")

    _run(["sudo", "systemctl", "disable", "--now", SERVICE_NAME], check=False)
    _ok(f"{SERVICE_NAME} set OFF by default (start it from the dashboard when needed).")

    zim_files = [f for f in os.listdir(zim_dir) if f.endswith(".zim")] if os.path.isdir(zim_dir) else []
    if zim_files:
        _info(f"ZIM content found ({len(zim_files)} file(s)).")
    else:
        _info("No ZIM content yet — run services/kiwix/download-wikipedia.py to add some.")
    _info(f"Start Kiwix from the dashboard, or: sudo systemctl start {SERVICE_NAME}")


def run(version=DEFAULT_VERSION, zim_dir=DEFAULT_ZIM_DIR, repo_root=None):
    """Full install sequence. Called by the thin CLI wrapper."""
    if repo_root is None:
        repo_root = _REPO_ROOT

    offline_dir = _offline_dir(repo_root)
    kiwix_arch = check_platform()
    data = get_tarball(version, kiwix_arch, offline_dir)
    install_kiwix_serve(data, version)
    create_service(os.path.expanduser(zim_dir))

    print()
    print("  OASIS -- Kiwix install complete.")
    _hr()
    _info(f"Service: {SERVICE_NAME}  (port {PORT})  — off by default")
    _info("Get content:  python3 services/kiwix/download-wikipedia.py")
    _info("Start it from the OASIS dashboard (the Wikipedia/Kiwix card) when needed.")
    print()
