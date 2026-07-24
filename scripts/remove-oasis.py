#!/usr/bin/env python3
"""
remove-oasis.py — factory reset / per-feature uninstall (manifest-driven)
-------------------------------------------------------------------------
Undo what the OASIS setup installs, reading the plan from the single source of
truth: installed-services.json. Each installed feature carries a removal record
(written at install time from the installer's own constants — see
common/installed_services.py and common/removal.py); this script runs the generic
runner over each record, plus a single aggregated config.txt rewrite and residual
system-teardown advisories.

Large downloaded data (maps / FCC DB / ZIMs / wheels / GrayWolf data) is NEVER
auto-deleted — it is surfaced as an advisory so you can remove it manually.

Boxes whose manifest predates the removal map self-heal: the plan backfills each
feature's record from the installers before running (no reinstall needed).

Dry-run by default: prints exactly what it would do and changes nothing. Pass
--apply to perform the teardown, or --feature KEY to uninstall a single feature.

Usage:
  python3 scripts/remove-oasis.py                 # dry-run: whole-suite plan
  python3 scripts/remove-oasis.py --apply         # perform the full teardown
  python3 scripts/remove-oasis.py --feature kiwix # dry-run one feature
  python3 scripts/remove-oasis.py --feature kiwix --apply
  python3 scripts/remove-oasis.py --check         # report installed state; change nothing

What it deliberately does NOT do (printed as manual follow-ups):
  • apt remove anything (chromium, rtl-sdr, gpsd, tcpdump … stay installed)
  • delete maps / FCC DB / ZIMs / wheels (expensive to re-download offline)
  • undo shared gpsd/chrony reconfig (see per-feature advisories)

Requires: Linux (Raspberry Pi OS), sudo, for --apply.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import hardware  # noqa: E402
from common import installed_services  # noqa: E402
from common import removal  # noqa: E402
from common import removal_backfill  # noqa: E402
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run  # noqa: E402


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plan(root, only=None):
    """Return the ordered removal plan as [(feature_key, record)].

    Backfills any missing removal records first (self-heal for legacy manifests),
    then returns the records for every installed feature — or just *only*, when a
    single feature is requested."""
    installed = installed_services.installed_features(root)
    removal_backfill.ensure(root, installed)
    rmap = installed_services.removal_map(root)
    keys = [only] if only else sorted(installed)
    return [(k, rmap.get(k, {})) for k in keys if k in installed]


def _apply_config(items, apply):
    """Aggregate every selected record's config.txt edits into ONE rewrite, so N
    features don't rewrite config.txt N times."""
    cfg = removal.config_path()
    _step(2, "config.txt")
    if not cfg:
        _info("config.txt not found — skipping.")
        return
    blocks, lines = [], []
    for _key, rec in items:
        blocks += [tuple(b) for b in rec.get("config_blocks", [])]
        lines += rec.get("config_lines", [])
    if not blocks and not lines:
        _ok("no OASIS config.txt edits to remove.")
        return
    with open(cfg, encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    new_text, changes = removal.strip_config(text, blocks, lines)
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


def _residual_warnings(reboot_needed):
    """System-level follow-ups not owned by any single feature."""
    _step(3, "Manual follow-ups")
    gd = _run(["systemctl", "get-default"], check=False, capture_output=True, text=True)
    if "multi-user.target" in (getattr(gd, "stdout", "") or ""):
        _warn("Boot target is multi-user.target (headless). To restore the desktop:")
        _info("  sudo systemctl set-default graphical.target")
    _info("apt packages (chromium, rtl-sdr, gpsd, tcpdump …) left installed by design.")
    if reboot_needed:
        _warn("REBOOT to finish dropping config.txt overlays:  sudo reboot")


def _report_status(root):
    print("\n  OASIS — remove-oasis (status)")
    _hr()
    items = plan(root)
    _step(1, "Installed features (from installed-services.json)")
    if not items:
        _info("none recorded.")
    for key, rec in items:
        svcs = ",".join(rec.get("services", [])) or "-"
        _info(f"{key:<18} services:{svcs}")
    print()


def run(apply=False, check=False, only=None):
    if sys.platform != "linux":
        _fail("remove-oasis targets Raspberry Pi OS (Linux) only.")
    root = repo_root()
    if check:
        _report_status(root)
        return 0

    items = plan(root, only)
    scope = f"feature '{only}'" if only else "whole suite"
    print(f"\n  OASIS — remove-oasis  ({scope}, {'APPLY' if apply else 'DRY-RUN'})")
    _hr()
    if only and not items:
        _warn(f"'{only}' is not installed (nothing recorded in the manifest).")
        return 0
    if not apply:
        _info("Dry-run: nothing will be changed. Re-run with --apply to perform it.")

    advisories = []
    reboot_needed = False
    _step(1, "Features")
    for key, rec in items:
        _info(f"— {key}")
        res = removal.apply(rec, apply=apply)
        reboot_needed = reboot_needed or res.get("requires_reboot")
        for c in res["changes"]:
            (_ok if apply else _info)(f"    {c}")
        advisories += res["advisory"]

    _apply_config(items, apply)

    if advisories:
        _step(4, "Left in place (delete manually if you want a clean slate)")
        for a in advisories:
            _warn(a)

    _residual_warnings(reboot_needed)

    # A whole-suite --apply also clears the manifest so the dashboard returns to
    # factory-fresh; a single --feature run drops just that feature.
    if apply:
        installed_services.remove_installed(root, {k for k, _ in items})
        # Factory-reset finalizer — parity with the web uninstall path: a
        # whole-suite reset clears the stale service->dongle assignments in
        # hardware.json (keeping the detected inventory), so the dongles don't
        # read as still set up.
        if only is None and hardware.clear_assignments(root):
            _ok("Cleared hardware device assignments (detected inventory kept).")

    _hr()
    if apply:
        print("\n  OASIS removed. Reboot to finish if prompted above:  sudo reboot\n")
    else:
        print("\n  Dry-run complete. Re-run with --apply to perform the teardown.\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Factory-reset a Raspberry Pi or uninstall one feature, driven "
                    "by installed-services.json. Dry-run unless --apply.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python3 scripts/remove-oasis.py                 # dry-run plan\n"
                "  python3 scripts/remove-oasis.py --apply         # perform teardown\n"
                "  python3 scripts/remove-oasis.py --feature kiwix # one feature (dry-run)\n"
                "  python3 scripts/remove-oasis.py --check         # status only\n"),
    )
    ap.add_argument("--apply", action="store_true",
                    help="Perform the teardown (default is a dry-run).")
    ap.add_argument("--check", action="store_true",
                    help="Report installed OASIS features; change nothing.")
    ap.add_argument("--feature", metavar="KEY", default=None,
                    help="Uninstall a single feature instead of the whole suite.")
    args = ap.parse_args(argv)
    return run(apply=args.apply, check=args.check, only=args.feature)


if __name__ == "__main__":
    sys.exit(main())
