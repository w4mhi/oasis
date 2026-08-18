#!/usr/bin/env python3
"""
enable-service-controls.py
--------------------------
Grant the OASIS server's user permission to start / stop / restart the OASIS
services from the dashboard power buttons — WITHOUT any password ever touching
the web layer.

It installs a narrow, validated `sudoers` rule (/etc/sudoers.d/oasis-service-
controls) that allows ONLY `systemctl start|stop|restart|enable|disable <unit>.service` for the
known OASIS units below, plus one fixed read-only `tcpdump -ni lo … udp port 7355`
for the dashboard's APRS SDR feed flow meter (/api/health/feed-flow), plus a
zero-arg `reboot` for the setup wizard's Reboot button (/api/setup/reboot), and
nothing else. The endpoints then run `sudo -n …`; the OS authorizes the exact
command, so OASIS never handles a credential.

Notably absent from the list: the `oasis` service itself — stopping the web
server would kill the dashboard with no way to bring it back from the browser.

It also adds the OASIS user to the `systemd-journal` group so the server can
read the Pat + Direwolf logs behind the Winlink mail client's session console
(/api/winlink/log). That's a plain group membership (no sudoers), left in place
on --disable (it grants nothing but log-read).

Opt-in and reversible. Run as your normal user (it asks for sudo once to write
the file); it refuses to run on non-Linux.

Usage:
  python3 scripts/enable-service-controls.py            # grant (current user)
  python3 scripts/enable-service-controls.py --user pi  # grant for a specific user
  python3 scripts/enable-service-controls.py --check    # report status
  python3 scripts/enable-service-controls.py --disable  # remove the permission

Requires: Linux, systemd, sudo.
"""

import argparse
import getpass
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run

# Must match server/routes/service_control.py's _CONTROLLABLE_SERVICES
# (everything OASIS-managed except the web server itself). Pinned by a drift
# test — a unit granted here but missing there is refused by /api/service and
# skipped by the guardian's STOP ALL, and the reverse is a silent no-op button.
#
# APPEND-ONLY: new units go on the END. PROBE_UNIT below depends on it.
#
# oasis-nwr is here because the conflict engine now starts it: assigning a
# dongle to nwr puts the unit in HW.boot_start_plan(), and both the boot
# reconciler and the assignment console reach it through `sudo -n systemctl`.
# _systemctl_seq swallows failures, so without this grant the console cell
# would sit on "assigned, stopped" and the boot start would do nothing.
UNITS   = ["graywolf", "graywolf-api", "pat", "pat-direwolf", "kiwix", "webssh",
           "aprs-sdr-feed", "openwebrx", "dump1090-fa", "adsb-api", "oasis-nwr"]
# enable/disable included so boot-state-tracking units (e.g. OpenWebRX: enable on
# start, disable on stop) work; the dashboard still only exposes start/stop/restart.
ACTIONS = ["start", "stop", "restart", "enable", "disable"]
SUDOERS_PATH = "/etc/sudoers.d/oasis-service-controls"

# The unit any "is the installed grant CURRENT?" probe must ask about.
#
# The grant is one rule covering every unit, so ANY single unit proves a grant
# exists — but not that it was written from THIS list. A box upgraded in place
# keeps the sudoers file it was granted with, and every unit that predates the
# upgrade still answers yes, so a probe on an old unit reports healthy while the
# newly-added one is not granted at all. Asking about the newest entry is what
# makes a stale file distinguishable from a current one.
PROBE_UNIT = UNITS[-1]


def grant_is_current(run=None, which=None):
    """True when sudo will run PROBE_UNIT's commands for this user without a
    password — i.e. the installed grant was written from the CURRENT unit list.

    Capability, not artifact. os.path.exists(SUDOERS_PATH) answers neither
    question honestly: /etc/sudoers.d is 0750 root:root on Debian and Pi OS, so
    a non-root caller cannot tell "absent" from "not allowed to look" (measured
    on pi5draws — see server/routes/setup.py::_service_controls_granted, which
    asks sudo the same way); and even when it CAN see the file, presence says
    nothing about which units are inside it.

    `sudo -n -l <cmd>` is a policy lookup, not an execution: exit 0 means
    permitted, non-zero means not, and -n guarantees it never prompts or hangs.
    A box whose operator has blanket NOPASSWD sudo answers yes, which is the
    correct answer — the commands will run and there is nothing left to grant.
    """
    if sys.platform != "linux":
        return False
    run = run or subprocess.run
    which = which or shutil.which
    systemctl = which("systemctl") or "/usr/bin/systemctl"
    try:
        r = run(["sudo", "-n", "-l", systemctl, "restart", f"{PROBE_UNIT}.service"],
                capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def removal_record(repo_root=None):
    """Teardown record for the service-controls feature: just the sudoers rule
    (see common/removal.py). The winlink-log group membership is left in place,
    as install does."""
    return {"files": [SUDOERS_PATH]}

# Passive RTL-SDR → GrayWolf feed flow probe (dashboard "APRS SDR Feed" meter).
# These MUST match server/app.py's _FEED_FLOW_ARGS / FEED_FLOW_PORT token-for-
# token — the endpoint runs `sudo -n <tcpdump> <SNIFF_ARGS>` and sudo only
# authorizes an exact command match. tcpdump on lo just *reads* a copy of the
# feed datagrams; it can't transmit or touch any other interface.
FEED_FLOW_PORT = 7355
SNIFF_ARGS = ["-ni", "lo", "-l", "-c", "10", "udp", "port", str(FEED_FLOW_PORT)]

# Hardware-aware conflict engine: apply-hardware re-templates a service's
# device config after a dashboard reassignment; the eeprom wrapper burns a
# unique RTL-SDR serial. Both are pinned, narrow entry points — apply_hardware
# takes NO arguments (nothing to inject); the eeprom wrapper validates its one
# argument itself before touching rtl_eeprom (see scripts/burn_dongle_serial.py).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
APPLY_HARDWARE_SCRIPT = os.path.join(REPO_ROOT, "scripts", "apply_hardware.py")
BURN_SERIAL_SCRIPT = os.path.join(REPO_ROOT, "scripts", "burn_dongle_serial.py")
# APRS frequency applier: rewrites the aprs-sdr-feed unit's rtl_fm -f token and
# restarts the feed. Wildcard arg like the eeprom wrapper — the script validates
# its own frequency (sdr_tune.normalize_freq) before touching the unit file.
SET_APRS_FREQ_SCRIPT = os.path.join(REPO_ROOT, "features", "rtl-sdr", "set-aprs-freq.py")

# Setup wizard's Reboot button (/api/setup/reboot): reboot takes NO arguments,
# same zero-arg pattern as apply_hardware above (nothing to inject). Path is
# the Debian/Raspberry Pi OS convention (/sbin/reboot); BENCH-VERIFY on real
# hardware — not confirmed in this dev environment.


def target_user(explicit):
    """User the OASIS server runs as: explicit > $SUDO_USER > current user."""
    return explicit or os.environ.get("SUDO_USER") or getpass.getuser()


def build_content(user, systemctl, tcpdump, reboot_bin):
    cmds = ", ".join(f"{systemctl} {a} {u}.service" for u in UNITS for a in ACTIONS)
    sniff = tcpdump + " " + " ".join(SNIFF_ARGS)
    apply_hw = f"{VENV_PYTHON} {APPLY_HARDWARE_SCRIPT}"
    burn_serial_cmd = f"{VENV_PYTHON} {BURN_SERIAL_SCRIPT} *"
    set_freq_cmd = f"{VENV_PYTHON} {SET_APRS_FREQ_SCRIPT} *"
    return (
        "# OASIS dashboard service controls — generated by enable-service-controls.py\n"
        f"# Lets '{user}' start/stop/restart only these OASIS units, no password,\n"
        f"# plus a fixed read-only tcpdump on lo for the APRS SDR feed flow meter,\n"
        f"# plus the hardware-aware engine's apply-hardware (zero-arg) and the\n"
        f"# validating eeprom-serial wrapper (validates its own argument — see\n"
        f"# scripts/burn_dongle_serial.py before assuming the wildcard below is unsafe),\n"
        f"# plus the APRS frequency applier (validates its own frequency argument —\n"
        f"# see features/rtl-sdr/set-aprs-freq.py) and a zero-arg reboot for the\n"
        f"# setup wizard's Reboot button.\n"
        f"Cmnd_Alias OASIS_SVC = {cmds}\n"
        f"Cmnd_Alias OASIS_SNIFF = {sniff}\n"
        f"Cmnd_Alias OASIS_HW_APPLY = {apply_hw}\n"
        f"Cmnd_Alias OASIS_HW_EEPROM = {burn_serial_cmd}\n"
        f"Cmnd_Alias OASIS_APRS_FREQ = {set_freq_cmd}\n"
        f"Cmnd_Alias OASIS_REBOOT = {reboot_bin}\n"
        f"{user} ALL=(root) NOPASSWD: OASIS_SVC, OASIS_SNIFF, OASIS_HW_APPLY, OASIS_HW_EEPROM, OASIS_APRS_FREQ, OASIS_REBOOT\n"
    )


def install(user):
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    tcpdump   = shutil.which("tcpdump")   or "/usr/bin/tcpdump"
    # Debian/Raspberry Pi OS convention; BENCH-VERIFY on real hardware — not
    # confirmed in this dev environment.
    reboot_bin = shutil.which("reboot")   or "/sbin/reboot"
    content   = build_content(user, systemctl, tcpdump, reboot_bin)
    _info(f"systemctl: {systemctl}")
    _info(f"tcpdump:   {tcpdump}  (read-only feed flow probe on lo)")
    _info(f"reboot:    {reboot_bin}  (zero-arg, setup wizard Reboot button)")
    _info("Units: " + ", ".join(UNITS))

    # Write to a temp file, VALIDATE with visudo, then install atomically. A
    # malformed sudoers file can lock you out of sudo, so we never write it
    # directly — visudo -cf must pass first.
    fd, tmp = tempfile.mkstemp(suffix=".sudoers")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        if _run(["sudo", "visudo", "-cf", tmp], check=False).returncode != 0:
            _fail("Generated sudoers file failed validation — NOT installing.")
        if _run(["sudo", "install", "-m", "0440", "-o", "root", "-g", "root",
                 tmp, SUDOERS_PATH], check=False).returncode != 0:
            _fail(f"Could not install {SUDOERS_PATH} (sudo required).")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    _ok(f"Installed {SUDOERS_PATH} for user '{user}'.")


JOURNAL_GROUP = "systemd-journal"


def _in_group(user, group):
    """True if *user* is a member of *group* (secondary membership)."""
    import grp
    try:
        return user in grp.getgrnam(group).gr_mem
    except KeyError:
        return False


def grant_journal_access(user):
    """Add *user* to the systemd-journal group so the OASIS server can read the
    Pat + Direwolf logs behind the Winlink session console (/api/winlink/log).
    Idempotent — safe to re-run."""
    import grp
    try:
        grp.getgrnam(JOURNAL_GROUP)
    except KeyError:
        _warn(f"No '{JOURNAL_GROUP}' group here — skipping. The Winlink log console "
              "may stay empty on this system.")
        return
    if _in_group(user, JOURNAL_GROUP):
        _ok(f"'{user}' already in '{JOURNAL_GROUP}' — Winlink session log is readable.")
        return
    if _run(["sudo", "usermod", "-aG", JOURNAL_GROUP, user], check=False).returncode == 0:
        _ok(f"Added '{user}' to '{JOURNAL_GROUP}' — Winlink session log now readable.")
        _warn("Restart the OASIS server so it picks up the new group membership:")
        _info("  sudo systemctl restart oasis")
    else:
        _warn(f"Could not add '{user}' to '{JOURNAL_GROUP}'. The Winlink log console "
              "will show '(journald log unavailable…)'.")


def disable():
    if not os.path.exists(SUDOERS_PATH):
        _ok("Already disabled (no sudoers file present).")
        return
    if _run(["sudo", "rm", "-f", SUDOERS_PATH], check=False).returncode == 0:
        _ok(f"Removed {SUDOERS_PATH}.")
    else:
        _fail(f"Could not remove {SUDOERS_PATH} (sudo required).")


def status():
    present = os.path.exists(SUDOERS_PATH)
    _info(f"sudoers file: {'present' if present else 'absent'}  ({SUDOERS_PATH})")
    r = _run(["sudo", "-n", "-l"], check=False, capture_output=True, text=True)
    out = getattr(r, "stdout", "") or ""
    if r.returncode == 0 and "OASIS_SVC" in out:
        _ok("sudo grants the OASIS service commands without a password.")
    else:
        _warn("sudo does not grant the OASIS service commands NOPASSWD for this user yet.")
    if r.returncode == 0 and ("OASIS_SNIFF" in out or "tcpdump" in out):
        _ok("sudo grants the feed flow probe (tcpdump on lo) without a password.")
    else:
        _warn("sudo does not grant the feed flow probe yet — the APRS SDR Feed meter "
              "will show 'flow: enable controls'. Re-run without --check to grant it.")
    if r.returncode == 0 and "OASIS_HW_APPLY" in out:
        _ok("sudo grants apply-hardware (device re-templating) without a password.")
    else:
        _warn("sudo does not grant apply-hardware yet — dashboard reassignment "
              "will persist but won't take effect until you run it manually.")
    if r.returncode == 0 and "OASIS_HW_EEPROM" in out:
        _ok("sudo grants the eeprom serial-naming wrapper without a password.")
    else:
        _warn("sudo does not grant the eeprom wrapper yet — 'name this dongle' "
              "will be unavailable from the dashboard.")
    if r.returncode == 0 and "OASIS_REBOOT" in out:
        _ok("sudo grants reboot (zero-arg) without a password.")
    else:
        _warn("sudo does not grant reboot yet — the setup wizard's Reboot "
              "button will be unavailable.")

    u = target_user(None)
    if _in_group(u, JOURNAL_GROUP):
        _ok(f"'{u}' is in '{JOURNAL_GROUP}' — the Winlink session log is readable.")
    else:
        _warn(f"'{u}' not in '{JOURNAL_GROUP}' — the Winlink log console will be empty. "
              "Re-run without --check to grant it.")


def run(args):
    print("\n  OASIS — enable-service-controls")
    _hr()
    if sys.platform != "linux":
        _fail("Service controls use systemd + sudoers — Linux only.")

    if args.check:
        status()
        print()
        return

    if args.disable:
        _step(1, "Removing dashboard service-control permission")
        disable()
        print()
        return

    user = target_user(args.user)
    _step(1, f"Granting '{user}' scoped NOPASSWD systemctl for OASIS units")
    install(user)
    _info("The dashboard power buttons (start / stop) will now work.")

    _step(2, f"Granting '{user}' journal read for the Winlink session log")
    grant_journal_access(user)

    _info("Undo with: python3 scripts/enable-service-controls.py --disable")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Allow the OASIS user to start/stop/restart OASIS services "
                    "from the dashboard (scoped sudoers NOPASSWD — no passwords in the browser).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 scripts/enable-service-controls.py            # grant (current user)\n"
                "  python3 scripts/enable-service-controls.py --user pi  # grant for 'pi'\n"
                "  python3 scripts/enable-service-controls.py --check     # report status\n"
                "  python3 scripts/enable-service-controls.py --disable   # remove\n"),
    )
    ap.add_argument("--user", help="User the OASIS server runs as (default: $SUDO_USER or current user).")
    ap.add_argument("--disable", action="store_true", help="Remove the permission.")
    ap.add_argument("--check", action="store_true", help="Report whether the permission is installed.")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
