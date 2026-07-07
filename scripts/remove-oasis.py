#!/usr/bin/env python3
"""
remove-oasis.py — factory reset / uninstall
-------------------------------------------
Undo everything the OASIS setup scripts install on a Raspberry Pi: stop/disable/
remove services, delete OASIS-managed system files, and strip the OASIS-managed
blocks from config.txt. Large downloaded data (maps / FCC DB / ZIMs / wheels) is
NEVER auto-deleted — it is listed with sizes so you can remove it manually.

Dry-run by default: prints exactly what it would do and changes nothing. Pass
--apply to actually perform the teardown. See docs/remove-oasis-design.md.

What it removes (with --apply):
  • Services : oasis, oasis-panel, graywolf, graywolf-api, pat, kiwix, webssh,
               aprs-sdr-feed, openwebrx, rgb-cooling-hat
  • Files    : sudoers rule, RTL-SDR blacklist, oasis-browser-launch, ttyd, kiwix-serve
  • Dirs     : /opt/rgb-cooling-hat
  • config.txt: the OASIS-managed DRA-Pi & CM4Stack blocks + the DS3231 RTC overlay
  • hwclock-set: restored from the OASIS backup, if present

What it deliberately does NOT do (printed as manual follow-ups):
  • apt remove anything (chromium, lxde, rtl-sdr, gpsd, tcpdump … stay installed)
  • delete maps / FCC DB / ZIMs / wheels (expensive to re-download offline)
  • flip the boot target, touch i2c group membership, or reinstall fake-hwclock

Usage:
  python3 scripts/remove-oasis.py            # dry-run: print the plan, change nothing
  python3 scripts/remove-oasis.py --apply    # perform the teardown, then: sudo reboot
  python3 scripts/remove-oasis.py --check     # report present OASIS state; change nothing

Requires: Linux (Raspberry Pi OS), sudo.
"""

import argparse
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run  # noqa: E402

# ── Inventory (source of truth — see docs/remove-oasis-design.md) ─────────────
SERVICES = [
    "oasis", "oasis-panel",
    "graywolf", "graywolf-api", "pat", "kiwix", "webssh",
    "aprs-sdr-feed", "openwebrx", "rgb-cooling-hat",
]
FILES = [
    "/etc/sudoers.d/oasis-service-controls",
    "/etc/modprobe.d/rtlsdr-blacklist.conf",
    "/usr/local/bin/oasis-browser-launch",
    "/usr/local/bin/ttyd",
    "/usr/local/bin/kiwix-serve",
]
DIRS = [
    "/opt/rgb-cooling-hat",
]
HWCLOCK_SET = "/lib/udev/hwclock-set"
HWCLOCK_BAK = "/lib/udev/hwclock-set.oasis.bak"
RTC_OVERLAY = "dtoverlay=i2c-rtc,ds3231"


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Install state at the suite root (user-owned — removed without sudo).
# installed-services.json is the feature manifest setup-oasis.py writes; the
# dashboard reads it via /api/installed-services to hide cards for features that
# were never installed. Leaving it behind makes the dashboard keep showing
# torn-down services as if installed; deleting it returns the dashboard to its
# default "show everything" state — i.e. factory-fresh.
SUITE_FILES = [
    os.path.join(repo_root(), "installed-services.json"),
]


# Large downloaded data — NEVER auto-deleted; probed for existence and sized.
DATA_PATHS = [
    "/var/lib/graywolf",
    os.path.join(repo_root(), "maps"),
    os.path.join(repo_root(), "fcc-offline-database"),
    os.path.join(repo_root(), "server", "wheels"),
    os.path.join(repo_root(), "offline-packages"),
]


# ── Config.txt marker import (keep block definitions in sync with installers) ─
def _import_markers(filename, names):
    """Import named constants from a hyphenated sibling script (e.g. enable-dra-pi.py)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    mod_name = filename.replace("-", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tuple(getattr(mod, n) for n in names)


_DRA_BEGIN, _DRA_END = _import_markers("../features/dra-audio-interface/enable-dra-pi.py", ("BLOCK_BEGIN", "BLOCK_END"))
_CM4_BEGIN, _CM4_END = _import_markers("../displays/cm4stack/install-cm4stack.py", ("BLOCK_BEGIN", "BLOCK_END"))


def config_path():
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.exists(p):
            return p
    return None


def _strip_block(body, begin, end, label, changes):
    if begin in body:
        pre, _, rest = body.partition(begin)
        _, _, post = rest.partition(end)
        body = pre.rstrip("\n") + "\n" + post.lstrip("\n")
        changes.append(f"removed the {label} managed block")
    return body


def strip_oasis_config(text):
    """Return (new_text, changes) with the OASIS-managed config.txt edits removed.

    Removes the DRA-Pi and CM4Stack managed blocks (by marker) and the standalone
    DS3231 RTC overlay line. Leaves dtparam=i2c_arm=on and all unrelated lines.
    """
    changes = []
    body = text
    body = _strip_block(body, _DRA_BEGIN, _DRA_END, "DRA-Pi", changes)
    body = _strip_block(body, _CM4_BEGIN, _CM4_END, "CM4Stack", changes)

    kept = []
    for ln in body.splitlines():
        if ln.strip() == RTC_OVERLAY:
            changes.append(f"removed '{RTC_OVERLAY}'")
            continue
        kept.append(ln)
    out = "\n".join(kept).rstrip("\n")
    return (out + "\n") if out else "", changes


# ── Read-only reporting ───────────────────────────────────────────────────────
def _dir_size_human(path):
    total = 0
    if os.path.isfile(path):
        total = os.path.getsize(path)
    else:
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    size = float(total)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0


def data_advisory():
    """List existing DATA_PATHS as (path, human_size). Read-only."""
    out = []
    for p in DATA_PATHS:
        if os.path.exists(p):
            out.append((p, _dir_size_human(p)))
    return out


def print_data_advisory():
    adv = data_advisory()
    _step(5, "Downloaded data — left in place (delete manually if you want a clean slate)")
    if not adv:
        _info("none found.")
        return
    for p, size in adv:
        _info(f"{size:>8}  {p}")
    _warn("These are expensive/impossible to re-download offline. To wipe them:")
    for p, _ in adv:
        _info(f"  sudo rm -rf {p}")


def _svc_state(svc):
    unit = f"/etc/systemd/system/{svc}.service"
    r = _run(["systemctl", "is-active", svc], check=False, capture_output=True, text=True)
    state = (getattr(r, "stdout", "") or "").strip() or "unknown"
    return state, os.path.exists(unit)


def report_status():
    print("\n  OASIS — remove-oasis (status)")
    _hr()
    _step(1, "Services")
    for svc in SERVICES:
        state, has_unit = _svc_state(svc)
        _info(f"{svc:<18} {state:<10} unit:{'present' if has_unit else 'absent'}")
    _step(2, "Files / dirs")
    for p in FILES + DIRS + SUITE_FILES:
        _info(f"{'present' if os.path.exists(p) else 'absent':<8} {p}")
    _step(3, "config.txt")
    cfg = config_path()
    if cfg:
        with open(cfg, encoding="utf-8", errors="ignore") as fh:
            _, changes = strip_oasis_config(fh.read())
        _info(f"{cfg}: " + ("; ".join(changes) if changes else "no OASIS blocks present"))
    else:
        _info("config.txt not found (not a Pi?)")
    _step(4, "hwclock-set backup")
    _info((f"present: {HWCLOCK_BAK}") if os.path.exists(HWCLOCK_BAK) else "absent")
    print_data_advisory()
    print()


# ── Teardown actions ──────────────────────────────────────────────────────────
def remove_services(apply):
    _step(1, "Services")
    for svc in SERVICES:
        unit = f"/etc/systemd/system/{svc}.service"
        if not apply:
            _info(f"would stop/disable/remove {svc} ({unit})")
            continue
        _run(["sudo", "systemctl", "stop", svc], check=False)
        _run(["sudo", "systemctl", "disable", svc], check=False)
        _run(["sudo", "rm", "-f", unit], check=False)
        _ok(f"removed {svc}")
    if apply:
        _run(["sudo", "systemctl", "daemon-reload"], check=False)
        _ok("systemctl daemon-reload")


def remove_files(apply):
    _step(2, "Files / dirs")
    for p in FILES:
        if not os.path.exists(p):
            _info(f"absent: {p}")
            continue
        if not apply:
            _info(f"would remove {p}")
        else:
            _run(["sudo", "rm", "-f", p], check=False)
            _ok(f"removed {p}")
    for d in DIRS:
        if not os.path.exists(d):
            _info(f"absent: {d}")
            continue
        if not apply:
            _info(f"would remove {d}/")
        else:
            _run(["sudo", "rm", "-rf", d], check=False)
            _ok(f"removed {d}/")
    for p in SUITE_FILES:
        if not os.path.exists(p):
            _info(f"absent: {p}")
            continue
        if not apply:
            _info(f"would remove {p} (dashboard reverts to default)")
        else:
            try:
                os.remove(p)
                _ok(f"removed {p}")
            except OSError as e:
                _warn(f"could not remove {p}: {e}")


def remove_config(apply):
    _step(3, "config.txt")
    cfg = config_path()
    if not cfg:
        _info("config.txt not found — skipping.")
        return
    with open(cfg, encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    new_text, changes = strip_oasis_config(text)
    if not changes:
        _ok("config.txt already clean.")
        return
    if not apply:
        for c in changes:
            _info(f"would: {c}")
        return
    bak = cfg + ".oasis-remove.bak"
    _run(["bash", "-c", f"test -f {bak} || sudo cp {cfg} {bak}"], check=False)
    _run(["sudo", "tee", cfg], input=new_text, check=False, capture_output=True, text=True)
    for c in changes:
        _ok(c)
    _info(f"backup: {bak}")


def restore_hwclock(apply):
    _step(4, "hwclock-set")
    if not os.path.exists(HWCLOCK_BAK):
        _info("no OASIS backup — nothing to restore.")
        return
    if not apply:
        _info(f"would restore {HWCLOCK_SET} from {HWCLOCK_BAK}")
        return
    _run(["sudo", "cp", HWCLOCK_BAK, HWCLOCK_SET], check=False)
    _ok(f"restored {HWCLOCK_SET}")


def print_warnings():
    _step(6, "Manual follow-ups")
    gd = _run(["systemctl", "get-default"], check=False, capture_output=True, text=True)
    if "multi-user.target" in (getattr(gd, "stdout", "") or ""):
        _warn("Boot target is multi-user.target (headless). To restore the desktop:")
        _info("  sudo systemctl set-default graphical.target")
    _warn("fake-hwclock was removed by enable-rtc; reinstall online if you want it:")
    _info("  sudo apt install fake-hwclock")
    _info("apt packages (chromium, lxde, rtl-sdr, gpsd, tcpdump) left installed by design.")
    _info("i2c group membership left as-is (harmless).")
    _warn("REBOOT to drop the config.txt overlays:  sudo reboot")


# ── Orchestration ─────────────────────────────────────────────────────────────
def run(apply=False, check=False):
    if sys.platform != "linux":
        _fail("remove-oasis targets Raspberry Pi OS (Linux) only.")
    if check:
        report_status()
        return 0
    print("\n  OASIS — remove-oasis  " + ("(APPLY)" if apply else "(DRY-RUN)"))
    _hr()
    if not apply:
        _info("Dry-run: nothing will be changed. Re-run with --apply to perform it.")
    remove_services(apply)
    remove_files(apply)
    remove_config(apply)
    restore_hwclock(apply)
    print_data_advisory()
    print_warnings()
    _hr()
    if apply:
        print("\n  OASIS removed. Reboot to finish:  sudo reboot\n")
    else:
        print("\n  Dry-run complete. Re-run with --apply to perform the teardown.\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Factory-reset a Raspberry Pi: remove all OASIS services, files, "
                    "and config.txt blocks. Dry-run unless --apply.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 scripts/remove-oasis.py            # dry-run plan\n"
                "  python3 scripts/remove-oasis.py --apply    # perform teardown\n"
                "  python3 scripts/remove-oasis.py --check     # status only\n"),
    )
    ap.add_argument("--apply", action="store_true",
                    help="Perform the teardown (default is a dry-run).")
    ap.add_argument("--check", action="store_true",
                    help="Report present OASIS state; change nothing.")
    args = ap.parse_args(argv)
    return run(apply=args.apply, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
