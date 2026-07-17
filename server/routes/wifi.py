"""
Wi-Fi route blueprint — the dashboard Wi-Fi picker (status/scan/connect/
forget) driving NetworkManager through the pinned oasis-netctl helper under
a scoped sudoers grant (scripts/enable-ap-fallback.py). Extracted verbatim
from server/app.py in the blueprint split; URLs unchanged.
"""

import os
import subprocess
import sys

from flask import Blueprint, jsonify, request

bp = Blueprint("wifi", __name__)

# ── Wi-Fi controls (scan / join known networks; AP fallback) ──────────────────
# The dashboard Wi-Fi picker drives NetworkManager through a single pinned helper,
# /usr/local/bin/oasis-netctl, run as `sudo -n`. scripts/enable-ap-fallback.py
# installs the helper + a sudoers rule scoped to exactly its four subcommands, so
# no credential ever reaches this layer. The helper also backs the AP-fallback
# watcher (oasis-netwatch) that keeps the dashboard reachable off-grid.
NETCTL_PATH = "/usr/local/bin/oasis-netctl"       # must match enable-ap-fallback.py
AP_CON_NAME = "OASIS-AP"                           # must match enable-ap-fallback.py


def _netctl(*sub, stdin=None, timeout=45):
    """Run `sudo -n oasis-netctl <sub…>`; returns (ok, stdout, stderr).
    ok=False (and empty stdout) when the helper is missing or unauthorised."""
    if sys.platform != "linux" or not os.path.exists(NETCTL_PATH):
        return False, "", "unavailable"
    try:
        r = subprocess.run(["sudo", "-n", NETCTL_PATH, *sub],
                           input=stdin, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", str(exc)
    return r.returncode == 0, r.stdout, r.stderr


def _nmcli_unescape(field):
    """Undo nmcli -t terse-mode escaping ('\\:' -> ':', '\\\\' -> '\\')."""
    return field.replace(r"\:", ":").replace(r"\\", "\\")


def _split_terse(line):
    """Split an nmcli -t line on unescaped ':' separators."""
    parts, buf, esc = [], "", False
    for ch in line:
        if esc:
            buf += "\\" + ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [_nmcli_unescape(p) for p in parts]


def _current_ssid():
    """The SSID the radio is associated with, via read-only iwgetid (no sudo).
    NetworkManager profile names can be netplan-style (netplan-wlan-<SSID>), so
    we read the live SSID rather than showing the connection name."""
    try:
        r = subprocess.run(["iwgetid", "-r"], capture_output=True,
                           text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


@bp.route("/api/wifi/status")
def api_wifi_status():
    """Report Wi-Fi mode (ap | client | none), SSID and whether the AP-fallback
    controls are wired up. Linux + NetworkManager only; degrades elsewhere."""
    if sys.platform != "linux":
        return jsonify({"ok": True, "supported": False})
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": True, "supported": False,
                        "reason": "controls-not-installed"})
    ok, out, _ = _netctl("status")
    mode, ssid = "none", None
    if ok:
        for line in out.splitlines():
            cols = _split_terse(line)
            if len(cols) < 2:
                continue
            name, ctype = cols[0], cols[1]
            if name == AP_CON_NAME:
                mode, ssid = "ap", AP_CON_NAME
                break
            if "wireless" in ctype:
                # Prefer the live SSID over the NM profile name (netplan-wlan-*).
                mode, ssid = "client", _current_ssid() or name
    return jsonify({"ok": True, "supported": True, "mode": mode,
                    "ssid": ssid, "ap_ip": "10.42.0.1"})


@bp.route("/api/wifi/scan")
def api_wifi_scan():
    """List Wi-Fi networks in range (via NetworkManager). Linux only."""
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False}), 200
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": False, "supported": False,
                        "reason": "controls-not-installed"}), 200
    ok, out, err = _netctl("scan")
    if not ok:
        low = (err or "").lower()
        reason = "no-privilege" if ("password" in low or "not allowed" in low) else "scan-failed"
        return jsonify({"ok": False, "supported": True, "reason": reason,
                        "error": (err or "").strip()[:200]}), 200
    seen, nets = set(), []
    for line in out.splitlines():
        cols = _split_terse(line)
        if len(cols) < 4:
            continue
        in_use, ssid, signal, security = cols[0], cols[1], cols[2], cols[3]
        if not ssid or ssid in seen:            # skip hidden/blank + de-dup by SSID
            continue
        seen.add(ssid)
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        nets.append({
            "ssid":     ssid,
            "signal":   sig,
            "secure":   bool(security and security != "--"),
            "in_use":   in_use.strip() in ("*", "yes"),
        })
    nets.sort(key=lambda n: n["signal"], reverse=True)
    return jsonify({"ok": True, "supported": True, "networks": nets})


@bp.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    """Join a Wi-Fi network by SSID + password (WPA2). Linux only.

    Body (JSON): {"ssid": "<name>", "password": "<8–63 chars>"}. The single
    onboard radio can't host the AP and be a client at once, so joining a network
    drops the OASIS AP — clients on the AP must reconnect via the new IP. CSRF is
    blocked by the same custom-header requirement as /api/service.
    """
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False,
                        "error": "NetworkManager not available"}), 200
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": False, "supported": False,
                        "error": "Wi-Fi controls not installed — run "
                                 "scripts/enable-ap-fallback.py"}), 200

    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    psk  = data.get("password") or ""
    if not ssid:
        return jsonify({"ok": False, "error": "SSID required"}), 400
    if not (8 <= len(psk) <= 63):
        return jsonify({"ok": False, "error": "WPA2 password must be 8–63 characters"}), 400

    # Trailing newline so the helper's `read` sees a complete line (without it,
    # `read` hits EOF, returns non-zero, and the password would be dropped).
    ok, out, err = _netctl("connect", ssid, stdin=psk + "\n", timeout=60)
    if not ok:
        msg = (err or out or "").strip() or "connection failed"
        low = msg.lower()
        if "password" in low and "authoriz" not in low:
            # Distinguish a missing sudo grant from a wrong Wi-Fi password.
            if "not allowed" in low or "a terminal is required" in low:
                msg += " — run: python3 scripts/enable-ap-fallback.py"
        return jsonify({"ok": False, "ssid": ssid, "error": msg[:300]}), 200
    return jsonify({"ok": True, "ssid": ssid})


@bp.route("/api/wifi/forget", methods=["POST"])
def api_wifi_forget():
    """Forget the current Wi-Fi network — delete its saved profile so the radio
    disconnects and stops auto-rejoining. The AP-fallback watcher then raises the
    OASIS access point. Linux only; CSRF-guarded like /api/service."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if sys.platform != "linux":
        return jsonify({"ok": False, "supported": False,
                        "error": "NetworkManager not available"}), 200
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": False, "supported": False,
                        "error": "Wi-Fi controls not installed — run "
                                 "scripts/enable-ap-fallback.py"}), 200

    ok, out, err = _netctl("forget", timeout=30)
    if not ok:
        msg = (err or out or "").strip() or "could not forget network"
        low = msg.lower()
        if "not allowed" in low or "a terminal is required" in low or "password is required" in low:
            msg += " — run: python3 scripts/enable-ap-fallback.py"
        return jsonify({"ok": False, "error": msg[:300]}), 200
    return jsonify({"ok": True})


