#!/usr/bin/env python3
"""
app.py
------
Off-grid Flask web server for OASIS - Off-grid Amateur Station Integrated Suite.

Serves the main index.html and all suite static files at the root, and wires
up the route blueprints:

  services/<name>/routes.py   service-owned APIs (adsb, aprs, winlink,
                              fcc_database, map)
  server/routes/*.py          server-core domains (setup, hardware, wifi,
                              service_control, health, system, files)
  server/appconfig.py         shared runtime config (suite root, portable
                              profile, port)

This module keeps only app creation, the app-level hooks (JSON error handler,
portable-mode gate, theme injection), the two root routes, and the launcher.

Designed to run on a Raspberry Pi with no internet connection.

Run (development):   python3 app.py
Run (recommended):   see docs/SETUP.md for the gunicorn + systemd setup.
"""

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

from flask import Flask, jsonify, request, send_file

# The Windows embeddable Python (shipped in _runtime/windows/) uses a pythonXX._pth
# file, which builds sys.path solely from that file and suppresses the automatic
# script-directory entry that normal CPython adds. Insert both the suite root and
# the server directory so the shared `common` package and server-local modules
# resolve under every launcher (embedded or system Python).
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_ROOT = os.path.abspath(os.path.join(SERVER_DIR, ".."))
for path in (SUITE_ROOT, SERVER_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import appconfig
from common import config_paths

# In portable mode, refuse the URL prefixes for daemon-backed features that were
# left out of appconfig.PORTABLE_FEATURES — both the JSON proxies and their pages.
# Keeps a locked USB build from exposing a Winlink/APRS surface whose backing
# services (pat, direwolf, graywolf-api) are not running. Feature key → prefixes.
_PORTABLE_BLOCK = {
    "winlink":  ("/api/winlink", "/server/winlink"),
    # The live map moved to /server/map; keep it gated with graywolf so a locked
    # portable build without the APRS backend still can't surface the map page.
    "graywolf": ("/api/aprs", "/server/map"),
}

# Serve the suite root as the static folder so all existing relative links
# in index.html (antenna-calc.html, ics-205/, etc.) keep working as-is.
app = Flask(__name__, static_folder=SUITE_ROOT, static_url_path="")


# Stable §3 slugs for errors that no route authored — raised by Flask/werkzeug
# itself. Anything unmapped becomes HTTP_<status>, which is still stable.
_HTTP_CODES = {
    400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
    404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE",
    500: "INTERNAL_ERROR", 502: "BAD_GATEWAY", 503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT", 507: "INSUFFICIENT_STORAGE",
}


@app.errorhandler(Exception)
def _api_json_error_handler(exc):
    """Return JSON (with the real error text) for any unhandled exception on an
    /api/* route, instead of gunicorn/werkzeug's opaque HTML 500 page.

    Without this, a server-side exception under gunicorn (the Pi's production
    WSGI server) is rendered as its default '<html>...Internal Server Error'
    page. The Setup Orchestrator's fetch() then calls response.json() on that
    HTML and fails with the useless "Unexpected token '<', "<html> ..." error,
    hiding the actual cause. Non-/api routes keep Flask's normal HTML handling.
    """
    from werkzeug.exceptions import HTTPException

    is_http = isinstance(exc, HTTPException)
    status = exc.code if is_http else 500
    if request.path.startswith("/api/"):
        if is_http:
            # Deliberate HTTP errors (abort(403)/404/…) carry a safe description.
            message = str(exc)
        else:
            # Unexpected server error: log the real cause (with traceback) server-
            # side, but return a generic message so raw internals (filesystem paths,
            # values) don't leak to LAN clients. Routes that want to surface a
            # specific cause already catch and return it themselves.
            app.logger.exception("Unhandled exception on %s", request.path)
            message = "Internal server error"
        # §3: every error carries a stable machine-readable slug, including the
        # ones NO route wrote — a 404 from send_from_directory, an abort(403)
        # from a guard. Those reach a client as an error like any other, and a
        # caller that must branch on prose has no contract at all.
        return jsonify({"ok": False, "error": message, "status": status,
                        "code": _HTTP_CODES.get(status, f"HTTP_{status}")}), status
    # Non-API routes: preserve Flask's default behavior (HTTPExceptions are
    # valid responses; re-raise anything else so the framework renders its 500).
    if is_http:
        return exc
    raise exc


# CORS is intentionally NOT applied globally.  All HTML is served from this
# same Flask instance (same origin), so cross-origin headers are unnecessary.
# Individual routes that legitimately need cross-origin access add them below.

# ── Shared light/dark theme toggle ────────────────────────────────────────────
# Inject common/js/theme.js just before </head> on every owned HTML page, so the
# sun/moon toggle (and the no-flash theme apply) appears everywhere without
# editing each page. Pages that manage their own theming are skipped:
#   • the dashboard kiosk (/oasis-dashboard/)   • the traffic map (/maps/traffic/)
#   • the graywolf-handbook (/static/graywolf-handbook/)
# theme.js is idempotent — it leaves a page's own toggle button (e.g. the
# dashboard's) alone and only adds the floating one when none exists. The traffic
# map is always dark and owns its theming; skipping it also keeps the floating
# toggle from landing on top of the topbar HOME button.
_THEME_SKIP_PREFIXES = ("/oasis-dashboard/", "/maps/traffic/", "/server/satellites/", "/static/graywolf-handbook/")
_THEME_SNIPPET = '<script src="/common/js/theme.js"></script>'

@app.before_request
def _portable_gate():
    """In portable mode, 404 the Winlink/APRS surfaces (API + pages) for any
    daemon-backed feature not in PORTABLE_FEATURES. No-op when unlocked."""
    if appconfig.PORTABLE_FEATURES is None:
        return None
    allowed = set(appconfig.PORTABLE_FEATURES)
    path = request.path or "/"
    for feat, prefixes in _PORTABLE_BLOCK.items():
        if feat not in allowed and any(path.startswith(p) for p in prefixes):
            return jsonify({"ok": False, "error": "disabled in portable mode"}), 404
    return None


@app.after_request
def _no_store_api(resp):
    # Dynamic state (the install ledger, health, live feeds) must never be served
    # from the browser cache. /api/* responses carried no cache headers, so a
    # browser could heuristically replay a stale /api/installed-services — making
    # the setup page show already-removed features (e.g. graywolf) as still
    # installed after a reboot. no-store forces a fresh fetch every time.
    if (request.path or "/").startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# App assets the browser must REVALIDATE, so a cached copy can never be paired
# with a newer server. `no-cache` still allows caching — it just forces an
# If-None-Match round trip, which on a LAN answers 304 and costs nothing.
#
# This is not hypothetical. After /api/adsb/recent stopped sending the epoch `ts`
# field, a browser holding a cached pre-migration common/js/traffic-list.js read
# `ts` as undefined, produced new Date(NaN).toISOString(), and threw — blanking
# the entire traffic list, APRS stations included, on a Pi whose server files were
# all correct. There is no in-band update mechanism for a deployed station, so the
# browser cache is the one place version skew can hide.
_REVALIDATE_PREFIXES = ("/common/js/", "/common/css/", "/maps/mapengine/")


@app.after_request
def _revalidate_app_assets(resp):
    path = request.path or "/"
    if path.startswith(_REVALIDATE_PREFIXES) or resp.mimetype == "text/html":
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


@app.after_request
def _inject_theme_toggle(resp):
    try:
        if resp.mimetype != "text/html":
            return resp
        path = request.path or "/"
        if any(path.startswith(p) for p in _THEME_SKIP_PREFIXES):
            return resp
        if resp.direct_passthrough:
            resp.direct_passthrough = False
        html = resp.get_data(as_text=True)
        if "</head>" not in html or "/common/js/theme.js" in html:
            return resp
        resp.set_data(html.replace("</head>", _THEME_SNIPPET + "</head>", 1))
    except Exception:
        # The toggle is a nicety — never let injection break a page.
        pass
    return resp


# ── Route blueprints ──────────────────────────────────────────────────────────
# Service-owned routes live with their service (services/<name>/routes.py);
# server-core domains live in server/routes/. Extracted from this file in the
# blueprint split — every URL is unchanged (blueprints keep full literal paths,
# no url_prefix, so each route stays greppable by its URL).
from services.adsb.routes import bp as _adsb_bp
from services.aprs.routes import bp as _aprs_bp
from services.winlink.routes import bp as _winlink_bp
from services.fcc_database.routes import bp as _fcc_bp
from maps.traffic.routes import bp as _map_bp
from maps.routes import bp as _mapdata_bp
from routes.files import bp as _files_bp
from routes.service_control import bp as _service_bp
from routes.hardware import bp as _hardware_bp
from routes.wifi import bp as _wifi_bp
from routes.system import bp as _system_bp
from routes.health import bp as _health_bp
from routes.setup import bp as _setup_bp
from routes.diagnostics import bp as _diag_bp
from routes.aprs_freq import bp as _aprs_freq_bp
from routes.horizon import bp as _horizon_bp
from routes.speech import bp as _speech_bp
from routes.refresh import bp as _refresh_bp
from services.satellites.routes import bp as _sat_bp

app.register_blueprint(_adsb_bp)
app.register_blueprint(_aprs_bp)
app.register_blueprint(_winlink_bp)
app.register_blueprint(_fcc_bp)
app.register_blueprint(_map_bp)
app.register_blueprint(_mapdata_bp)
app.register_blueprint(_files_bp)
app.register_blueprint(_service_bp)
app.register_blueprint(_health_bp)
app.register_blueprint(_hardware_bp)
app.register_blueprint(_wifi_bp)
app.register_blueprint(_system_bp)
app.register_blueprint(_setup_bp)
app.register_blueprint(_diag_bp)
app.register_blueprint(_aprs_freq_bp)
app.register_blueprint(_horizon_bp)
app.register_blueprint(_sat_bp)
app.register_blueprint(_speech_bp)
app.register_blueprint(_refresh_bp)


# Self-heal the installed-services ledger on boot: a teardown interrupted after
# its artifacts were removed but before the ledger drop landed (power cut, lost
# worker result) would otherwise leave a feature reading as installed and block a
# clean reinstall. reconcile() is Linux-only, conservative (drops only on
# positive evidence of absence), and never raises — but guard it anyway so a
# ledger hiccup can never stop the server booting.
try:
    from common import installed_services as _installed_services
    _reconciled = _installed_services.reconcile(SUITE_ROOT)
    if _reconciled:
        app.logger.warning(
            "installed-services ledger reconciled on boot; dropped stale features: %s",
            ", ".join(_reconciled),
        )
except Exception:
    app.logger.exception("installed-services reconcile failed on boot (ignored)")


@app.route("/")
def index():
    """Smart home: JS reads localStorage and redirects to the dashboard kiosk or index.html."""
    return '''<!doctype html><meta charset="utf-8">
<script>
// Legacy "7inch" maps to 800x480; any dashboard resolution routes to the kiosk.
(function () {
  var RES = { "7inch": "800x480", "800x480": "800x480", "1920x1200": "1920x1200" };
  var res = RES[localStorage.getItem("oasis_layout")];
  window.location.replace(res ? "/oasis-dashboard/dashboard.html?res=" + res : "/index.html");
})();
</script>
<noscript><meta http-equiv="refresh" content="0;url=/index.html"></noscript>''', 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route("/station.json")
def serve_station_json():
    """Serve the operator's station profile, now stored under configuration/."""
    path = config_paths.station_json(SUITE_ROOT)
    if not os.path.exists(path):
        return jsonify({}), 404
    return send_file(path, mimetype="application/json")


# Minimal built-in default so the hazard pills work even if the operator deleted
# configuration/hazards.json — the shipped file is the real source of truth.
_HAZARDS_FALLBACK = {"hazards": [
    {"key": "fire", "label": "Fire", "emoji": "\U0001F525", "color": "#ff5c33",
     "symbols": [["/", ":"]], "name_match": ["WILDFIRE"]},
]}


@app.route("/hazards.json")
def serve_hazards_json():
    """APRS natural-hazard categories (dashboard hazard pills + map). Served from
    configuration/hazards.json — operator-editable — with a built-in fallback."""
    path = config_paths.hazards_json(SUITE_ROOT)
    if os.path.exists(path):
        return send_file(path, mimetype="application/json")
    return jsonify(_HAZARDS_FALLBACK)


PORT = appconfig.PORT

def find_free_port(start=8083, end=8093):
    """Return the first TCP port in [start, end] not already in use."""
    for p in range(start, end + 1):
        if _port_bindable(p):
            return p
    raise RuntimeError(f"No free port found between {start} and {end}")


def _port_bindable(port, host=""):
    """True if `port` can be bound the way gunicorn binds it (SO_REUSEADDR).

    Mirroring gunicorn's bind semantics matters on restart: the previous
    instance's socket is often only in TIME_WAIT, which a plain bind() reports
    as "in use" even though gunicorn (SO_REUSEADDR) would bind it fine. Probing
    with SO_REUSEADDR keeps this check honest and avoids drifting to 8084.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def resolve_port(preferred=8083, wait=15.0, poll=0.5):
    """Pick the port to serve on, deterministically across restarts.

    Honors OASIS_PORT as an explicit override. Otherwise prefers `preferred`
    (8083, the canonical OASIS port the dashboard expects). On a service
    restart where the old instance is still releasing the port, waits up to
    `wait` seconds for it to free rather than drifting to the next port —
    drift strands the dashboard (every service shows red). Falls back to the
    next free port only if the preferred one never frees, so startup never
    hard-fails.
    """
    env = os.environ.get("OASIS_PORT")
    if env:
        try:
            preferred = int(env)
        except ValueError:
            pass

    deadline = time.monotonic() + wait
    while True:
        if _port_bindable(preferred):
            return preferred
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    return find_free_port(preferred)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OASIS Flask server")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a web browser on startup (useful for headless Pi deployments).",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Force the Flask development server even when gunicorn is installed.",
    )
    args = parser.parse_args()

    PORT = resolve_port()
    appconfig.PORT = PORT                  # blueprints report the live port (dev-server path)
    os.environ["OASIS_PORT"] = str(PORT)   # so the gunicorn-loaded app reports this port
    url = f"http://localhost:{PORT}/"

    # Prefer the gunicorn production server when it's installed — re-exec into it.
    # gunicorn imports this module as 'app', so __main__ never runs under gunicorn
    # (no recursion). Falls back to the Flask dev server if gunicorn is absent or
    # --dev is given. This is why a plain `python app.py` now serves via gunicorn.
    # gunicorn is POSIX-only (needs fcntl / os.fork), so Windows always uses the
    # Flask dev server regardless of what's installed.
    if not args.dev and sys.platform != "win32":
        try:
            import gunicorn  # noqa: F401
            _have_gunicorn = True
        except ImportError:
            _have_gunicorn = False
        if _have_gunicorn:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            print(f"\n  OASIS (gunicorn) — {url}\n")
            # --workers 1: the Setup Orchestrator's plan/job state (_setup_plans /
            # _setup_jobs in routes/setup.py) lives in plain in-process dicts,
            # which multiple gunicorn workers (separate OS processes, no shared
            # memory) can't see across each other — causing intermittent 404
            # "unknown planId". Setup work already runs in a background thread
            # that doesn't block the request, so a single worker costs nothing.
            # --threads: /api/diagnostics makes in-process self-HTTP calls; a
            # threaded worker serves them concurrently (a sync single worker would
            # deadlock/time them out). Mirrors start-oasis.py + start-server.sh.
            os.execv(sys.executable, [
                sys.executable, "-m", "gunicorn",
                "--chdir", app_dir,
                "--bind", f"0.0.0.0:{PORT}",
                "--workers", "1", "--threads", "4",
                "--access-logfile", "-",
                "app:app",
            ])
            # os.execv replaces this process; nothing below runs on success.

    print(f"\n  OASIS (dev server) — {url}\n")
    if not args.no_browser:
        # Open the explicit desktop page, not the bare "/" smart-redirect: "/"
        # honors a stored localStorage oasis_layout (stamped by the dashboard kiosk
        # page) and would hijack the auto-open back to the dashboard forever. index.html
        # also runs its self-healing hatch to clear a stale stamp. Matches
        # run-portable.sh and scripts/enable-autostart-pi.py.
        threading.Timer(1.2, lambda: webbrowser.open(url + "index.html")).start()
    # host=0.0.0.0 so other devices on the off-grid network can reach it.
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
