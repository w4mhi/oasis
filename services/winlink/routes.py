"""
Winlink route blueprint — the OASIS-styled mail UI's static files plus the
same-origin pass-through proxies to Pat's JSON API (port 8082). Extracted
verbatim from server/app.py in the blueprint split; URLs unchanged.
"""

import json
import os

from flask import Blueprint, Response, jsonify, request, send_from_directory

import appconfig
from common.api_shape import clamp_limit
from common.web_guard import require_oasis_request, require_oasis_request_for

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
# **This layer is the API contract boundary** (docs/api-contract.md §10). Pat is
# a third-party Go binary on 127.0.0.1:8082 whose payload shape we neither own
# nor can pin — read-state.js already copes with MID/Mid/mid and Unread/unread,
# which is the front-end telling us Pat's casing varies by version.
#
# So the migration here is deliberately BOUNDED: OASIS owns the ENVELOPE (ok,
# named container key, list bounds, error codes) and passes Pat's INNER objects
# through untouched. Renaming Pat's fields would need a live Pat to verify
# against, and guessing at them would break the mail client on a version we
# never tested. That inner-field work is recorded as §7 debt, not done blind.
#
# What this does fix is the part that was indefensible: several of these
# returned a BARE JSON ARRAY, which §1 forbids outright, and every error was
# Pat's own body passed through verbatim — so an OASIS error and a Pat error
# were indistinguishable to a caller.
WINLINK_PORT = 8082
WINLINK_BOXES = {"in", "out", "sent", "archive"}

# §4. A mailbox is small in practice; the bound exists so the response cannot
# become unbounded, not to paginate.
_MAILBOX_DEFAULT_LIMIT = 500
_MAILBOX_MAX_LIMIT = 2000


def _pat_json(path, *, method="GET", query="", data=None, headers=None,
              timeout=10):
    """(payload, error_response) — fetch and PARSE Pat's JSON.

    Exactly one of the two is None. `payload` is whatever Pat sent (a list, a
    dict, or None for an empty body — Pat answers some POSTs with no content).
    Callers build their own envelope around it; nothing here reaches the client
    unexamined, which is the whole point of §10.
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
    except urllib.error.HTTPError as e:
        # Pat is up and REFUSED. Keep its status (a 400 is genuinely the
        # caller's fault) but say plainly that the rejection came from Pat —
        # passing its body through verbatim made an OASIS error and a Pat error
        # the same thing on the wire.
        try:
            detail = e.read().decode("utf-8", "replace").strip()
        except Exception:
            detail = ""
        return None, (jsonify({
            "ok": False,
            "error": detail[:300] or f"Pat rejected the request ({e.code}).",
            "code": "PAT_REJECTED",
        }), e.code)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return None, (jsonify({
            "ok": False,
            "error": f"Pat (Winlink) unreachable ({reason}). Is the 'pat' "
                     f"service running on port {WINLINK_PORT}?",
            "code": "WINLINK_UNAVAILABLE",
        }), 503)
    except TimeoutError:
        return None, (jsonify({"ok": False, "error": "Pat (Winlink) timed out.",
                               "code": "WINLINK_TIMEOUT"}), 503)
    if not body.strip():
        return None, None                  # empty body is a valid Pat success
    try:
        return json.loads(body), None
    except ValueError:
        return None, (jsonify({
            "ok": False,
            "error": "Pat returned a body that is not JSON.",
            "code": "WINLINK_BAD_RESPONSE",
        }), 502)


def _pat_flag(payload, *name_variants):
    """Read a boolean from Pat's status regardless of casing.

    mail.html already did this client-side (`pick(s, "Connected", "connected")`)
    because Pat's field casing varies by version. Doing it here means every
    caller — including a model — gets one stable lower-case field, while the
    raw object still ships untouched for anything we haven't anticipated."""
    if not isinstance(payload, dict):
        return None
    for name in name_variants:
        if name in payload:
            return bool(payload[name])
    return None


@bp.route("/api/winlink/mailbox/<box>", methods=["GET"])
def api_winlink_mailbox_list(box):
    """List messages in a Pat mailbox (in / out / sent / archive).

    §1: this returned a BARE JSON ARRAY. Message objects pass through as Pat
    emits them (see the module note); only the container is ours."""
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox",
                        "code": "UNKNOWN_MAILBOX"}), 400
    payload, error = _pat_json(f"/api/mailbox/{box}")
    if error:
        return error
    messages = payload if isinstance(payload, list) else []
    limit = clamp_limit(request.args.get("limit"), _MAILBOX_DEFAULT_LIMIT,
                        _MAILBOX_MAX_LIMIT)
    shown = messages[:limit]
    return jsonify({
        "ok": True,
        "box": box,
        "messages": shown,
        "total": len(messages),
        "count": len(shown),
        "truncated": len(messages) > len(shown),
        "limit": limit,
    })


@bp.route("/api/winlink/mailbox/<box>/<mid>", methods=["GET", "DELETE"])
@require_oasis_request_for("DELETE")
def api_winlink_message(box, mid):
    """Read or delete a single Pat message."""
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox",
                        "code": "UNKNOWN_MAILBOX"}), 400
    payload, error = _pat_json(f"/api/mailbox/{box}/{mid}", method=request.method)
    if error:
        return error
    if request.method == "DELETE":
        # §8: deleting an already-deleted message is still "it is gone".
        return jsonify({"ok": True, "box": box, "mid": mid, "deleted": True})
    return jsonify({"ok": True, "box": box, "mid": mid,
                    "message": payload if isinstance(payload, dict) else None})


@bp.route("/api/winlink/mailbox/<box>/<mid>/<path:attachment>", methods=["GET"])
def api_winlink_attachment(box, mid, attachment):
    """Stream a message attachment from Pat, passing Pat's Content-Type through.

    Unlike the JSON proxies above, attachments are arbitrary bytes (form XML,
    FormData.txt, photos, PDFs), so we forward Pat's Content-Type rather than
    forcing application/json. ?download=1 adds a Content-Disposition so the
    browser saves rather than renders inline.
    """
    if box not in WINLINK_BOXES:
        return jsonify({"ok": False, "error": "unknown mailbox",
                        "code": "UNKNOWN_MAILBOX"}), 400
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
        # The attachment BODY is arbitrary bytes, but an error is not — it gets
        # the same envelope as everything else instead of Pat's raw body.
        try:
            detail = e.read().decode("utf-8", "replace").strip()
        except Exception:
            detail = ""
        return jsonify({"ok": False, "code": "PAT_REJECTED",
                        "error": detail[:300] or "attachment not available."}), e.code
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False, "code": "WINLINK_UNAVAILABLE",
                        "error": f"Pat (Winlink) unreachable ({reason})."}), 503
    except TimeoutError:
        return jsonify({"ok": False, "code": "WINLINK_TIMEOUT",
                        "error": "Pat (Winlink) timed out."}), 503


@bp.route("/api/winlink/mailbox/out", methods=["POST"])
@require_oasis_request
def api_winlink_compose():
    """Queue a composed message into Pat's outbox.

    CSRF-guarded: this queues OUTBOUND RADIO EMAIL. It was unguarded until
    2026-08-08 and the coverage sweep gave it a false pass — its 45-line window
    happened to catch a NEIGHBOURING route's inline header check. A
    multipart/form-data POST is a "simple" request needing no preflight, so any
    LAN page could have queued traffic. mail.html's api() already sends the
    header on every non-GET, so this is server-side catch-up.

    Forwards the form body (to / cc / subject / body) verbatim with its
    Content-Type so Pat parses it exactly as its own UI would.
    """
    headers = {}
    ctype = request.headers.get("Content-Type")
    if ctype:
        headers["Content-Type"] = ctype
    _payload, error = _pat_json("/api/mailbox/out", method="POST",
                                data=request.get_data(), headers=headers)
    if error:
        return error
    return jsonify({"ok": True, "queued": True})


@bp.route("/api/winlink/status", methods=["GET"])
def api_winlink_status():
    """Pat's connection status, with the two booleans every consumer wants
    lifted out of Pat's varying casing (see _pat_flag). The raw object still
    ships under `status` for anything not anticipated here."""
    payload, error = _pat_json("/api/status")
    if error:
        return error
    return jsonify({
        "ok": True,
        "connected": _pat_flag(payload, "Connected", "connected"),
        "dialing": _pat_flag(payload, "Dialing", "dialing"),
        "status": payload if isinstance(payload, dict) else None,
    })


@bp.route("/api/winlink/aliases", methods=["GET"])
def api_winlink_aliases():
    """Pat's configured connect aliases (available transports).

    §1/§4: Pat answers with either a list or an object depending on version —
    mail.html had to branch on which. Normalised to a list of names here, once."""
    payload, error = _pat_json("/api/connect_aliases")
    if error:
        return error
    if isinstance(payload, dict):
        aliases = sorted(str(k) for k in payload)
    elif isinstance(payload, list):
        aliases = [str(a) for a in payload]
    else:
        aliases = []
    return jsonify({"ok": True, "aliases": aliases, "count": len(aliases)})


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
        return jsonify({"ok": False, "error": "forbidden",
                        "code": "FORBIDDEN"}), 403
    _payload, error = _pat_json("/api/disconnect")
    if error:
        return error
    return jsonify({"ok": True, "disconnected": True})


# Transport modes Pat's rmslist accepts (verified against a live Pat). The
# no-mode call hangs, so a mode is required and allowlisted.
RMS_MODES = {"packet", "ardop", "vara", "varahf", "varafm", "pactor"}


@bp.route("/api/winlink/rmslist", methods=["GET"])
def api_winlink_rmslist():
    """Slim proxy of Pat's RMS gateway list for one transport mode.

    Pat's /api/rmslist?mode=X returns the gateways PLUS a per-gateway VOACAP
    propagation report whose raw text is ~95% of the (multi-MB) payload. We
    strip that — keeping the link_quality score — so the browser caches a lean
    snapshot (essential on the Pi). The data is sourced from Pat's web API,
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
        try:
            detail = e.read().decode("utf-8", "replace").strip()
        except Exception:
            detail = ""
        return jsonify({"ok": False, "code": "PAT_REJECTED",
                        "error": detail[:300] or "Pat rejected the request."}), e.code
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        return jsonify({"ok": False, "code": "WINLINK_UNAVAILABLE",
                        "error": f"Pat (Winlink) unreachable ({reason}). The "
                                 "gateway list needs Pat running + connectivity."}), 503
    except TimeoutError:
        return jsonify({"ok": False, "code": "WINLINK_TIMEOUT",
                        "error": "Pat timed out building the gateway list."}), 503

    try:
        gateways = _json.loads(raw)
    except ValueError:
        return jsonify({"ok": False, "error": "Pat returned malformed data.",
                        "code": "WINLINK_BAD_RESPONSE"}), 502

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
    return jsonify({"ok": True, "mode": mode, "gateways": slim,
                    "total": len(slim), "count": len(slim), "truncated": False})


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
        return jsonify({"ok": False, "error": "forbidden",
                        "code": "FORBIDDEN"}), 403
    qs = urllib.parse.urlencode({k: v for k, v in request.args.items()})
    payload, error = _pat_json("/api/connect", query=qs, timeout=120)
    if error:
        return error
    return jsonify({"ok": True, "started": True,
                    "result": payload if isinstance(payload, dict) else None})


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
        # §2: "this host has no journald" is an ANSWER, not a failed request.
        return jsonify({"ok": True, "supported": False, "read": False,
                        "lines": [], "count": 0, "reason": "not-linux",
                        "detail": None})
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
        return jsonify({"ok": True, "supported": True, "read": False,
                        "lines": [], "count": 0, "reason": "probe-error",
                        "detail": str(exc)[:300]})
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "permission" in err.lower() or not err:
            err += " (journal not readable — add the OASIS user to the 'systemd-journal' group)"
        return jsonify({"ok": True, "supported": True, "read": False,
                        "lines": [], "count": 0, "reason": "journal-unreadable",
                        "detail": err[:300]})
    lines = r.stdout.splitlines()
    # `read` (not `ok`) is the flag to branch on: an empty `lines` from a
    # SUCCESSFUL tail means Pat has simply been quiet, which is not the same
    # fact as "we could not read the journal".
    return jsonify({"ok": True, "supported": True, "read": True,
                    "lines": lines, "count": len(lines),
                    "reason": None, "detail": None})


