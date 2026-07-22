#!/usr/bin/env python3
"""
services/satellites/install-predict.py
--------------------------------------
Install the Python pass-prediction stack the Satellites page needs — Skyfield
(astronomy over the SGP4 propagator) plus its compiled numpy dependency, into
the OASIS server venv. Without these, services/satellites/predict.py can't be
imported, so /api/satellites/passes and /api/satellites/track return HTTP 500
and the page shows no passes with every card disabled (the roster still loads,
since it never imports predict).

These wheels are shared with the server (bundled from scripts/requirements.txt
into server/wheels/), but the base server setup installs only the server pypi
group — so the prediction stack is installed here, when the operator enables the
Satellites feature, rather than bloating every server's venv with numpy.

Offline-first: installs from the bundled wheels in server/wheels/ (no PyPI);
falls back to PyPI when the wheel dir is empty and there's internet. Idempotent —
pip no-ops packages already satisfied, safe to re-run. Reads the exact package
set from the manifest 'satellites' pypi group (single source of truth with the
wheel bundler). Reuses the same install machinery as setup-server so behaviour
never drifts.

When run as root (the privileged setup worker), the resulting site-packages are
root-owned but world-readable, so the server user imports them fine.

Usage:
  python3 services/satellites/install-predict.py
"""

import argparse
import os
import sys

# services/satellites/install-predict.py → repo root is three levels up.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
from common import server as S  # noqa: E402
from common.oasis_lib import _hr, _ok, _info, _warn  # noqa: E402

FEATURE = "satellites"

# The exact import services/satellites/predict.py performs — the real success
# signal for the prediction stack. numpy + sgp4 are named explicitly so a missing
# compiled dep fails here even though skyfield imports them lazily.
PREDICT_IMPORT = "import numpy, sgp4; from skyfield.api import EarthSatellite, load, wgs84"


def run():
    _hr()
    print("  ▶  Satellite pass-prediction stack (Skyfield + numpy)")
    _hr()

    venv_dir   = os.path.join(REPO_ROOT, ".venv")
    wheels_dir = os.path.join(REPO_ROOT, "server", "wheels")
    pip        = S._venv_bin(venv_dir, "pip")

    if not os.path.exists(S._venv_bin(venv_dir, "python")):
        _warn(f"No server venv at {venv_dir} — run scripts/setup-server.py first "
              "(the Satellites feature depends on the server). Nothing to do.")
        return 1

    specs = S._packages_from_manifest(FEATURE)
    if not specs:
        _warn("No 'satellites' packages in the manifest — nothing to install.")
        return 0

    online, banner = S.decide_source(wheels_dir)
    if online is None:
        # No bundled wheels and no internet: can't install now. Skip cleanly (the
        # normal offline bundle ships the wheels) rather than aborting the whole
        # Satellites setup — re-run with the bundle or online to finish.
        _warn("server/wheels/ is empty and no internet is reachable — can't fetch "
              "the prediction stack now. Re-run with the offline bundle (or online) "
              "later; the roster works meanwhile, pass prediction won't.")
        return 0
    S.print_source_banner(online, banner, wheels_dir)

    for spec in specs:
        S.install_one(pip, spec, online, wheels_dir)

    # Verify the EXACT import the server does (services/satellites/predict.py), NOT
    # a bare `import skyfield`: skyfield is pure-Python and imports numpy lazily, so
    # `import skyfield` passes even when the compiled numpy/sgp4 wheels failed to
    # install (wrong interpreter/ABI in the bundle). That false-pass would ship a
    # "successful" feature with a broken stack (/api/satellites/passes 500, roster
    # greyed). This exercises numpy + sgp4 through skyfield.api, so a missing
    # compiled dep is caught here.
    if not S._import_ok(venv_dir, PREDICT_IMPORT):
        # Bundled wheels didn't satisfy this interpreter. If we installed offline
        # and there's internet, retry from PyPI (pull matching wheels — what a
        # manual `pip install` does) and re-verify, rather than falsely succeeding.
        if online is False and S.has_internet():
            _warn("Prediction import failed from the bundled wheels — retrying from "
                  "PyPI for this interpreter.")
            for spec in specs:
                S.install_one(pip, spec, True, wheels_dir)
        if not S._import_ok(venv_dir, PREDICT_IMPORT):
            v = sys.version_info
            _warn("Pass prediction is NOT usable: `from skyfield.api import "
                  "EarthSatellite` fails — numpy/sgp4 are missing or ABI-mismatched "
                  f"for this Python {v.major}.{v.minor}. /api/satellites/passes will "
                  "500 and the roster stays greyed. Fix: re-run online, or bundle "
                  "wheels matching this interpreter. See the pip errors above.")
            return 1
    _ok("Prediction stack installed — /api/satellites/passes and /track are live.")
    _info("Restart the server to pick it up if it was already running.")
    return 0


def main():
    argparse.ArgumentParser(
        description="Install the satellite pass-prediction Python stack "
                    "(Skyfield + numpy) into the OASIS venv. Offline-first "
                    "(bundled wheels), idempotent.",
    ).parse_args()
    sys.exit(run())


if __name__ == "__main__":
    main()
