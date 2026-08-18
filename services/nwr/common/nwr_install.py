"""Install NOAA Weather Radio support: multimon-ng, and nothing else.

Nothing to install on the Python side — the SAME parser and its tables live in
the repo (services/nwr/common/same.py and vendor/dsame3/defs.py), so there is no
wheel, no venv step, and no version floor.

rtl_fm comes from the rtl-sdr feature, which this one depends on rather than
reinstalling. rtl_power comes with the same tools; when it is absent, scanning
reports unavailable and listening is unaffected — a missing nicety must not gate
the feature.
"""
import os
import sys

_SUITE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "..")
sys.path.insert(0, os.path.normpath(_SUITE_ROOT))

from common.oasis_lib import _hr, _step, _ok, _info, _warn, _run, sudo_apt_cmd  # noqa: E402
from common import manifest as M  # noqa: E402

PACKAGES = ["multimon-ng"]


def removal_record(repo_root=None):
    """Teardown record. No units and no files of our own: the capture is an
    ad-hoc Flask subprocess and the config lives in configuration/nwr.json,
    which the generic config teardown handles. The apt package stays per the
    leave-apt policy."""
    return {"services": [], "files": []}


def _suite():
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VERSION_CODENAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def run(repo_root=None, online=None):
    _hr()
    _step(1, "Installing multimon-ng")
    pkgs = M.apt_packages("nwr", suite=_suite()) or PACKAGES
    r = _run(sudo_apt_cmd("apt-get", "install", "-y", *pkgs), check=False)
    if r.returncode != 0:
        _warn("apt could not install " + ", ".join(pkgs))
        _info("  Offline? The package ships in the bundle group 'nwr'.")
        return {"ok": False, "error": "multimon-ng not installed"}
    _ok("multimon-ng installed")

    _step(2, "Checking the receive chain")
    import shutil
    for binary, why in (("rtl_fm", "required - install the rtl-sdr feature"),
                        ("rtl_power", "optional - channel scan only"),
                        ("ffmpeg", "optional - live audio in the browser")):
        if shutil.which(binary):
            _ok(f"{binary} present")
        else:
            _warn(f"{binary} missing ({why})")

    _info("Assign an RTL-SDR to 'nwr' in Setup - Hardware, then open /server/nwr/")
    return {"ok": True}
