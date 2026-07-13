#!/usr/bin/env python3
"""
services/winlink/common/winlink.py
---------------------------------
Service-owned implementation for the Winlink/Pat installer and modem setup.
"""

import getpass
import glob
import json
import os
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run, has_internet,
    pat_find_local, pat_latest_release, pat_download_deb,
    deb_field, dpkg_installed_version, version_decision,
)

try:
    from common import manifest as M
    _MANIFEST_FEATURE = M.get_feature("winlink")
    _HAS_MANIFEST_ENTRY = True
except (KeyError, Exception):
    _MANIFEST_FEATURE = None
    _HAS_MANIFEST_ENTRY = False


def _offline_dir(repo_root):
    if _HAS_MANIFEST_ENTRY:
        return M.bundle_dir(os.path.join(repo_root, "offline-packages"), "winlink")
    return os.path.join(repo_root, "offline-packages", "pat")


SERVICE = "pat"
DEFAULT_PORT = 8082
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}.service"
MODEM_SERVICE = "pat-direwolf"
MODEM_SERVICE_FILE = f"/etc/systemd/system/{MODEM_SERVICE}.service"
MODEM_AGW_PORT = 8000
MODEM_KISS_PORT = 8001
MODEM_ADEVICE = "plughw:audioinjectorpi,0"
MODEM_PTT_BCM = 12
MODEM_DRA_CONF_NAME = "oasis-winlink.conf"
MODEM_DIGIRIG_CONF_NAME = "oasis-winlink-digirig.conf"
MODEM_DIGIRIG_ADEVICE = "plughw:CARD=Device,DEV=0"
MODEM_DIGIRIG_SERIAL_GLOB = "/dev/serial/by-id/usb-Silicon_Labs_CP210*-if00-port0"
MODEM_DIGIRIG_SERIAL_GLOB_ANY = "/dev/serial/by-id/*CP210*-if00*"
MODEM_DIGIRIG_TTY_GLOB = "/dev/ttyUSB*"
TELNET_ALIAS = "telnet://{mycall}:CMSTelnet@cms.winlink.org:8772/wl2k"

ARCH_MAP = {
    "aarch64": "arm64",
    "arm64":   "arm64",
    "armv7l":  "armhf",
    "armhf":   "armhf",
    "armv6l":  "armhf",
    "x86_64":  "amd64",
    "amd64":   "amd64",
    "i686":    "i386",
    "i386":    "i386",
}


def station_callsign(repo_root):
    try:
        with open(os.path.join(repo_root, "station.json")) as fh:
            call = json.load(fh).get("callsign")
    except (OSError, ValueError):
        return None
    call = str(call or "").strip().upper()
    return call or None


def target_user_home():
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        home = os.path.expanduser("~")
    return user, home


def _chown_to_user(path, user):
    if os.geteuid() != 0:
        return
    try:
        info = pwd.getpwnam(user)
        os.chown(path, info.pw_uid, info.pw_gid)
    except (KeyError, OSError) as exc:
        _warn(f"Could not chown {path} to {user}: {exc}")


def check_platform():
    _step(1, "Checking platform")
    if sys.platform != "linux":
        _fail("Pat's Linux packages require a Linux system.\n"
              "     For macOS: download the .pkg from https://github.com/la5nta/pat/releases")
    if _run(["which", "apt"], check=False, capture_output=True).returncode != 0:
        _fail("apt not found. This script supports Debian/Ubuntu/Raspberry Pi OS.")

    machine = platform.machine()
    deb_arch = ARCH_MAP.get(machine)
    _info(f"Architecture: {machine}")
    if not deb_arch:
        _fail(f"No Pat .deb available for architecture \"{machine}\".")
    _ok(f"Architecture -> .deb arch: {deb_arch}")
    return deb_arch


def resolve_release(pinned_version, deb_arch):
    _step(2, "Resolving Pat release")
    release = pat_latest_release(pinned_version)
    tag = release["tag_name"]
    version = tag.lstrip("v")
    _ok(f"Release: {tag}")

    want = f"pat_{version}_linux_{deb_arch}.deb"
    asset = next((a for a in release.get("assets", []) if a["name"] == want), None)
    if not asset:
        available = [a["name"] for a in release["assets"] if a["name"].endswith(".deb")]
        _fail(f"Expected asset \"{want}\" not found.\n"
              f"     Available .deb files: {available}")

    _ok(f"Asset: {asset['name']}  ({asset['size'] / 1_048_576:.1f} MB)")
    return asset["browser_download_url"], asset["name"]


def install_deb(deb_path):
    pkg = deb_field(deb_path, "Package") or SERVICE
    ourv = deb_field(deb_path, "Version")
    inst = dpkg_installed_version(pkg)
    if version_decision(pkg, ourv, inst) == "skip":
        return

    _info(f"Running: sudo apt install -y {deb_path}")
    _info("You may be prompted for your sudo password.")
    print()
    if _run(["sudo", "apt", "install", "-y", deb_path], check=False).returncode != 0:
        _fail("apt install failed. Check the output above for details.")
    _ok("Pat installed")


def install_pat(deb_arch, pinned_version, offline_dir):
    _step(3, "Installing Pat")
    local = pat_find_local(offline_dir, deb_arch)

    if not has_internet():
        if local:
            _warn("No internet — installing the offline bundle "
                  "(can't check GitHub for a newer release).")
            install_deb(local)
            return
        _fail("No internet and no offline package in offline-packages/pat/.\n"
              "     Connect to the internet, or build a bundle first:\n"
              "       python3 scripts/create-oasis-offline.py")

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


def write_config(port, callsign, locator, password):
    _step(4, "Writing Pat configuration")
    user, home = target_user_home()
    cfg_dir = os.path.join(home, ".config", "pat")
    cfg_path = os.path.join(cfg_dir, "config.json")
    addr = f"0.0.0.0:{port}"

    os.makedirs(cfg_dir, exist_ok=True)
    _chown_to_user(cfg_dir, user)

    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as exc:
            _warn(f"Existing config.json is not valid JSON ({exc}) — leaving it untouched.")
            return cfg_path
        changed = False
        if cfg.get("http_addr") != addr:
            cfg["http_addr"] = addr
            changed = True
        if _ensure_agwpe_transport(cfg):
            changed = True
        if changed:
            _save_config(cfg_path, cfg, user)
            _ok(f"Existing config updated (http_addr -> {addr}; AGWPE transport ensured)")
        else:
            _ok("Existing config.json already on the right port — left as-is.")
        return cfg_path

    cfg = {
        "mycall": callsign,
        "secure_login_password": password or "",
        "locator": locator or "",
        "http_addr": addr,
        "ax25": {"engine": "agwpe"},
        "agwpe": {"addr": f"127.0.0.1:{MODEM_AGW_PORT}", "radio_port": 0},
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
    os.chmod(path, 0o600)
    _chown_to_user(path, user)


def _ensure_agwpe_transport(cfg):
    changed = False
    ax25 = cfg.setdefault("ax25", {})
    if isinstance(ax25, dict) and not ax25.get("engine"):
        ax25["engine"] = "agwpe"
        changed = True
    agwpe = cfg.setdefault("agwpe", {})
    if isinstance(agwpe, dict):
        if not agwpe.get("addr"):
            agwpe["addr"] = f"127.0.0.1:{MODEM_AGW_PORT}"
            changed = True
        if "radio_port" not in agwpe:
            agwpe["radio_port"] = 0
            changed = True
    return changed


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


def _debian_suite():
    try:
        with open("/etc/os-release") as fh:
            for line in fh:
                if line.startswith("VERSION_CODENAME="):
                    return line.strip().split("=", 1)[1].strip().strip('"') or None
    except OSError:
        pass
    return None


def _direwolf_bundle_debs(repo_root):
    if not repo_root:
        return []
    suite = _debian_suite()
    pkg_root = os.path.join(repo_root, "offline-packages")
    cand = ([M.bundle_dir(pkg_root, "direwolf", suite)] if suite else []) + [M.bundle_dir(pkg_root, "direwolf")]
    for d in cand:
        if os.path.isdir(d):
            debs = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".deb") and not f.startswith("._")]
            if debs:
                return debs
    return []


def install_direwolf(repo_root=None):
    _step(6, "Installing Direwolf (Winlink RF modem)")
    inst = dpkg_installed_version("direwolf")
    if inst:
        _ok(f"Direwolf already installed ({inst})")
        return True

    debs = _direwolf_bundle_debs(repo_root)
    if debs:
        _info(f"Installing {len(debs)} bundled direwolf package(s) ...")
        if (_run(["sudo", "apt", "install", "--no-install-recommends", "-y", *debs],
                 check=False).returncode == 0 and dpkg_installed_version("direwolf")):
            _ok("Direwolf installed (offline bundle)")
            return True
        _warn("Offline bundle install failed (missing deps?) — trying apt.")

    if _run(["which", "apt"], check=False, capture_output=True).returncode != 0:
        _warn("apt not found — install Direwolf manually for RF Winlink.")
        return False
    if not has_internet():
        _warn("No internet and no usable Direwolf bundle. Telnet Winlink still works; bundle it (scripts/create-oasis-offline.py) or connect to install.")
        return False
    _info("Running: sudo apt install -y direwolf")
    _info("You may be prompted for your sudo password.")
    print()
    if _run(["sudo", "apt", "install", "-y", "direwolf"], check=False).returncode != 0:
        _warn("Direwolf install failed. Telnet Winlink still works; the RF modem is unavailable until it's installed.")
        return False
    _ok("Direwolf installed")
    return True


def compute_ptt_gpio(override=None):
    if override is not None:
        return override
    best = None
    for chip in sorted(glob.glob("/sys/class/gpio/gpiochip*")):
        try:
            base = int(open(os.path.join(chip, "base")).read().strip())
            ngpio = int(open(os.path.join(chip, "ngpio")).read().strip())
            label = open(os.path.join(chip, "label")).read().strip().lower()
        except (OSError, ValueError):
            continue
        if ("bcm" in label or "rp1" in label or "pinctrl" in label) and 40 <= ngpio <= 80:
            if best is None or ngpio > best[1]:
                best = (base, ngpio, label)
    if best is None:
        return None
    return best[0] + MODEM_PTT_BCM


def _sync_ptt_line(cfg_path, ptt_gpio, user):
    try:
        with open(cfg_path) as fh:
            lines = fh.readlines()
    except OSError:
        return False
    want, changed = f"PTT GPIO {ptt_gpio}", False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("PTT GPIO") and s.split("#")[0].strip() != want:
            comment = ln[ln.find("#"):].rstrip("\n") if "#" in ln else ""
            lines[i] = f"{want}          {comment}\n" if comment else f"{want}\n"
            changed = True
    if changed:
        with open(cfg_path, "w") as fh:
            fh.writelines(lines)
        _chown_to_user(cfg_path, user)
    return changed


def write_direwolf_config(callsign, adevice, ptt_gpio):
    _step(7, "Writing Direwolf (RF modem) configuration")
    user, home = target_user_home()
    cfg_dir = os.path.join(home, ".config", "direwolf")
    cfg_path = os.path.join(cfg_dir, MODEM_DRA_CONF_NAME)
    os.makedirs(cfg_dir, exist_ok=True)
    _chown_to_user(cfg_dir, user)

    if os.path.exists(cfg_path):
        if ptt_gpio is not None and _sync_ptt_line(cfg_path, ptt_gpio, user):
            _ok(f"Existing conf kept; PTT GPIO synced -> {ptt_gpio}")
        else:
            _ok("Existing Direwolf conf left as-is.")
        return cfg_path

    ptt_line = (f"PTT GPIO {ptt_gpio}" if ptt_gpio is not None
                else f"# PTT GPIO <auto-detect failed — re-run with --ptt-gpio N (gpiochip base + {MODEM_PTT_BCM})>")
    conf = f"""# OASIS — Winlink RF modem (Direwolf). Generated by install-winlink.py.
# Pat reaches this over AGWPE at 127.0.0.1:{MODEM_AGW_PORT}. Hardware-exclusive
# with GrayWolf (same sound card + GPIO {MODEM_PTT_BCM}); the dashboard's
# '{MODEM_SERVICE}' card starts/stops it. Do NOT enable at boot.

ADEVICE   {adevice}
ARATE     48000
ACHANNELS 1
CHANNEL 0
MYCALL {callsign}
MODEM 1200
{ptt_line}          # sysfs = SoC gpiochip base + {MODEM_PTT_BCM}; re-run installer if the kernel base shifts
AGWPORT {MODEM_AGW_PORT}
KISSPORT {MODEM_KISS_PORT}

MAXV22 0
"""
    with open(cfg_path, "w") as fh:
        fh.write(conf)
    _chown_to_user(cfg_path, user)
    _ok(f"Wrote {cfg_path}")
    if ptt_gpio is None:
        _warn("Could not auto-detect the PTT GPIO number — set it with "
              f"--ptt-gpio N (SoC gpiochip base + {MODEM_PTT_BCM}) and re-run.")
    return cfg_path


def detect_digirig_serial(override=None):
    if override:
        return override
    for pattern in (MODEM_DIGIRIG_SERIAL_GLOB, MODEM_DIGIRIG_SERIAL_GLOB_ANY, MODEM_DIGIRIG_TTY_GLOB):
        cands = sorted(glob.glob(pattern))
        if cands:
            return cands[0]
    return None


def detect_usb_adevice(override=None):
    if override:
        return override
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    for line in out.splitlines():
        if line.startswith("card") and "USB" in line:
            m = re.search(r"card\s+\d+:\s+(\S+)\s+\[", line)
            if m:
                return f"plughw:CARD={m.group(1)},DEV=0"
    return MODEM_DIGIRIG_ADEVICE


def write_digirig_config(callsign, adevice, ptt_serial, force=False):
    _step(7, "Writing DigiRig Direwolf (RF modem) configuration")
    user, home = target_user_home()
    cfg_dir = os.path.join(home, ".config", "direwolf")
    cfg_path = os.path.join(cfg_dir, MODEM_DIGIRIG_CONF_NAME)
    os.makedirs(cfg_dir, exist_ok=True)
    _chown_to_user(cfg_dir, user)

    if os.path.exists(cfg_path) and not force:
        _ok("Existing DigiRig conf left as-is.")
        return cfg_path

    ptt_line = (f"PTT {ptt_serial} RTS" if ptt_serial
                else "# PTT <serial by-id> RTS  — no CP210x/ttyUSB found; plug in the DigiRig and re-run, or set --modem-ptt-serial /dev/serial/by-id/usb-Silicon_Labs_CP210x…-if00-port0")
    conf = f"""# OASIS — Winlink RF modem (Direwolf) on a DigiRig Mobile.
# Generated by install-winlink.py. Pat reaches this over AGWPE at 127.0.0.1:{MODEM_AGW_PORT}.
# DigiRig = USB sound card + a CP210x serial bridge whose RTS line keys PTT —
# no GPIO, no boot overlay. Hardware-exclusive with GrayWolf; the dashboard's
# '{MODEM_SERVICE}' card starts/stops it. Do NOT enable at boot.

ADEVICE   {adevice}
ARATE     48000
ACHANNELS 1
CHANNEL 0
{ptt_line}
MYCALL {callsign}
MODEM 1200
AGWPORT {MODEM_AGW_PORT}
KISSPORT {MODEM_KISS_PORT}

MAXV22 0
"""
    with open(cfg_path, "w") as fh:
        fh.write(conf)
    _chown_to_user(cfg_path, user)
    _ok(f"Wrote {cfg_path}")
    if not ptt_serial:
        _warn("No DigiRig CP210x/ttyUSB serial detected — the conf has NO working PTT line, so Direwolf won't key the radio. Plug in the DigiRig and re-run, or set --modem-ptt-serial /dev/serial/by-id/usb-Silicon_Labs_CP210x…-if00-port0.")
    return cfg_path


def create_modem_service(conf_path, ptt_gpio, interface="dra"):
    _step(8, f"Creating {MODEM_SERVICE} systemd service")
    user, home = target_user_home()
    direwolf_bin = shutil.which("direwolf") or "/usr/bin/direwolf"

    unexport = (
        f"ExecStopPost=/bin/sh -c 'echo {ptt_gpio} > /sys/class/gpio/unexport 2>/dev/null || true'\n"
        if ptt_gpio is not None else "")

    hw_note = (f"DRA sound card + GPIO {MODEM_PTT_BCM}" if interface == "dra"
               else "DigiRig USB sound card + CP210x serial RTS")

    unit = f"""[Unit]
Description=Winlink RF modem (Direwolf) — OASIS
After=network.target sound.target

[Service]
Type=simple
User={user}
Environment=HOME={home}
ExecStart={direwolf_bin} -t 0 -c {conf_path}
{unexport}Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    proc = subprocess.Popen(["sudo", "tee", MODEM_SERVICE_FILE],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    proc.communicate(unit.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {MODEM_SERVICE_FILE}")
    _run(["sudo", "chmod", "644", MODEM_SERVICE_FILE], check=False)
    _run(["sudo", "systemctl", "daemon-reload"], check=False)
    _run(["sudo", "systemctl", "disable", MODEM_SERVICE], check=False)
    _ok(f"Service file: {MODEM_SERVICE_FILE}  (runs as {user}; disabled at boot, start on demand)")
    _info(f"Start RF Winlink:  sudo systemctl start {MODEM_SERVICE}   (stops GrayWolf; frees the {hw_note})")


def _guess_host():
    out = _run(["hostname", "-I"], check=False, capture_output=True, text=True).stdout
    parts = out.split()
    return parts[0] if parts else "<pi-ip>"


def run(pinned_version=None, callsign="W4MHI", locator=None, password=None,
        no_password=False, port=DEFAULT_PORT, no_service=False, repo_root=None,
        no_modem=False, ptt_gpio=None, modem_adevice=None,
        modem_callsign=None, modem_interface="dra", modem_only=False,
        modem_ptt_serial=None):
    if repo_root is None:
        repo_root = _REPO_ROOT

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

    if modem_only:
        _info("--modem-only: skipping Pat install/config; configuring the RF modem only.")
    else:
        install_pat(deb_arch, pinned_version, offline_dir)
        write_config(port, callsign, locator, password)
        if no_service:
            _step(5, "Creating systemd service")
            _info("--no-service: skipped. Start the UI manually with:  pat http")
        else:
            create_service(port)

    if no_modem:
        _step(6, "Installing Direwolf (Winlink RF modem)")
        _info("--no-modem: skipped. Telnet (internet) Winlink only.")
    elif install_direwolf(repo_root):
        mycall = modem_callsign or callsign
        dra_adevice = (modem_adevice if (modem_adevice and modem_interface == "dra") else MODEM_ADEVICE)
        gpio = compute_ptt_gpio(ptt_gpio)
        dra_conf = write_direwolf_config(callsign=mycall, adevice=dra_adevice, ptt_gpio=gpio)
        dg_adevice_override = (modem_adevice if (modem_adevice and modem_interface == "digirig") else None)
        dg_adevice = detect_usb_adevice(dg_adevice_override)
        dg_serial = detect_digirig_serial(modem_ptt_serial)
        digirig_conf = write_digirig_config(callsign=mycall, adevice=dg_adevice, ptt_serial=dg_serial,
                                            force=(modem_interface == "digirig"))

        if modem_interface == "digirig":
            active_conf, active_gpio = digirig_conf, None
        else:
            active_conf, active_gpio = dra_conf, gpio

        if no_service:
            _step(8, f"Creating {MODEM_SERVICE} systemd service")
            _info(f"--no-service: skipped. Start manually:  direwolf -t 0 -c {active_conf}")
        else:
            create_modem_service(active_conf, active_gpio, interface=modem_interface)

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
    if not no_modem:
        _info(f"RF Winlink (packet): start the '{MODEM_SERVICE}' card on the dashboard "
              "(it stops GrayWolf), then Action -> Connect -> ax25:///<gateway-call>.")
        _info("Full runbook + APRS<->Winlink mode-switch: docs/plan-winlink.md")
    print()
