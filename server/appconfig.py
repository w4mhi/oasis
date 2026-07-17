"""
appconfig.py
------------
Shared runtime configuration for the OASIS server — the module-level state that
app.py and the route blueprints (server/routes/*, services/*/routes.py) all
need. Kept as plain module attributes (referenced `appconfig.X` at call time)
so tests can patch individual values without touching Flask app config.

Import-safe on its own: does the same sys.path bootstrap as app.py so the
shared `common` package resolves under every launcher (embedded Windows
Python, gunicorn --chdir server, tests inserting server/ on sys.path).
"""

import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_ROOT = os.path.abspath(os.path.join(SERVER_DIR, ".."))
for _path in (SUITE_ROOT, SERVER_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from common import config_paths

# Suite version (git-tracked, single source of truth). The dashboard reads it
# via /version.json; the server also surfaces it in /api/server-info so doctor.py
# and the setup page can report it.
VERSION_FILE = os.path.join(SUITE_ROOT, "version.json")

# Written by setup-oasis.py: the set of features the operator chose to install.
# The dashboard reads it (via /api/installed-services) to hide cards for
# services that were never installed. Absent file → show everything.
INSTALLED_SERVICES_FILE = config_paths.installed_services_json(SUITE_ROOT)

# Portable / "locked" profile. When OASIS_FEATURES is set (comma-separated
# feature keys, e.g. "fcc,forms,repeaterbook"), the suite runs as a read-mostly
# standalone-tools build straight off a USB stick: /api/installed-services
# reports exactly this list with locked=True (so the dashboard shows only these
# gated cards AND hides the "show not installed" reveal button), and the routes
# for the daemon-backed features left out are refused (see app.py's
# _portable_gate). Absent/empty → None → normal behaviour (read
# installed-services.json). run-portable.command sets this before launching.
_portable_env = os.environ.get("OASIS_FEATURES", "").strip()
PORTABLE_FEATURES = (
    sorted({f.strip() for f in _portable_env.split(",") if f.strip()})
    if _portable_env else None
)

# The port the server listens on. app.py's __main__ overwrites this via
# resolve_port() before serving (and exports OASIS_PORT before the gunicorn
# re-exec, so the re-imported module picks the same value up here).
PORT = int(os.environ.get("OASIS_PORT", "8083"))
