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

from common.oasis_lib import (_hr, _step, _ok, _info, _warn, _fail, _run,  # noqa: E402
                              dpkg_installed_version, sudo_apt_cmd)
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


def missing_packages(pkgs):
    """Which of *pkgs* dpkg does not have installed, at ANY version.

    Presence, not a version floor, is the right test for this feature: it has
    no wheel, no venv step and no version floor (see the module docstring), and
    every multimon-ng Debian has shipped for years decodes SAME the same way.
    The stronger test would be a floor like rtl-sdr's, and a floor we do not
    need buys nothing while costing an apt run — the very apt run that failed
    on raspad, where multimon-ng 1.3.1+dfsg-1+b1 was already on PATH. The
    project's rule is version-aware, idempotent, never downgrade: an install
    that is already satisfied has nothing to do.

    dpkg-query does report a version for a package removed but not purged, so
    a config-files-only ghost would skip the apt run. That is why step 3 asks
    PATH for multimon-ng itself rather than trusting this answer.
    """
    return [p for p in pkgs if not dpkg_installed_version(p)]


def apt_failure_hint(stderr):
    """The one line worth adding to what apt already said, or None.

    A held lock, a fetch that 404s and a package the index does not know are
    three different problems with three different fixes, and the operator has
    to be told which one they have. The old code printed a guess about being
    offline for all of them: on raspad it was the lock, the box was online,
    the bundle group existed, and the guess sent the investigation down the
    wrong path for its entire length. So each hint is only offered when apt's
    own words support it, and no words means no hint.
    """
    low = (stderr or "").lower()
    if "lock" in low and ("could not get lock" in low
                          or "unable to acquire" in low
                          or "another process" in low
                          or "frontend lock" in low):
        return ("Another apt held the lock — apt-daily.timer, or an operator's own "
                "apt. Nothing was installed; run the install again.")
    if "unable to locate package" in low or "no installation candidate" in low:
        return ("The package index does not know that name. Run "
                "`sudo apt-get update`; the package ships in the bundle group 'nwr'.")
    if "failed to fetch" in low or "404" in low or "temporary failure in name resolution" in low:
        return "Offline, or the mirror is unreachable. The package ships in the bundle group 'nwr'."
    return None


def run(repo_root=None, online=None):
    _hr()
    _step(1, "Installing multimon-ng")
    pkgs = M.apt_packages("nwr", suite=_suite()) or PACKAGES
    missing = missing_packages(pkgs)
    if not missing:
        for p in pkgs:
            _ok(f"{p} already installed ({dpkg_installed_version(p)}) — nothing to do")
    else:
        # stderr is captured because _run() does not capture anything by
        # default, and apt's own words are the whole point of the failure
        # branch. stdout is left streaming so the install still shows progress.
        r = _run(sudo_apt_cmd("apt-get", "install", "-y", *missing),
                 check=False, stderr=subprocess.PIPE, text=True)
        if r.returncode != 0:
            _warn("apt could not install " + ", ".join(missing))
            hint = apt_failure_hint(r.stderr)
            if hint:
                _info("  " + hint)
            # Printed LAST on purpose: setup_registry takes the final line of
            # the script's output as reason_text, so what the operator reads in
            # the browser is apt's message and not ours.
            said = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
            for line in said[-4:]:
                _info(line[:200])
            return {"ok": False,
                    "error": said[-1][:200] if said else "multimon-ng not installed"}
        _ok("multimon-ng installed")

    _step(2, "Installing the watch service")
    venv_py = os.path.join(repo_root, ".venv", "bin", "python3")
    entry = os.path.join(repo_root, "services", "nwr", "install.py")
    write_unit(venv_py, entry)

    _step(3, "Checking the receive chain")
    import shutil
    for binary, why in (("multimon-ng", "required - the SAME decoder itself"),
                        ("rtl_fm", "required - install the rtl-sdr feature"),
                        ("rtl_power", "optional - channel scan only"),
                        ("ffmpeg", "optional - live audio in the browser")):
        if shutil.which(binary):
            _ok(f"{binary} present")
        else:
            _warn(f"{binary} missing ({why})")

    _info("Assign an RTL-SDR to 'nwr' in Setup - Hardware, then open /server/nwr/")
    return {"ok": True}
