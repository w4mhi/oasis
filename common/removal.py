"""Generic removal runner: undo one feature's removal record.

A removal record is written into installed-services.json by the installer at
install time (see common/installed_services.py). This module applies one — the
single mechanism shared by scripts/remove-oasis.py and the installer worker.

Record schema (all keys optional):

    {
      "services": [str],       # systemctl stop/disable + rm /etc/systemd/system/<svc>.service
      "files": [str],          # sudo rm -f
      "dirs": [str],           # sudo rm -rf
      "config_blocks": [[begin, end]],  # marker pairs to strip from config.txt
      "config_lines": [str],   # standalone lines to strip from config.txt
      "restore": [[src, dst]], # sudo cp src dst (e.g. hwclock-set backup)
      "script": str,           # repo-relative teardown script run instead of the above
      "data_paths": [str],     # ADVISORY ONLY — never deleted (offline data is precious)
      "requires_reboot": bool
    }

apply() is dry-run by default: it returns the intended changes without executing
anything. It never deletes data_paths, and never errors on absent files/units
(the underlying commands run with check=False), so removal is idempotent and safe
to re-run. config.txt edits are surfaced in the change list here but performed by
scripts/remove-oasis.py's single aggregated config rewrite, not per-record.
"""
from common.oasis_lib import _run as _default_run


def apply(record, apply=False, run=_default_run):
    """Apply one removal record. Returns
    {"ok", "changes": [str], "advisory": [str], "requires_reboot": bool}.
    With apply=False nothing is executed."""
    record = record or {}
    changes, advisory = [], []
    reboot = bool(record.get("requires_reboot"))

    # A bespoke teardown script owns the whole removal for complex features
    # (e.g. the 7" kiosk). It supersedes the declarative fields.
    script = record.get("script")
    if script:
        changes.append(f"run teardown script {script}")
        if apply:
            run(["python3", script, "--apply"], check=False)
        return {"ok": True, "changes": changes, "advisory": advisory, "requires_reboot": reboot}

    for svc in record.get("services", []):
        unit = f"/etc/systemd/system/{svc}.service"
        changes.append(f"stop/disable/remove {svc}")
        if apply:
            run(["sudo", "systemctl", "stop", svc], check=False)
            run(["sudo", "systemctl", "disable", svc], check=False)
            run(["sudo", "rm", "-f", unit], check=False)

    for p in record.get("files", []):
        changes.append(f"remove {p}")
        if apply:
            run(["sudo", "rm", "-f", p], check=False)

    for d in record.get("dirs", []):
        changes.append(f"remove {d}/")
        if apply:
            run(["sudo", "rm", "-rf", d], check=False)

    for pair in record.get("restore", []):
        src, dst = pair
        changes.append(f"restore {dst} from {src}")
        if apply:
            run(["sudo", "cp", src, dst], check=False)

    if record.get("services") and apply:
        run(["sudo", "systemctl", "daemon-reload"], check=False)

    # config.txt edits are aggregated into ONE file rewrite by remove-oasis.py
    # (so N features don't rewrite config.txt N times); here we only report them.
    for pair in record.get("config_blocks", []):
        begin = pair[0]
        changes.append(f"strip config.txt block starting {begin!r}")
    for ln in record.get("config_lines", []):
        changes.append(f"strip config.txt line {ln!r}")

    for p in record.get("data_paths", []):
        advisory.append(f"left in place (delete manually to reclaim space): {p}")

    return {"ok": True, "changes": changes, "advisory": advisory, "requires_reboot": reboot}
