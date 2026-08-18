"""Install NOAA Weather Radio support: multimon-ng, the always-on watch unit,
and nothing else.

Nothing to install on the Python side — the SAME parser and its tables live in
the repo (services/nwr/common/same.py and vendor/dsame3/defs.py), so there is no
wheel, no venv step, and no version floor.

rtl_fm comes from the rtl-sdr feature, which this one depends on rather than
reinstalling. rtl_power comes with the same tools; when it is absent, scanning
reports unavailable and listening is unaffected — a missing nicety must not gate
the feature.
"""
import os
import subprocess
import sys

_SUITE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "..")
sys.path.insert(0, os.path.normpath(_SUITE_ROOT))

from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run, sudo_apt_cmd  # noqa: E402
from common import manifest as M  # noqa: E402
# SERVICE is canonical in services/nwr/common/daemon.py — imported, not redefined.
from services.nwr.common.daemon import SERVICE  # noqa: E402

PACKAGES = ["multimon-ng"]

UNIT_PATH = f"/etc/systemd/system/{SERVICE}.service"


def unit_text(venv_py, entry):
    """The systemd unit for the always-on watch.

    Restart=on-failure and not `always`: a daemon that cannot claim its dongle
    should stop and say so in the console, not spin forever hiding the reason.

    No PartOf=. adsb-api is bound to its decoder unit; the watch has no separate
    decoder to follow -- rtl_fm and multimon-ng are its own children.
    """
    return f"""[Unit]
Description=OASIS NOAA Weather Radio watch (SAME/EAS)
After=network.target

[Service]
Type=simple
ExecStart={venv_py} {entry} --serve
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""


def write_unit(venv_py, entry):
    """Write the unit via `sudo tee`, following adsb's _write_api_unit pattern
    (services/adsb/common/adsb.py:359-388) exactly: `sudo tee` + `daemon-reload`,
    and stop there. A missing or non-interactive sudo makes `tee` exit non-zero,
    and _fail() stops the installer right there.

    Deliberately does NOT `systemctl enable`. common/hardware.py's
    boot_start_plan() decides what starts at boot from the persisted device
    assignments, and skips services with no dongle assigned -- enabling the
    unit here would bypass that and start the watch on every boot whether or
    not an operator ever gave it a dongle. Assigning a dongle to nwr is what
    puts the unit in that plan, and the plan is what starts it."""
    proc = subprocess.Popen(
        ["sudo", "tee", UNIT_PATH],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    proc.communicate(unit_text(venv_py, entry).encode())
    if proc.returncode != 0:
        _fail(f"Could not write {UNIT_PATH}")
    _ok(f"Service file: {UNIT_PATH}")
    _run(["sudo", "systemctl", "daemon-reload"])


def removal_record(repo_root=None):
    """Teardown: the watch unit. v1 returned nothing because the capture was an
    ad-hoc Flask subprocess; leaving it empty now would strand a running daemon
    holding a dongle after the feature is uninstalled."""
    return {"services": [SERVICE], "files": [UNIT_PATH]}


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

    _step(2, "Installing the watch service")
    venv_py = os.path.join(repo_root, ".venv", "bin", "python3")
    entry = os.path.join(repo_root, "services", "nwr", "install.py")
    write_unit(venv_py, entry)

    _step(3, "Checking the receive chain")
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
