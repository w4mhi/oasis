"""
Winlink route blueprint — the OASIS-styled mail UI's static files plus the
same-origin pass-through proxies to Pat's JSON API (port 8082). Extracted
verbatim from server/app.py in the blueprint split; URLs unchanged.
"""

import os

from flask import Blueprint, Response, jsonify, request, send_from_directory

import appconfig

WINLINK_DIR = os.path.join(appconfig.SUITE_ROOT, "services", "winlink", "static")

bp = Blueprint("winlink", __name__)


@bp.route("/server/winlink/<path:filename>")
def winlink_static(filename):
    """Serve the Winlink UI from the service-owned static directory."""
    return send_from_directory(WINLINK_DIR, filename)


# ── Winlink (Pat) proxy ───────────────────────────────────────────────────────
# OASIS ships an OASIS-styled Winlink mail client (/server/winlink/mail.html) that talks
# to Pat's JSON API. Pat runs on port 8082 and does NOT emit CORS headers, so the
# browser stays same-origin by going through these thin pass-through proxies
# (same pattern as the /api/aprs/* routes above). The live connect-session log is
# the one exception: the page opens a WebSocket straight to Pat (ws://host:8082/ws),
# which is not subject to CORS and keeps streaming off this sync backend.
#
# These are byte/JSON pass-throughs, so they are resilient to Pat payload-shape
# changes; the front-end binds the actual field names.
WINLINK_PORT = 8082
WINLINK_BOXES = {"in", "out", "sent", "archive"}


def _winlink_proxy(path, *, method="GET", query="", data=None, headers=None,
                   timeout=10):
    """Forward a request to Pat on WINLINK_PORT and pass its response through.

    Mirrors api_aprs_*_proxy: same-origin pass-through, verbatim HTTPError body,
    503 on URLError/timeout with an OASIS-style message.
    """
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{WINLINK_PORT}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200) or 200
        return Response(body, status=status, content_type="application/json")
    except urllib.error.HTTPError as e:
        # Pat is up but returned an error — pass its body through verbatim.
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"Pat (Winlink) unreachable ({reason}). "
                                 "Is the 'pat' service running on "
                                 f"port {WINLINK_PORT}?"}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "Pat (Winlink) timed out."}), 503


@bp.route("/api/winlink/mailbox/<box>", methods=["GET"])
def api_winlink_mailbox_list(box):
    """List messages in a Pat mailbox (in / out / sent / archive)."""
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox"}), 400
    return _winlink_proxy(f"/api/mailbox/{box}")


@bp.route("/api/winlink/mailbox/<box>/<mid>", methods=["GET", "DELETE"])
def api_winlink_message(box, mid):
    """Read or delete a single Pat message."""
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox"}), 400
    return _winlink_proxy(f"/api/mailbox/{box}/{mid}", method=request.method)


@bp.route("/api/winlink/mailbox/<box>/<mid>/<path:attachment>", methods=["GET"])
def api_winlink_attachment(box, mid, attachment):
    """Stream a message attachment from Pat, passing Pat's Content-Type through.

    Unlike the JSON proxies above, attachments are arbitrary bytes (form XML,
    FormData.txt, photos, PDFs), so we forward Pat's Content-Type rather than
    forcing application/json. ?download=1 adds a Content-Disposition so the
    browser saves rather than renders inline.
    """
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox"}), 400
    import urllib.request
    import urllib.error
    import urllib.parse

    url = (f"http://127.0.0.1:{WINLINK_PORT}/api/mailbox/"
           f"{box}/{mid}/{urllib.parse.quote(attachment)}")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
        out = Response(body, status=200, content_type=ctype)
        if request.args.get("download"):
            safe = attachment.replace('"', "").replace("\\", "")
            out.headers["Content-Disposition"] = f'attachment; filename="{safe}"'
        return out
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"Pat (Winlink) unreachable ({reason})."}), 503
    except TimeoutError:
        return jsonify({"ok": False, "error": "Pat (Winlink) timed out."}), 503


@bp.route("/api/winlink/mailbox/out", methods=["POST"])
def api_winlink_compose():
    """Queue a composed message into Pat's outbox.

    Forwards the form body (to / cc / subject / body) verbatim with its
    Content-Type so Pat parses it exactly as its own UI would.
    """
    headers = {}
    ctype = request.headers.get("Content-Type")
    if ctype:
        headers["Content-Type"] = ctype
    return _winlink_proxy("/api/mailbox/out", method="POST",
                          data=request.get_data(), headers=headers)


@bp.route("/api/winlink/status", methods=["GET"])
def api_winlink_status():
    """Proxy Pat's connection status."""
    return _winlink_proxy("/api/status")


@bp.route("/api/winlink/aliases", methods=["GET"])
def api_winlink_aliases():
    """Proxy Pat's configured connect aliases (available transports)."""
    return _winlink_proxy("/api/connect_aliases")


@bp.route("/api/winlink/disconnect", methods=["POST"])
def api_winlink_disconnect():
    """Abort the in-progress Pat connect session (the Abort button).

    POST + custom-header gate: this mutates state (kills the active RF/telnet
    session), so it gets the same CSRF protection as /api/service — a cross-
    origin page can't set the header without a preflight this never grants.
    Pat's own endpoint stays GET; only the OASIS-facing surface changes.

    Returns 400 from Pat when there's no active session — harmless.
    """
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return _winlink_proxy("/api/disconnect")


# Transport modes Pat's rmslist accepts (verified against a live Pat). The
# no-mode call hangs, so a mode is required and allowlisted.
RMS_MODES = {"packet", "ardop", "vara", "varahf", "varafm", "pactor"}


@bp.route("/api/winlink/rmslist", methods=["GET"])
def api_winlink_rmslist():
    """Slim proxy of Pat's RMS gateway list for one transport mode.

    Pat's /api/rmslist?mode=X returns the gateways PLUS a per-gateway VOACAP
    propagation report whose raw text is ~95% of the (multi-MB) payload. We
    strip that — keeping the link_quality score — so the browser caches a lean
    snapshot (essential on the Pi Zero). The data is sourced from Pat's web API,
    so it needs connectivity at download time; the client caches it for offline
    use. Generous timeout because Pat computes the predictions live.
    """
    import json as _json
    import urllib.request
    import urllib.error

    mode = (request.args.get("mode") or "").strip().lower()
    if mode not in RMS_MODES:
        return jsonify({"ok": False, "error": "unknown mode"}), 400

    url = f"http://127.0.0.1:{WINLINK_PORT}/api/rmslist?mode={mode}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type="application/json")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False,
                        "error": f"Pat (Winlink) unreachable ({reason}). The "
                                 "gateway list needs Pat running + connectivity."}), 503
    except TimeoutError:
        return jsonify({"ok": False,
                        "error": "Pat timed out building the gateway list."}), 503

    try:
        gateways = _json.loads(raw)
    except ValueError:
        return jsonify({"ok": False, "error": "Pat returned malformed data."}), 502

    slim = []
    for g in (gateways or []):
        if not isinstance(g, dict):
            continue
        freq = g.get("freq") or {}
        dial = g.get("dial") or {}
        pred = g.get("prediction") or {}
        slim.append({
            "callsign":     g.get("callsign"),
            "gridsquare":   g.get("gridsquare"),
            "distance":     g.get("distance"),
            "azimuth":      g.get("azimuth"),
            "modes":        g.get("modes"),
            "freq_khz":     freq.get("khz"),
            "dial_khz":     dial.get("khz"),
            "link_quality": pred.get("link_quality"),
            "url":          g.get("url"),
        })
    return jsonify({"ok": True, "mode": mode, "count": len(slim), "gateways": slim})


@bp.route("/api/winlink/connect", methods=["POST"])
def api_winlink_connect():
    """Start a Pat connect session. Forwards ?url=<alias-or-transport-url>.

    POST + custom-header gate: starting a connect keys the transmitter on the
    RF path, so it gets the same CSRF protection as /api/service (a GET here
    could be triggered by any <img src=…> a LAN browser happens to load).
    Pat's own endpoint stays GET; only the OASIS-facing surface changes.

    Longer timeout: a connect (esp. RF) can take a while. The live log streams
    over the browser's direct WebSocket to Pat, not through here.
    """
    import urllib.parse
    if request.headers.get("X-OASIS-Request") != "1":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    return _winlink_proxy("/api/connect", query=qs, timeout=120)


@bp.route("/api/winlink/log", methods=["GET"])
def api_winlink_log():
    """Tail the Pat journald log for the mail UI's session console.

    Pat's live-log WebSocket is same-origin only (gorilla's default origin check
    rejects this cross-origin page on :8083 → Pat :8082), so it never reaches the
    browser. journald is the reliable same-origin source (the same lines you see
    in `journalctl -u pat`). We tail only the `pat` unit — Direwolf's audio-level
    meters and raw AX.25 frame dumps are excluded so the console shows just Pat's
    session/B2F lines. Read-only; the only input is a clamped line count. Falls
    back gracefully (ok:false) so the client keeps whatever it already showed."""
    import subprocess
    import sys as _sys

    if _sys.platform != "linux":
        return jsonify({"ok": False, "supported": False, "lines": []})
    try:
        n = int(request.args.get("lines", 40))
    except (TypeError, ValueError):
        n = 40
    n = max(1, min(n, 300))

    # -o cat = message text only; Pat already prefixes its own timestamps.
    cmd = ["journalctl", "-u", "pat",
           "-n", str(n), "-o", "cat", "--no-pager"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "lines": []})
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "permission" in err.lower() or not err:
            err += " (journal not readable — add the OASIS user to the 'systemd-journal' group)"
        return jsonify({"ok": False, "error": err, "lines": []})
    return jsonify({"ok": True, "lines": r.stdout.splitlines()})


