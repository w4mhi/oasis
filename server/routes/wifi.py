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

from common.api_shape import clamp_limit

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


def _no_privilege(err):
    """True when the helper failed because the sudoers grant is missing, rather
    than because the operation itself did not work. Worth telling apart: one is
    fixed by running scripts/enable-ap-fallback.py, the other is not."""
    low = (err or "").lower()
    return ("password" in low or "not allowed" in low
            or "a terminal is required" in low)


@bp.route("/api/wifi/status")
def api_wifi_status():
    """Report Wi-Fi mode (ap | client | none), SSID and whether the AP-fallback
    controls are wired up. Linux + NetworkManager only; degrades elsewhere.

    §5: this returned three different key sets — two keys off-Linux, three when
    the helper was absent, five when it worked. Now one shape, with null for
    what could not be determined."""
    if sys.platform != "linux":
        return jsonify({"ok": True, "supported": False, "mode": None,
                        "ssid": None, "ap_ip": None, "reason": "not-linux"})
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": True, "supported": False, "mode": None,
                        "ssid": None, "ap_ip": None,
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
                    "ssid": ssid, "ap_ip": "10.42.0.1", "reason": None})


@bp.route("/api/wifi/scan")
def api_wifi_scan():
    """List Wi-Fi networks in range (via NetworkManager). Linux only.

    §2: a scan that could not run is not a failed REQUEST — same shape as
    /api/health/feed-flow, which has exactly this problem. `scanned` says
    whether the radio actually looked, so an empty `networks` on a host with
    no sudo grant cannot be misread as "no networks in range".

    §5: one key set for all five outcomes. It had four."""
    supported = sys.platform == "linux" and os.path.exists(NETCTL_PATH)
    reason = None
    nets = None
    detail = None
    if sys.platform != "linux":
        reason = "not-linux"
    elif not supported:
        reason = "controls-not-installed"
    else:
        ok, out, err = _netctl("scan")
        if not ok:
            reason = "no-privilege" if _no_privilege(err) else "scan-failed"
            detail = (err or "").strip()[:200] or None
        else:
            seen, nets = set(), []
            for line in out.splitlines():
                cols = _split_terse(line)
                if len(cols) < 4:
                    continue
                in_use, ssid, signal, security = cols[0], cols[1], cols[2], cols[3]
                if not ssid or ssid in seen:    # skip hidden/blank + de-dup by SSID
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
    found = nets if nets is not None else []
    limit = clamp_limit(request.args.get("limit"), 200, 1000)
    shown = found[:limit]
    return jsonify({
        "ok":        True,
        "supported": supported,
        "scanned":   nets is not None,
        "networks":  shown,
        "total":     len(found),
        "count":     len(shown),
        "truncated": len(found) > len(shown),
        "limit":     limit,
        "reason":    reason,
        "detail":    detail,
    })


@bp.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    """Join a Wi-Fi network by SSID + password (WPA2). Linux only.

    Body (JSON): {"ssid": "<name>", "password": "<8–63 chars>"}. The single
    onboard radio can't host the AP and be a client at once, so joining a network
    drops the OASIS AP — clients on the AP must reconnect via the new IP. CSRF is
    blocked by the same custom-header requirement as /api/service.
    """
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden",
                        "code": "FORBIDDEN"}), 403
    # §2: unlike the probes, this is an ACTION — "join this network" either
    # happened or it did not, so ok:false is right here. What was wrong was
    # serving it with HTTP 200, which made a refused join look like a
    # successful call to anything that checks the status first.
    if sys.platform != "linux":
        return jsonify({"ok": False, "error": "NetworkManager not available",
                        "code": "WIFI_CONTROLS_UNAVAILABLE"}), 503
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": False,
                        "error": "Wi-Fi controls not installed — run "
                                 "scripts/enable-ap-fallback.py",
                        "code": "WIFI_CONTROLS_UNAVAILABLE"}), 503

    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    psk  = data.get("password") or ""
    if not ssid:
        return jsonify({"ok": False, "error": "SSID required",
                        "code": "SSID_REQUIRED"}), 400
    if not (8 <= len(psk) <= 63):
        return jsonify({"ok": False,
                        "error": "WPA2 password must be 8–63 characters",
                        "code": "INVALID_PASSWORD"}), 400

    # Trailing newline so the helper's `read` sees a complete line (without it,
    # `read` hits EOF, returns non-zero, and the password would be dropped).
    ok, out, err = _netctl("connect", ssid, stdin=psk + "\n", timeout=60)
    if not ok:
        msg = (err or out or "").strip() or "connection failed"
        if _no_privilege(err or out):
            # A missing sudo grant is a different problem from a wrong Wi-Fi
            # password, and only one of them is fixed by re-running a script.
            return jsonify({"ok": False, "ssid": ssid,
                            "error": msg[:300] + " — run: python3 "
                                                 "scripts/enable-ap-fallback.py",
                            "code": "WIFI_NO_PRIVILEGE"}), 503
        # 502: the helper was reached and reported failure — the same shape the
        # contract already uses when a backing daemon answers with bad news.
        return jsonify({"ok": False, "ssid": ssid, "error": msg[:300],
                        "code": "WIFI_CONNECT_FAILED"}), 502
    return jsonify({"ok": True, "ssid": ssid})


@bp.route("/api/wifi/forget", methods=["POST"])
def api_wifi_forget():
    """Forget the current Wi-Fi network — delete its saved profile so the radio
    disconnects and stops auto-rejoining. The AP-fallback watcher then raises the
    OASIS access point. Linux only; CSRF-guarded like /api/service."""
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden",
                        "code": "FORBIDDEN"}), 403
    if sys.platform != "linux":
        return jsonify({"ok": False, "error": "NetworkManager not available",
                        "code": "WIFI_CONTROLS_UNAVAILABLE"}), 503
    if not os.path.exists(NETCTL_PATH):
        return jsonify({"ok": False,
                        "error": "Wi-Fi controls not installed — run "
                                 "scripts/enable-ap-fallback.py",
                        "code": "WIFI_CONTROLS_UNAVAILABLE"}), 503

    ok, out, err = _netctl("forget", timeout=30)
    if not ok:
        msg = (err or out or "").strip() or "could not forget network"
        if _no_privilege(err or out):
            return jsonify({"ok": False,
                            "error": msg[:300] + " — run: python3 "
                                                 "scripts/enable-ap-fallback.py",
                            "code": "WIFI_NO_PRIVILEGE"}), 503
        return jsonify({"ok": False, "error": msg[:300],
                        "code": "WIFI_FORGET_FAILED"}), 502
    return jsonify({"ok": True})


