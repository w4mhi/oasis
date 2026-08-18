#!/usr/bin/env python3
"""
enable-ap-fallback.py
---------------------
Make the Raspberry Pi fall back to a Wi-Fi access point when no known network is
in range — so an operator can always reach the OASIS dashboard, on-grid or off.

Behaviour (single onboard radio → AP *or* client, never both at once):

  Prefer known Wi-Fi, AP only as fallback
    On boot, NetworkManager auto-connects to any saved Wi-Fi in range. A watcher
    (oasis-netwatch.service) waits a few seconds; if no client link comes up it
    raises the `OASIS-AP` hotspot. If a live client link later drops for good, it
    falls back to the AP again. Rejoining a known network happens on reboot or from
    the dashboard Wi-Fi picker (a single radio can't host the AP and scan at once).

What it installs (all reversible with --disable):
  • avahi-daemon                     → the dashboard resolves at http://<hostname>.local:8083
  • NetworkManager `OASIS-AP` profile → WPA2 hotspot, 10.42.0.1/24, DHCP for clients
  • /usr/local/bin/oasis-netctl      → tiny privileged Wi-Fi helper (scan/connect/AP)
  • /usr/local/bin/oasis-netwatch    → the boot-decision + drop-monitor daemon
  • oasis-netwatch.service           → runs the watcher at boot
  • /etc/sudoers.d/oasis-wifi        → lets the OASIS user drive oasis-netctl, no password

Defaults (EmComm-friendly — a public, well-known hotspot):
  SSID       OASIS            (--ssid)
  Password   oasis-emcomm     (--password, WPA2 8–63 chars)
  AP address 10.42.0.1        (NetworkManager `shared` default; DHCP hands out .2–.254)

Usage:
  python3 scripts/enable-ap-fallback.py
  python3 scripts/enable-ap-fallback.py --ssid FIELD1 --password mypassword
  python3 scripts/enable-ap-fallback.py --check
  python3 scripts/enable-ap-fallback.py --disable

Requires: Raspberry Pi OS (Debian/Linux), NetworkManager, systemd, sudo.
"""

import argparse
import getpass
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common.oasis_lib import (
    _hr, _step, _ok, _info, _warn, _fail, _run, dpkg_installed_version,
    sudo_apt_cmd,
)

# ── Shared constants — MUST match server/app.py's Wi-Fi endpoints ──────────────
AP_CON        = "OASIS-AP"                          # NetworkManager connection name
AP_IFACE      = "wlan0"                             # onboard radio
AP_ADDR       = "10.42.0.1/24"                      # NM `shared` default gateway
DEFAULT_SSID  = "OASIS"
DEFAULT_PSK   = "oasis-emcomm"                      # public EmComm hotspot password
NETCTL_PATH   = "/usr/local/bin/oasis-netctl"       # privileged Wi-Fi helper
NETWATCH_PATH = "/usr/local/bin/oasis-netwatch"     # boot-decision + monitor daemon
NETWATCH_UNIT = "oasis-netwatch"
NETWATCH_SVC  = f"/etc/systemd/system/{NETWATCH_UNIT}.service"
SUDOERS_PATH  = "/etc/sudoers.d/oasis-wifi"


def removal_record(repo_root=None):
    """Teardown record for the ap-fallback feature: the netwatch service, the two
    /usr/local/bin helpers, and the Wi-Fi sudoers rule (see common/removal.py).

    The OASIS-AP NetworkManager profile is DELIBERATELY not removed: it is the
    off-grid connectivity lifeline (a public EmComm hotspot, PSK 'oasis-emcomm' —
    not a secret), and nothing recreates it (start-oasis.py has no Wi-Fi logic).
    Deleting it could strand a box that boots with no known Wi-Fi. Left in place
    with an advisory; the operator can drop it by hand if they want it gone."""
    return {"services": [NETWATCH_UNIT],
            "files": [NETCTL_PATH, NETWATCH_PATH, SUDOERS_PATH],
            "notes": [f"NetworkManager '{AP_CON}' hotspot profile kept so the box "
                      f"stays reachable off-grid; `nmcli connection delete {AP_CON}` "
                      "to remove it manually."]}

# ── Privileged Wi-Fi helper (installed to NETCTL_PATH, invoked via `sudo -n`) ──
# The dashboard's /api/wifi/* endpoints run exactly `sudo -n oasis-netctl <cmd>`;
# the sudoers rule below authorises only this binary. `connect` reads the PSK from
# stdin (never argv) so it can't leak through the process list.
NETCTL_SRC = r'''#!/bin/sh
# oasis-netctl — privileged Wi-Fi helper for the OASIS dashboard.
# Installed by scripts/enable-ap-fallback.py. Invoked as: sudo -n oasis-netctl <cmd>
set -eu

AP_CON="__AP_CON__"
IFACE="__IFACE__"

case "${1:-}" in
  scan)
    # Terse, stable columns for the dashboard picker. --rescan yes forces a fresh
    # sweep; falls back to the cached list if the radio is busy (e.g. hosting AP).
    nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list --rescan yes 2>/dev/null \
      || nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list 2>/dev/null
    ;;
  status)
    # One terse line per active connection: NAME:TYPE:DEVICE
    nmcli -t -f NAME,TYPE,DEVICE connection show --active 2>/dev/null
    ;;
  connect)
    ssid="${2:-}"
    [ -n "$ssid" ] || { echo "ERR empty ssid" >&2; exit 2; }
    # PSK arrives on stdin (one line) — kept out of argv/ps. `read` returns
    # non-zero at EOF without a trailing newline but still assigns what it read,
    # so `|| true` keeps the value instead of clobbering it.
    IFS= read -r psk || true
    len=${#psk}
    if [ "$len" -lt 8 ] || [ "$len" -gt 63 ]; then
      echo "ERR WPA2 password must be 8-63 characters" >&2; exit 2
    fi
    # Purge any stale saved profile for this SSID first. A leftover profile with
    # an incomplete security section (e.g. a netplan-wlan-* keyfile) makes NM
    # reactivate it and fail with "key-mgmt: property is missing". Never delete
    # the OASIS AP profile. Matching by the profile's stored SSID (unescaped -g).
    nmcli -g NAME connection show 2>/dev/null | while IFS= read -r cname; do
      [ -z "$cname" ] && continue
      [ "$cname" = "$AP_CON" ] && continue
      ctype=$(nmcli -g connection.type connection show "$cname" 2>/dev/null || true)
      [ "$ctype" = "802-11-wireless" ] || continue
      csid=$(nmcli -g 802-11-wireless.ssid connection show "$cname" 2>/dev/null || true)
      [ "$csid" = "$ssid" ] && nmcli connection delete id "$cname" >/dev/null 2>&1 || true
    done
    # Fresh join. NM auto-detects WPA2/WPA3 and saves the profile so it
    # auto-reconnects on future boots. Joining drops the AP (single radio).
    nmcli device wifi connect "$ssid" password "$psk" ifname "$IFACE"
    ;;
  ap-up)
    nmcli connection up "$AP_CON"
    ;;
  forget)
    # Delete the saved profile for the active client network so NetworkManager
    # won't auto-rejoin; the radio disconnects and oasis-netwatch raises the AP.
    # Never touches the OASIS AP profile itself.
    con=$(nmcli -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null || true)
    if [ -z "$con" ] || [ "$con" = "$AP_CON" ]; then
      echo "not connected to a client network" >&2
      exit 0
    fi
    nmcli connection delete id "$con"
    ;;
  *)
    echo "ERR unknown command" >&2; exit 2
    ;;
esac
'''

# ── Boot-decision + drop-monitor daemon (installed to NETWATCH_PATH) ───────────
# Prefer known Wi-Fi; raise the AP only when there is no client link. Dependency-
# free (talks to NetworkManager through nmcli). Single radio → AP or client, so it
# never tears down a live client link, and once in AP fallback it stays there
# until a reboot or a dashboard-initiated join (a radio can't host + scan at once).
NETWATCH_SRC = r'''#!/usr/bin/env python3
"""oasis-netwatch — prefer known Wi-Fi, fall back to the OASIS AP.

Installed by scripts/enable-ap-fallback.py; run by oasis-netwatch.service.
Logs every decision to stdout so `journalctl -u oasis-netwatch -f` shows what
it's doing."""
import subprocess
import sys
import time

AP_CON    = "__AP_CON__"
IFACE     = "__IFACE__"
BOOT_WAIT = 25   # s: give NetworkManager time to auto-join a known network at boot
GRACE     = 45   # s: a client link must be gone this long before we fall back to AP
POLL      = 15   # s: monitor interval


def log(msg):
    print(f"[netwatch] {msg}", flush=True)


def nm(*args, timeout=20):
    try:
        return subprocess.run(["nmcli", *args], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def active():
    """(client_wifi_up, ap_up) from NetworkManager's active connections."""
    r = nm("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")
    client_up = ap_up = False
    if r and r.returncode == 0:
        for line in r.stdout.splitlines():
            # terse mode escapes ':' inside fields as '\:'; split on unescaped ':'
            parts = line.replace(r"\:", "\x00").split(":")
            parts = [p.replace("\x00", ":") for p in parts]
            if len(parts) < 3:
                continue
            name, ctype, device = parts[0], parts[1], parts[2]
            if name == AP_CON:
                ap_up = True
            elif "wireless" in ctype and device == IFACE:
                client_up = True
    return client_up, ap_up


def client_ssid():
    """SSID of the active client link, for logging. Empty if unknown."""
    r = nm("-g", "GENERAL.CONNECTION", "device", "show", IFACE)
    return (r.stdout.strip() if r and r.returncode == 0 else "") or "?"


def ap_up():
    log(f"raising the {AP_CON} access point…")
    r = nm("connection", "up", AP_CON, timeout=40)
    if r and r.returncode == 0:
        log(f"{AP_CON} is up — dashboard reachable at http://10.42.0.1:8083")
        return True
    err = (r.stderr.strip() if r and r.stderr else "nmcli failed")
    log(f"could not raise {AP_CON}: {err}")
    return False


def state_str(client, ap):
    if ap:
        return "serving AP"
    if client:
        return f"client ({client_ssid()})"
    return "no link"


def main():
    log(f"starting — prefer known Wi-Fi on {IFACE}, else raise {AP_CON} "
        f"(boot wait {BOOT_WAIT}s, drop grace {GRACE}s)")

    # Boot: wait for NetworkManager to auto-join a saved network before deciding.
    deadline = time.monotonic() + BOOT_WAIT
    while time.monotonic() < deadline:
        client, ap = active()
        if client or ap:
            break
        time.sleep(2)

    client, ap = active()
    log(f"boot decision: {state_str(client, ap)}")
    if not client and not ap:
        log("no known network in range at boot")
        ap_up()

    # Monitor: fall back to the AP after a sustained loss of the client link.
    down_since = None
    last = state_str(*active())
    log(f"monitoring (every {POLL}s) — current: {last}")
    while True:
        time.sleep(POLL)
        client, ap = active()
        cur = state_str(client, ap)
        if cur != last:
            log(f"state change: {last} → {cur}")
            last = cur
        if client or ap:
            if down_since is not None:
                log("client link restored — cancelling AP fallback")
            down_since = None          # healthy (client) or already serving the AP
            continue
        now = time.monotonic()
        if down_since is None:
            down_since = now
            log(f"client link lost — {GRACE}s grace before AP fallback")
        elif now - down_since >= GRACE:
            log(f"no client link for {int(now - down_since)}s — falling back to AP")
            if ap_up():
                down_since = None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
'''

NETWATCH_SERVICE = f"""[Unit]
Description=OASIS Wi-Fi AP fallback watcher (prefer known Wi-Fi, else raise the OASIS AP)
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
ExecStart={NETWATCH_PATH}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""


def target_user(explicit):
    """User the OASIS server runs as: explicit > $SUDO_USER > current user."""
    return explicit or os.environ.get("SUDO_USER") or getpass.getuser()


def _nmcli():
    return shutil.which("nmcli")


def _nmcli_priv():
    """nmcli argv for privileged ops (add/modify/delete/up). Prefixed with sudo
    unless we're already root — modifying system connections needs root."""
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return None
    return [nmcli] if os.geteuid() == 0 else ["sudo", nmcli]


def _write_root_file(content, dest, mode):
    """Atomically install `content` at `dest` (root-owned) via a temp file."""
    fd, tmp = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        if _run(["sudo", "install", "-m", mode, "-o", "root", "-g", "root",
                 tmp, dest], check=False).returncode != 0:
            _fail(f"Could not install {dest} (sudo required).")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── avahi (<hostname>.local resolution) ───────────────────────────────────────

def install_avahi():
    # avahi publishes THIS machine's own name — <hostname>.local — so the dashboard
    # is reachable by name without changing the operator's hostname. (We do NOT
    # rename the box to 'oasis': that would rewrite the machine's identity and, e.g.,
    # invalidate Chromium's hostname-keyed kiosk profile lock.)
    host = os.uname().nodename.split(".")[0]
    if dpkg_installed_version("avahi-daemon"):
        _ok("avahi-daemon already installed.")
    else:
        _info("Installing avahi-daemon (apt)…")
        if _run(sudo_apt_cmd("apt-get", "install", "-y", "avahi-daemon"),
                check=False).returncode != 0:
            _warn(f"Could not install avahi-daemon — '{host}.local' may not resolve. "
                  "Operators can still use http://10.42.0.1:8083 on the AP.")
            return
    _run(["sudo", "systemctl", "enable", "--now", "avahi-daemon"], check=False)
    _ok(f"avahi-daemon enabled — dashboard resolves at http://{host}.local:8083 (or the Pi's IP).")


# ── OASIS-AP NetworkManager profile ────────────────────────────────────────────

def _current_regdomain():
    """Live Wi-Fi regulatory country (e.g. 'US'), or None/'00' when unset."""
    r = _run(["iw", "reg", "get"], check=False, capture_output=True, text=True)
    m = re.search(r"country (\w\w):", getattr(r, "stdout", "") or "")
    return m.group(1) if m else None


def resolve_country(country):
    """Decide the Wi-Fi country to apply. Precedence: explicit --country, then
    the already-configured regulatory domain (no prompt needed), else ask the
    operator — the AP can't beacon without it. Returns a 2-letter code or None."""
    if country:
        return country.strip().upper()
    reg = _current_regdomain()
    if reg and reg != "00":
        _info(f"Wi-Fi country already set to {reg} — using it.")
        return None                     # already applied; nothing to change
    _warn("Wi-Fi regulatory country is not set — the OASIS AP can't broadcast "
          "without it.")
    try:
        ans = input("     Enter your 2-letter Wi-Fi country code "
                    "(e.g. US, GB, DE): ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if len(ans) == 2 and ans.isalpha():
        return ans
    _warn("No valid country entered — continuing, but the AP may not broadcast. "
          "Set it later with: python3 scripts/enable-ap-fallback.py --country <CC>")
    return None


def prep_radio(country):
    """AP mode won't beacon on a soft-blocked radio or with an unset regulatory
    domain (country 00) — the usual reason 'the AP is up but no device sees it'.
    Unblock the radio and apply the country both live and persistently."""
    _run(["sudo", "rfkill", "unblock", "wifi"], check=False)
    if country:
        # Persist across reboots (raspi-config) and apply to the live PHY (iw).
        _run(["sudo", "raspi-config", "nonint", "do_wifi_country", country],
             check=False, capture_output=True)
        _run(["sudo", "iw", "reg", "set", country], check=False)
    reg = _current_regdomain()
    if not reg or reg == "00":
        _warn("Wi-Fi regulatory domain is unset (country 00) — the AP may not "
              "broadcast. Re-run with --country <CC>, e.g. --country US.")
    else:
        _ok(f"Radio unblocked · regulatory domain {reg}.")


def create_ap_profile(ssid, psk, channel):
    nm = _nmcli_priv()
    if not nm:
        _fail("nmcli not found — this Pi isn't running NetworkManager (Bookworm+).")
    # Recreate cleanly so the profile always matches the requested SSID/password.
    _run([*nm, "connection", "delete", AP_CON], check=False,
         capture_output=True)
    if _run([*nm, "connection", "add", "type", "wifi", "ifname", AP_IFACE,
             "con-name", AP_CON, "autoconnect", "no", "ssid", ssid],
            check=False).returncode != 0:
        _fail(f"Could not create the {AP_CON} Wi-Fi profile.")
    # Pin band + channel (2.4 GHz): brcmfmac AP mode is more reliable with an
    # explicit channel than letting NetworkManager auto-select. Force clean
    # WPA2-only (RSN/CCMP-AES): a mixed/ambiguous mode makes Windows & Android
    # fall back to the WPS-PIN flow instead of prompting for the passphrase.
    _run([*nm, "connection", "modify", AP_CON,
          "802-11-wireless.mode", "ap",
          "802-11-wireless.band", "bg",
          "802-11-wireless.channel", str(channel),
          "ipv4.method", "shared",
          "ipv4.addresses", AP_ADDR,
          "wifi-sec.key-mgmt", "wpa-psk",
          "wifi-sec.proto", "rsn",
          "wifi-sec.pairwise", "ccmp",
          "wifi-sec.group", "ccmp",
          "wifi-sec.pmf", "disable",
          "wifi-sec.psk", psk], check=False)
    _ok(f"AP profile '{AP_CON}': SSID '{ssid}', WPA2, {AP_ADDR.split('/')[0]}")


# ── privileged helper + watcher + sudoers ──────────────────────────────────────

def install_helpers():
    _write_root_file(
        NETCTL_SRC.replace("__AP_CON__", AP_CON).replace("__IFACE__", AP_IFACE),
        NETCTL_PATH, "0755")
    _ok(f"Installed {NETCTL_PATH} (privileged Wi-Fi helper).")
    _write_root_file(
        NETWATCH_SRC.replace("__AP_CON__", AP_CON).replace("__IFACE__", AP_IFACE),
        NETWATCH_PATH, "0755")
    _ok(f"Installed {NETWATCH_PATH} (AP-fallback watcher).")


def install_sudoers(user):
    # Scope the OASIS user to exactly the oasis-netctl subcommands, nothing else.
    # `connect *` covers the SSID argument (PSK travels on stdin, not argv).
    content = (
        "# OASIS dashboard Wi-Fi controls — generated by enable-ap-fallback.py\n"
        f"# Lets '{user}' scan/join/forget Wi-Fi and raise the AP via oasis-netctl, no password.\n"
        f"Cmnd_Alias OASIS_NETCTL = {NETCTL_PATH} scan, {NETCTL_PATH} status, "
        f"{NETCTL_PATH} connect *, {NETCTL_PATH} forget, {NETCTL_PATH} ap-up\n"
        f"{user} ALL=(root) NOPASSWD: OASIS_NETCTL\n"
    )
    fd, tmp = tempfile.mkstemp(suffix=".sudoers")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        if _run(["sudo", "visudo", "-cf", tmp], check=False).returncode != 0:
            _fail("Generated sudoers file failed validation — NOT installing.")
        if _run(["sudo", "install", "-m", "0440", "-o", "root", "-g", "root",
                 tmp, SUDOERS_PATH], check=False).returncode != 0:
            _fail(f"Could not install {SUDOERS_PATH} (sudo required).")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    _ok(f"Installed {SUDOERS_PATH} for user '{user}'.")


def install_service():
    _write_root_file(NETWATCH_SERVICE, NETWATCH_SVC, "0644")
    _run(["sudo", "systemctl", "daemon-reload"], check=False)
    if _run(["sudo", "systemctl", "enable", "--now", f"{NETWATCH_UNIT}.service"],
            check=False).returncode != 0:
        _warn(f"Could not enable {NETWATCH_UNIT}.service.")
        return
    _ok(f"{NETWATCH_UNIT}.service enabled — AP fallback is armed.")


# ── disable / status ───────────────────────────────────────────────────────────

def disable():
    _run(["sudo", "systemctl", "disable", "--now", f"{NETWATCH_UNIT}.service"],
         check=False, capture_output=True)
    for path in (NETWATCH_SVC, NETWATCH_PATH, NETCTL_PATH, SUDOERS_PATH):
        _run(["sudo", "rm", "-f", path], check=False)
    _run(["sudo", "systemctl", "daemon-reload"], check=False)
    nm = _nmcli_priv()
    if nm:
        _run([*nm, "connection", "delete", AP_CON], check=False,
             capture_output=True)
    _ok("Removed the AP-fallback watcher, helper, sudoers rule and AP profile.")
    _info("avahi-daemon was left installed (harmless). Remove it with: "
          "sudo apt-get remove avahi-daemon")


def status():
    _info(f"watcher unit:  {NETWATCH_SVC}")
    r = _run(["systemctl", "is-active", f"{NETWATCH_UNIT}.service"],
             check=False, capture_output=True, text=True)
    state = (getattr(r, "stdout", "") or "").strip() or "unknown"
    (_ok if state == "active" else _warn)(f"oasis-netwatch.service: {state}")
    nmcli = _nmcli()
    if nmcli:
        r = _run([nmcli, "-t", "-f", "NAME", "connection", "show"],
                 check=False, capture_output=True, text=True)
        have = AP_CON in (getattr(r, "stdout", "") or "").splitlines()
        (_ok if have else _warn)(
            f"AP profile '{AP_CON}': {'present' if have else 'absent'}")
    else:
        _warn("nmcli not found — NetworkManager required (Raspberry Pi OS Bookworm+).")
    # Radio liveness: is wlan0 actually in AP mode + beaconing, and is the
    # regulatory domain set? These are the usual reasons an AP is 'up' in NM but
    # invisible to other devices.
    reg = _current_regdomain()
    if reg and reg != "00":
        _ok(f"regulatory domain: {reg}")
    else:
        _warn("regulatory domain unset (country 00) — AP won't broadcast. "
              "Re-run with --country <CC>, e.g. --country US.")
    r = _run(["iw", "dev", AP_IFACE, "info"], check=False,
             capture_output=True, text=True)
    info = getattr(r, "stdout", "") or ""
    if "type AP" in info:
        m = re.search(r"channel (\d+)", info)
        _ok("radio in AP mode (broadcasting)"
            + (f" · channel {m.group(1)}" if m else ""))
    elif "type managed" in info:
        _info("radio in client (managed) mode — not hosting the AP right now.")
    elif not info:
        _info(f"'iw dev {AP_IFACE} info' unavailable — can't confirm AP RF state.")
    r = _run(["sudo", "-n", "-l"], check=False, capture_output=True, text=True)
    out = getattr(r, "stdout", "") or ""
    (_ok if "OASIS_NETCTL" in out else _warn)(
        "sudo grants the dashboard Wi-Fi controls (oasis-netctl)."
        if "OASIS_NETCTL" in out else
        "sudo does not yet grant the Wi-Fi controls — re-run without --check.")
    present = os.path.exists("/usr/sbin/avahi-daemon") or bool(
        dpkg_installed_version("avahi-daemon"))
    (_ok if present else _warn)(
        f"avahi-daemon: {'installed' if present else 'absent'}")


def run(args):
    print("\n  OASIS — enable-ap-fallback")
    _hr()
    if sys.platform != "linux":
        _fail("AP fallback uses NetworkManager + systemd — Linux (Raspberry Pi OS) only.")

    if args.check:
        status()
        print()
        return

    if args.disable:
        _step(1, "Removing Wi-Fi AP fallback")
        disable()
        print()
        return

    psk = args.password
    if not (8 <= len(psk) <= 63):
        _fail("WPA2 password must be 8–63 characters (--password).")

    user = target_user(args.user)
    _step(1, "Preparing the radio (rfkill + regulatory domain)")
    prep_radio(resolve_country(args.country))
    _step(2, "Installing avahi (<hostname>.local resolution)")
    install_avahi()
    _step(3, f"Creating the '{args.ssid}' AP profile (WPA2, ch {args.channel})")
    create_ap_profile(args.ssid, psk, args.channel)
    _step(4, "Installing the Wi-Fi helper + fallback watcher")
    install_helpers()
    _step(5, f"Granting '{user}' the dashboard Wi-Fi controls")
    install_sudoers(user)
    _step(6, "Arming the AP-fallback watcher")
    install_service()
    print()
    _ok("Done. The Pi now prefers known Wi-Fi and raises the OASIS AP as a fallback.")
    _info(f"AP SSID '{args.ssid}' · password '{psk}' · http://10.42.0.1:8083 "
          f"(or http://{os.uname().nodename.split('.')[0]}.local:8083)")
    _info("Test the AP now (drops any client link on this single radio):")
    _info("  sudo nmcli connection up OASIS-AP && iw dev wlan0 info")
    _info("Undo with: python3 scripts/enable-ap-fallback.py --disable")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Fall back to a WPA2 Wi-Fi access point when no known network "
                    "is in range, so the OASIS dashboard is always reachable.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--ssid", default=DEFAULT_SSID,
                    help=f"AP network name (default: {DEFAULT_SSID}).")
    ap.add_argument("--password", default=DEFAULT_PSK,
                    help="AP WPA2 password, 8–63 chars "
                         f"(default: {DEFAULT_PSK}).")
    ap.add_argument("--country", default=None,
                    help="Wi-Fi regulatory country code, e.g. US. Needed for AP "
                         "beaconing. If omitted and the Pi's country is unset, "
                         "you'll be prompted for it.")
    ap.add_argument("--channel", type=int, default=6,
                    help="2.4 GHz AP channel (default: 6).")
    ap.add_argument("--user", default=None,
                    help="User the OASIS server runs as (default: current/SUDO_USER).")
    ap.add_argument("--check", action="store_true",
                    help="Report status and exit (no changes).")
    ap.add_argument("--disable", action="store_true",
                    help="Remove the AP fallback (watcher, helper, sudoers, AP profile).")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
