"""
Health route blueprint — the dashboard's probes: local-service HTTP checks,
binary presence, systemd unit status, the RTL-SDR feed-flow sniffer, ZIM
content, hardware RTC, and known config-file checks (incl. Pat's config
discovery). Extracted verbatim from server/app.py in the blueprint split;
URLs unchanged.
"""

import os
import re
import subprocess
import sys
import time

from flask import Blueprint, jsonify, request

from routes.service_control import _OASIS_SERVICES

bp = Blueprint("health", __name__)


@bp.route("/api/health/probe")
def api_health_probe():
    """Proxy health check for external services.
    Accepts ?service=graywolf|kiwix|webssh&port=N.
    Makes a real server-side HTTP GET so the browser receives an actual
    200/non-200 distinction rather than an opaque no-cors response."""
    import urllib.request
    import urllib.error

    service = request.args.get("service", "")
    try:
        port = int(request.args.get("port", "0"))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid port",
                        "code": "INVALID_PORT"}), 400

    ALLOWED = {"graywolf", "kiwix", "webssh", "aprs_api", "winlink", "openwebrx"}
    if service not in ALLOWED:
        return jsonify({"ok": False, "error": "unknown service",
                        "code": "UNKNOWN_SERVICE"}), 400
    if not (1 <= port <= 65535):
        return jsonify({"ok": False, "error": "port out of range",
                        "code": "PORT_OUT_OF_RANGE"}), 400

    url = f"http://127.0.0.1:{port}/"
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "OASIS-HealthProbe/1.0")
        with urllib.request.urlopen(req, timeout=3) as resp:
            status = resp.status
        return jsonify({"ok": True, "service": service, "port": port,
                        "reachable": True, "status": status, "detail": None})
    except urllib.error.HTTPError as e:
        # Service replied with an HTTP error — it IS running, just returning
        # an error code (e.g. 404 on / is still "up" for our purposes).
        return jsonify({"ok": True, "service": service, "port": port,
                        "reachable": True, "status": e.code, "detail": None})
    except Exception as e:
        # §2: the PROBE SUCCEEDED — it connected, or tried to and learned the
        # service is not listening. "Nothing is on port 8080" is the answer,
        # not a failure to answer. `reachable` carries it.
        # NOT `error` — §2 reserves that for a failed request, and this one
        # succeeded. `detail` is the same word /api/adsb/health settled on.
        return jsonify({"ok": True, "service": service, "port": port,
                        "reachable": False, "status": None, "detail": str(e)})


@bp.route("/api/health/binary")
def api_health_binary():
    """Check whether a named system binary is installed.
    Accepts ?name=rtl_test|rtl_fm|socat|tcpdump|pat|ttyd|kiwix-serve|...
    Only alphanumeric names with hyphens/underscores are accepted.

    Looks on PATH first, then falls back to the standard bin dirs — the WSGI
    server can run under a trimmed systemd PATH that omits /usr/bin, which made
    installed tools (e.g. rtl_test) look missing."""
    import shutil
    name = request.args.get("name", "")
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$', name):
        return jsonify({"ok": False, "error": "invalid binary name",
                        "code": "INVALID_BINARY_NAME"}), 400
    path = shutil.which(name)
    if not path:
        for d in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
                  "/usr/bin", "/sbin", "/bin"):
            cand = os.path.join(d, name)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                path = cand
                break
    # §2: `ok` was `bool(path)` — "we looked and it isn't installed" reported as
    # a failed request. The lookup always succeeds; `present` is the finding.
    return jsonify({"ok": True, "binary": name, "present": bool(path),
                    "path": path or None})


@bp.route("/api/health/service")
def api_health_service():
    """Report systemd status for a known OASIS service (Linux only).
    Accepts ?name=graywolf|graywolf-api|pat|kiwix|webssh|aprs-sdr-feed|oasis."""
    import subprocess
    import sys as _sys

    name = request.args.get("name", "")
    if name not in _OASIS_SERVICES:
        return jsonify({"ok": False, "error": "unknown service",
                        "code": "UNKNOWN_SERVICE"}), 400
    if _sys.platform != "linux":
        # §2: asking a Mac about systemd and being told "no systemd here" is a
        # successful answer. §5: same key set as the Linux path, nulls for what
        # cannot be known — not a shorter dict.
        return jsonify({"ok": True, "service": name, "supported": False,
                        "active": None, "enabled": None,
                        "installed": False, "running": False})

    def _q(verb):
        try:
            r = subprocess.run(["systemctl", verb, f"{name}.service"],
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip()
        except Exception:
            return ""

    active  = _q("is-active")     # active | inactive | failed | activating | ""
    enabled = _q("is-enabled")    # enabled | disabled | static | "" (not installed)

    # gpsd is socket-activated: gpsd.service is only "active" while a client is
    # connected and goes idle otherwise, but it's available whenever gpsd.socket
    # is listening. Treat a live socket as active so the card isn't falsely down.
    if name == "gpsd" and active != "active":
        try:
            sock = subprocess.run(["systemctl", "is-active", "gpsd.socket"],
                                  capture_output=True, text=True, timeout=5).stdout.strip()
            if sock == "active":
                active = "active"
                if not enabled:
                    enabled = "enabled"
        except Exception:
            pass

    return jsonify({
        # §2: `ok` was `active == "active"`, so a cleanly-reported STOPPED
        # service was indistinguishable from a failed request. Every consumer
        # already branched on `active`/`installed`/`enabled` and none read `ok`,
        # which is the tell that it was never carrying real information here.
        "ok":        True,
        "service":   name,
        "supported": True,
        "running":   active == "active",
        "active":    active or "unknown",
        "enabled":   enabled or "not-found",
        # Installed = the unit file exists in any real state (enabled/disabled/
        # static/indirect/masked/…). Absent units make `systemctl is-enabled`
        # print "not-found" (Pi OS Trixie / systemd ≥ 254, on stdout) or nothing
        # (older) — both mean not installed, so exclude them explicitly rather
        # than relying on `bool(enabled)`.
        "installed": enabled not in ("", "not-found"),
    })


# ── RTL-SDR → GrayWolf feed flow probe ────────────────────────────────────────
# The aprs-sdr-feed.service unit is `rtl_fm … | socat … UDP-SENDTO:127.0.0.1:7355`.
# systemctl is-active only proves the pipe's tail (socat) is alive — a dongle
# yanked from USB leaves the unit "active" while ZERO audio reaches GrayWolf.
# To prove audio is actually moving we passively sniff the loopback feed with
# tcpdump (libpcap copies datagrams; it never steals them from GrayWolf's reader).
# socat emits ~50 datagrams/s (one per 20 ms audio chunk), so a short capture
# either fills fast (healthy) or times out empty (silent/dead feed).
FEED_FLOW_PORT     = 7355     # matches services/rtl-feed/common/feed.py default + the sudoers rule
_FEED_FLOW_NPKTS   = 10       # capture up to N packets, then tcpdump exits
_FEED_FLOW_TIMEOUT = 1.0      # hard cap (s): bounds a dead feed's response time
_FEED_FLOW_NOMINAL = 50       # pkt/s at full feed — the UI scales the bar to this
# Pinned tcpdump argv (sans sudo + binary path). MUST match the OASIS_SNIFF
# Cmnd_Alias in scripts/enable-service-controls.py token-for-token, or `sudo -n`
# denies it. The port is baked in (not client-supplied) so there is no argument-
# injection surface on the privileged path.
_FEED_FLOW_ARGS = ["-ni", "lo", "-l", "-c", str(_FEED_FLOW_NPKTS),
                   "udp", "port", str(FEED_FLOW_PORT)]


def _resolve_tcpdump():
    """tcpdump path, PATH first then the standard sbin/bin dirs (the WSGI server
    can run under a trimmed systemd PATH). Mirrors api_health_binary's lookup."""
    import shutil
    path = shutil.which("tcpdump")
    if not path:
        for d in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
                  "/usr/bin", "/sbin", "/bin"):
            cand = os.path.join(d, "tcpdump")
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                path = cand
                break
    return path


@bp.route("/api/health/feed-flow")
def api_health_feed_flow():
    """Report whether UDP datagrams are actually flowing on the RTL-SDR feed port
    (a data-flow health check the systemd is-active probe can't give). Returns
    packet rate over a short passive capture. Linux + scoped sudo (tcpdump) only.

    §2/§5: this had FIVE returns, four of them `ok:false` with HTTP 200 and each
    with a different key set, so "the probe couldn't run" and "the request
    failed" were the same value and the shape depended on which one you hit.
    Now one exit with one key set: `supported` (can this host probe at all),
    `probed` (did the capture actually run), `flowing` (the finding), and
    `reason` naming the obstacle when there was one.

    `flowing` is null when nothing was measured — reporting false there would
    say "the feed is dead" when the truth is "we never listened", and that
    distinction is the entire value of this probe."""
    supported = sys.platform == "linux"
    reason = None if supported else "not-linux"
    tcpdump = _resolve_tcpdump() if supported else None
    if supported and not tcpdump:
        reason = "tcpdump-missing"

    packets = None
    elapsed = None
    detail = None
    if supported and tcpdump:
        argv = ["sudo", "-n", tcpdump, *_FEED_FLOW_ARGS]
        t0 = time.monotonic()
        out = err = ""
        rc = None
        timed_out = False
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=_FEED_FLOW_TIMEOUT)
            out, err, rc = r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired as e:
            # Healthy feeds exit on -c N before this; a timeout means the feed is
            # slow or dead. Partial stdout (any trickle) is still on the exception.
            out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
            timed_out = True
        except Exception as e:
            reason, detail = "probe-error", str(e)
        if reason is None:
            elapsed = max(time.monotonic() - t0, 1e-3)
            captured = sum(1 for ln in out.splitlines() if ln.strip())
            # No packets AND tcpdump itself failed (not merely an empty capture)
            # → say whether it's a missing sudo grant or some other tcpdump error.
            if captured == 0 and not timed_out and rc not in (0, None):
                low = (err or "").lower()
                if ("password is required" in low or "not allowed to execute" in low
                        or "a terminal is required" in low):
                    reason = "no-privilege"
                else:
                    reason, detail = "probe-error", (err or "").strip()[:200]
                elapsed = None
            else:
                packets = captured

    probed = packets is not None
    return jsonify({
        "ok":          True,
        "supported":   supported,
        "probed":      probed,
        "flowing":     (packets > 0) if probed else None,
        "port":        FEED_FLOW_PORT,
        "packets":     packets,
        "elapsed_ms":  round(elapsed * 1000) if elapsed is not None else None,
        "pps":         round(packets / elapsed, 1) if probed else None,
        "nominal_pps": _FEED_FLOW_NOMINAL,
        "reason":      reason,
        "detail":      detail,
    })


@bp.route("/api/health/zim")
def api_health_zim():
    """Offline Wikipedia/ZIM content presence for the dashboard Wikipedia card.
    Kiwix content lives OUTSIDE the suite root (~/oasis-offline/zim by default,
    or an SSD), so /api/browse can't see it — scan the standard locations here.
    Uses the same $SUDO_USER-aware home resolution as the kiwix installer so
    this matches the directory kiwix-start actually looks in, even if this
    Flask process and the installer worker run as different users."""
    from services.kiwix.common.kiwix import target_user_home
    candidates = [
        os.path.join(target_user_home()[1], "oasis-offline", "zim"),
        "/mnt/ssd/zim",
        "/mnt/ssd/Documents/reference/zim",
    ]
    for d in candidates:
        try:
            zims = [f for f in os.listdir(d) if f.endswith(".zim")]
        except OSError:
            continue
        if zims:
            total = 0
            for f in zims:
                try:
                    total += os.path.getsize(os.path.join(d, f))
                except OSError:
                    pass
            return jsonify({"ok": True, "count": len(zims), "dir": d,
                            "total_gb": round(total / 1e9, 1),
                            "names": [os.path.splitext(f)[0] for f in sorted(zims)]})
    return jsonify({"ok": True, "count": 0, "dir": candidates[0],
                    "total_gb": 0, "names": []})


@bp.route("/api/health/rtc")
def api_health_rtc():
    """Hardware-RTC status from sysfs (no sudo): presence, driver name, whether
    it set the system clock at boot (hctosys), and drift vs the system clock.
    The Witty Pi 3's DS3231 appears here once features/rtc-hat/enable-rtc.py + a reboot load it."""
    import sys as _sys
    base = "/sys/class/rtc/rtc0"
    if _sys.platform != "linux" or not os.path.isdir(base):
        # §5: same keys as the present path. A two-key dict here and a five-key
        # dict below meant the caller had to know which world it was in first.
        return jsonify({"ok": True, "present": False, "name": None,
                        "hctosys": None, "drift_s": None})

    def _read(name):
        try:
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    drift = None
    date_s, time_s = _read("date"), _read("time")   # sysfs RTC date/time are UTC
    if date_s and time_s:
        try:
            import calendar
            import datetime as _dt
            t = _dt.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
            drift = round(time.time() - calendar.timegm(t.timetuple()), 1)
        except (ValueError, OverflowError):
            pass

    return jsonify({"ok": True, "present": True, "name": _read("name") or None,
                    "hctosys": _read("hctosys") == "1", "drift_s": drift})


# Fixed config artifacts written by the install/enable scripts. Keys map to
# server-side absolute paths — no arbitrary path input is accepted.
def _health_paths():
    return {
        "rtl_blacklist": "/etc/modprobe.d/rtlsdr-blacklist.conf",
        "pat_config":    _pat_config_path(),
    }


@bp.route("/api/health/file")
def api_health_file():
    """Existence check for a known OASIS config artifact (no arbitrary paths).
    Accepts ?key=rtl_blacklist|pat_config. For pat_config also reports whether
    the callsign and Winlink password are set (booleans only — never values)."""
    key  = request.args.get("key", "")
    path = _health_paths().get(key)
    if not path:
        return jsonify({"ok": False, "error": "unknown key",
                        "code": "UNKNOWN_KEY"}), 400

    exists = os.path.isfile(path)
    # §5: these two were added only for an existing, readable pat_config, so
    # "no Winlink password" and "not a pat_config" and "the file is corrupt"
    # were all the same absent key. null means unknown; false means we looked.
    callsign_set = password_set = None
    if key == "pat_config" and exists:
        try:
            import json as _json
            with open(path) as fh:
                cfg = _json.load(fh)
            callsign_set = bool(cfg.get("mycall"))
            password_set = bool(cfg.get("secure_login_password"))
        except Exception:
            pass
    # §2: `ok` was `exists`, so a correct report of "not configured yet" read as
    # a failed request. §10: built inline — this was `jsonify(info)`, one of the
    # returns whose shape no reviewer and no gate could see.
    return jsonify({"ok": True, "key": key, "exists": exists,
                    "callsign_set": callsign_set, "password_set": password_set})


def _pat_config_path():
    """Best-effort path to Pat's config.json.

    Setup can run under a different account than Pat's target user (for example
    gunicorn as root while Pat config lives under /home/pi), so probe a small
    set of candidate homes and use the first existing config.
    """
    homes = []

    def _add_home(h):
        h = (h or "").strip()
        if h and h not in homes:
            homes.append(h)

    current_home = os.path.expanduser("~")
    _add_home(current_home)

    sudo_user = (os.environ.get("SUDO_USER") or "").strip()
    if sudo_user:
        try:
            import pwd as _pwd
            su_home = _pwd.getpwnam(sudo_user).pw_dir
            _add_home(su_home)
        except Exception:
            pass

    # Pat may run as a different service user than the web server process.
    # Probe systemd for that user/home and include it as a candidate.
    try:
        import pwd as _pwd
        r = subprocess.run(
            ["systemctl", "show", "pat", "--property", "User", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        pat_user = (r.stdout or "").strip()
        if pat_user:
            _add_home(_pwd.getpwnam(pat_user).pw_dir)
    except Exception:
        pass

    # Explicit HOME from the unit environment takes precedence when present.
    try:
        r = subprocess.run(
            ["systemctl", "show", "pat", "--property", "Environment", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        env_line = (r.stdout or "").strip()
        for token in env_line.split():
            if token.startswith("HOME="):
                _add_home(token.split("=", 1)[1])
                break
    except Exception:
        pass

    # Common fallback locations on Pi and dev boxes.
    _add_home("/home/pi")
    _add_home("/root")
    try:
        for ent in os.scandir("/home"):
            if ent.is_dir(follow_symlinks=False):
                _add_home(ent.path)
    except Exception:
        pass

    seen = set()
    candidates = []
    for home in homes:
        if not home:
            continue
        p = os.path.join(home, ".config", "pat", "config.json")
        if p not in seen:
            seen.add(p)
            candidates.append(p)

    for p in candidates:
        if os.path.isfile(p):
            return p
    return candidates[0] if candidates else os.path.join(current_home, ".config", "pat", "config.json")


