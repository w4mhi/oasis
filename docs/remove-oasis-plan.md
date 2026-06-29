# remove-oasis.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/remove-oasis.py`, a dry-run-by-default "factory reset" that undoes everything the OASIS setup scripts install on a Raspberry Pi.

**Architecture:** Single self-contained script driven by data tables (services, files, dirs, data paths). A pure `strip_oasis_config(text)` function does the testable `config.txt` surgery; the rest orchestrates `systemctl`/`rm`/restore via `oasis_lib._run`. Off-Pi it `_fail`s early; dry-run prints the plan and mutates nothing.

**Tech Stack:** Python 3 stdlib only, `scripts/common/oasis_lib.py` helpers, `unittest` (run directly, no pytest).

## Global Constraints

- Offline-first: stdlib only, no new dependencies.
- Reuse `oasis_lib` helpers: `_hr, _step, _ok, _info, _warn, _fail, _run`.
- Match sibling-script style: banner, idempotent, `--check` read-only, `sys.path.insert` shim to import `common.oasis_lib`.
- Mutating actions go through `_run([... "sudo" ...], check=False)`; never abort on an already-absent target.
- Tests live in `scripts/tests/`, run with `python3 scripts/tests/<file>.py`, unittest-based, must pass off-Pi (no sudo, no systemctl).
- Spec: `docs/remove-oasis-design.md` (source of truth for the inventory tables).

---

### Task 1: Scaffold, constants, CLI, platform guard

**Files:**
- Create: `scripts/remove-oasis.py`
- Test: `scripts/tests/test_remove_oasis.py`

**Interfaces:**
- Produces:
  - `SERVICES: list[str]`, `FILES: list[str]`, `DIRS: list[str]`, `DATA_PATHS: list[str]`
  - `repo_root() -> str`
  - `config_path() -> str | None` (returns `/boot/firmware/config.txt` or `/boot/config.txt`, else `None`)
  - `main(argv=None) -> int`
  - argparse flags: `--apply` (bool), `--check` (bool)

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_remove_oasis.py
import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import importlib
remove_oasis = importlib.import_module("remove-oasis".replace("-", "_")) \
    if False else __import__("importlib").machinery  # placeholder, replaced below

# Import the hyphenated module by path:
import importlib.util
_MOD_PATH = os.path.join(_SCRIPTS, "remove-oasis.py")
_spec = importlib.util.spec_from_file_location("remove_oasis", _MOD_PATH)
remove_oasis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(remove_oasis)


class TestInventory(unittest.TestCase):
    def test_services_complete(self):
        for svc in ("oasis", "oasis-panel", "graywolf", "graywolf-api", "pat",
                    "kiwix", "webssh", "aprs-sdr-feed", "openwebrx", "rgb-cooling-hat"):
            self.assertIn(svc, remove_oasis.SERVICES)

    def test_files_and_dirs(self):
        self.assertIn("/etc/sudoers.d/oasis-service-controls", remove_oasis.FILES)
        self.assertIn("/opt/rgb-cooling-hat", remove_oasis.DIRS)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 scripts/tests/test_remove_oasis.py`
Expected: FAIL — `No such file or directory` for `scripts/remove-oasis.py`.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""
remove-oasis.py — factory reset / uninstall
-------------------------------------------
Undo everything the OASIS setup scripts install on a Raspberry Pi: stop/disable/
remove services, delete OASIS-managed system files, and strip the OASIS-managed
blocks from config.txt. Large downloaded data (maps/FCC DB/ZIMs/wheels) is NEVER
auto-deleted — it is listed with sizes for you to remove manually.

Dry-run by default. See docs/remove-oasis-design.md.

Usage:
  python3 scripts/remove-oasis.py            # dry-run: print the plan, change nothing
  python3 scripts/remove-oasis.py --apply    # perform the teardown
  python3 scripts/remove-oasis.py --check     # report present OASIS state; change nothing
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run  # noqa: E402

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


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DATA_PATHS = [
    "/var/lib/graywolf",
    os.path.join(repo_root(), "maps"),
    os.path.join(repo_root(), "fcc-offline-database"),
    os.path.join(repo_root(), "server", "wheels"),
    os.path.join(repo_root(), "offline-packages"),
]


def config_path():
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.exists(p):
            return p
    return None


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


def run(apply=False, check=False):
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 scripts/tests/test_remove_oasis.py`
Expected: PASS (2 tests). Also `python3 scripts/remove-oasis.py --help` prints usage.

- [ ] **Step 5: Commit**

```bash
git add scripts/remove-oasis.py scripts/tests/test_remove_oasis.py
git commit -m "feat: scaffold remove-oasis.py with inventory tables and CLI"
```

---

### Task 2: Pure config.txt surgery (`strip_oasis_config`)

**Files:**
- Modify: `scripts/remove-oasis.py`
- Test: `scripts/tests/test_remove_oasis.py`

**Interfaces:**
- Consumes: `config_path()` from Task 1.
- Produces: `strip_oasis_config(text: str) -> tuple[str, list[str]]` — returns the
  rewritten file text and a list of human-readable removal descriptions. Removes the
  DRA-Pi managed block, the CM4Stack managed block (each between its
  `BLOCK_BEGIN`/`BLOCK_END` markers, imported from the installers), and any standalone
  `dtoverlay=i2c-rtc,ds3231` line. Leaves `dtparam=i2c_arm=on` and all unrelated lines.

- [ ] **Step 1: Write the failing test**

```python
class TestStripConfig(unittest.TestCase):
    SAMPLE = (
        "dtparam=audio=on\n"
        "dtparam=i2c_arm=on\n"
        "dtoverlay=i2c-rtc,ds3231\n"
        "# --- OASIS DRA-Pi-Zero (managed by scripts/enable-dra-pi.py) ---\n"
        "dtparam=audio=off\n"
        "dtoverlay=audioinjector-wm8731-audio\n"
        "# --- end OASIS DRA-Pi-Zero ---\n"
        "# --- OASIS CM4Stack (managed by scripts/enable-cm4stack.py) ---\n"
        "dtoverlay=m5stack-cm4\n"
        "# --- end OASIS CM4Stack ---\n"
        "dtoverlay=vc4-kms-v3d\n"
    )

    def test_removes_oasis_only(self):
        out, changes = remove_oasis.strip_oasis_config(self.SAMPLE)
        self.assertNotIn("audioinjector-wm8731-audio", out)
        self.assertNotIn("m5stack-cm4", out)
        self.assertNotIn("i2c-rtc,ds3231", out)
        self.assertNotIn("OASIS DRA-Pi", out)
        self.assertNotIn("OASIS CM4Stack", out)
        # preserved:
        self.assertIn("dtparam=i2c_arm=on", out)
        self.assertIn("dtparam=audio=on", out)
        self.assertIn("dtoverlay=vc4-kms-v3d", out)
        self.assertTrue(changes)

    def test_idempotent_on_clean(self):
        clean = "dtparam=i2c_arm=on\ndtoverlay=vc4-kms-v3d\n"
        out, changes = remove_oasis.strip_oasis_config(clean)
        self.assertEqual(out.strip(), clean.strip())
        self.assertEqual(changes, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 scripts/tests/test_remove_oasis.py`
Expected: FAIL — `module 'remove_oasis' has no attribute 'strip_oasis_config'`.

- [ ] **Step 3: Write minimal implementation**

Add near the top of `remove-oasis.py`, after the `_run` import:

```python
import importlib.util


def _import_markers(filename, names):
    """Import named constants from a hyphenated sibling script (e.g. enable-dra-pi.py)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_").rstrip(".py"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tuple(getattr(mod, n) for n in names)


_DRA_BEGIN, _DRA_END = _import_markers("enable-dra-pi.py", ("BLOCK_BEGIN", "BLOCK_END"))
_CM4_BEGIN, _CM4_END = _import_markers("enable-cm4stack.py", ("BLOCK_BEGIN", "BLOCK_END"))

RTC_OVERLAY = "dtoverlay=i2c-rtc,ds3231"


def _strip_block(body, begin, end, label, changes):
    if begin in body:
        pre, _, rest = body.partition(begin)
        _, _, post = rest.partition(end)
        body = pre.rstrip("\n") + "\n" + post.lstrip("\n")
        changes.append(f"removed the {label} managed block")
    return body


def strip_oasis_config(text):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 scripts/tests/test_remove_oasis.py`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/remove-oasis.py scripts/tests/test_remove_oasis.py
git commit -m "feat: config.txt surgery for remove-oasis (markers + RTC line)"
```

---

### Task 3: Status report (`--check`) and data advisory

**Files:**
- Modify: `scripts/remove-oasis.py`
- Test: `scripts/tests/test_remove_oasis.py`

**Interfaces:**
- Consumes: `SERVICES`, `FILES`, `DIRS`, `DATA_PATHS`, `config_path`, `strip_oasis_config`.
- Produces:
  - `data_advisory() -> list[tuple[str, str]]` — for each existing `DATA_PATHS` entry,
    `(path, human_size)`; non-existent paths omitted. Pure/read-only.
  - `_dir_size_human(path) -> str` — `du -sh`-style size via `os.walk` (no shell).
  - `report_status()` — prints read-only status; performs no mutating `_run`.

- [ ] **Step 1: Write the failing test**

```python
import tempfile

class TestDataAdvisory(unittest.TestCase):
    def test_lists_only_existing(self):
        with tempfile.TemporaryDirectory() as d:
            present = os.path.join(d, "maps")
            os.makedirs(present)
            with open(os.path.join(present, "x.mbtiles"), "wb") as fh:
                fh.write(b"0" * 2048)
            orig = remove_oasis.DATA_PATHS
            remove_oasis.DATA_PATHS = [present, os.path.join(d, "absent")]
            try:
                adv = remove_oasis.data_advisory()
            finally:
                remove_oasis.DATA_PATHS = orig
            paths = [p for p, _ in adv]
            self.assertIn(present, paths)
            self.assertNotIn(os.path.join(d, "absent"), paths)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 scripts/tests/test_remove_oasis.py`
Expected: FAIL — `has no attribute 'data_advisory'`.

- [ ] **Step 3: Write minimal implementation**

```python
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
    for unit in ("B", "K", "M", "G", "T"):
        if total < 1024 or unit == "T":
            return f"{total:.0f}{unit}" if unit == "B" else f"{total:.1f}{unit}"
        total /= 1024.0


def data_advisory():
    out = []
    for p in DATA_PATHS:
        if os.path.exists(p):
            out.append((p, _dir_size_human(p)))
    return out


def _svc_state(svc):
    unit = f"/etc/systemd/system/{svc}.service"
    active = _run(["systemctl", "is-active", svc], check=False,
                  capture_output=True, text=True)
    state = (getattr(active, "stdout", "") or "").strip() or "unknown"
    return state, os.path.exists(unit)


def report_status():
    print("\n  OASIS — remove-oasis (status)")
    _hr()
    _step(1, "Services")
    for svc in SERVICES:
        state, has_unit = _svc_state(svc)
        _info(f"{svc:<18} {state:<10} unit:{'present' if has_unit else 'absent'}")
    _step(2, "Files / dirs")
    for p in FILES + DIRS:
        _info(f"{'present' if os.path.exists(p) else 'absent':<8} {p}")
    _step(3, "config.txt")
    cfg = config_path()
    if cfg:
        _, changes = strip_oasis_config(open(cfg, encoding="utf-8", errors="ignore").read())
        _info(f"{cfg}: " + ("; ".join(changes) if changes else "no OASIS blocks present"))
    else:
        _info("config.txt not found (not a Pi?)")
    _step(4, "hwclock-set backup")
    _info(("present " + HWCLOCK_BAK) if os.path.exists(HWCLOCK_BAK) else "absent")
    print_data_advisory()
    print()


def print_data_advisory():
    adv = data_advisory()
    _step(5, "Downloaded data — left in place (delete manually if you want a clean slate)")
    if not adv:
        _info("none found.")
        return
    for p, size in adv:
        _info(f"{size:>8}  {p}")
    _warn("These are expensive/impossible to re-download offline. To wipe:")
    for p, _ in adv:
        _info(f"  sudo rm -rf {p}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 scripts/tests/test_remove_oasis.py`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/remove-oasis.py scripts/tests/test_remove_oasis.py
git commit -m "feat: --check status report and data advisory for remove-oasis"
```

---

### Task 4: Teardown actions + dry-run/apply wiring

**Files:**
- Modify: `scripts/remove-oasis.py`
- Test: `scripts/tests/test_remove_oasis.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `remove_services(apply)`, `remove_files(apply)`, `remove_config(apply)`,
    `restore_hwclock(apply)` — each prints `would …` when `apply=False`, else acts via
    `_run([... sudo ...], check=False)`.
  - `run(apply, check)` wired: `check` → `report_status()`; else run all teardown
    phases then warnings; off-Pi guard via `config_path()`/`sys.platform`.
  - `print_warnings()` — boot-target, fake-hwclock, i2c group, apt, reboot notes.

- [ ] **Step 1: Write the failing test**

```python
class TestDryRunIsSafe(unittest.TestCase):
    def test_dry_run_makes_no_mutating_calls(self):
        calls = []
        orig = remove_oasis._run
        remove_oasis._run = lambda cmd, *a, **k: calls.append(cmd) or type(
            "R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        try:
            remove_oasis.remove_services(apply=False)
            remove_oasis.remove_files(apply=False)
            remove_oasis.restore_hwclock(apply=False)
        finally:
            remove_oasis._run = orig
        flat = " ".join(" ".join(c) for c in calls)
        for bad in ("sudo", "rm", "disable", "stop"):
            self.assertNotIn(bad, flat)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 scripts/tests/test_remove_oasis.py`
Expected: FAIL — `has no attribute 'remove_services'`.

- [ ] **Step 3: Write minimal implementation**

```python
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


def remove_config(apply):
    _step(3, "config.txt")
    cfg = config_path()
    if not cfg:
        _info("config.txt not found — skipping.")
        return
    text = open(cfg, encoding="utf-8", errors="ignore").read()
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
    _run(["sudo", "tee", cfg], input=new_text, check=False,
         capture_output=True, text=True)
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
    _warn("fake-hwclock was removed by enable-rtc; reinstall online if wanted:")
    _info("  sudo apt install fake-hwclock")
    _info("apt packages (chromium, lxde, rtl-sdr, gpsd, tcpdump) left installed by design.")
    _info("i2c group membership left as-is.")
    _warn("REBOOT to drop the config.txt overlays:  sudo reboot")
```

Replace the stub `run()`:

```python
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
        print("\n  OASIS removed. Reboot to finish.\n")
    else:
        print("\n  Dry-run complete. Re-run with --apply to perform the teardown.\n")
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 scripts/tests/test_remove_oasis.py`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/remove-oasis.py scripts/tests/test_remove_oasis.py
git commit -m "feat: teardown actions + dry-run/apply wiring for remove-oasis"
```

---

### Task 5: Executable bit, preflight, docs cross-link

**Files:**
- Modify: `scripts/remove-oasis.py` (chmod), `docs/SETUP.md` (mention the reset command)

- [ ] **Step 1: Make executable + byte-compile check**

```bash
chmod +x scripts/remove-oasis.py
python3 -m py_compile scripts/remove-oasis.py
python3 scripts/remove-oasis.py --help
python3 scripts/remove-oasis.py            # dry-run prints plan, exits 0 (off-Pi: _fail is fine)
```

Expected: `--help` and dry-run run without traceback. (On macOS the platform guard
`_fail`s — that is the intended off-Pi behavior.)

- [ ] **Step 2: Run preflight (CI gate mirror)**

Run: `/preflight`
Expected: green (byte-compile + thin-CLI `--help` + manifest checks pass).

- [ ] **Step 3: Add a short "Factory reset" note to docs/SETUP.md**

Add under the setup/uninstall section:

```markdown
### Factory reset / uninstall

To undo everything the setup scripts installed (services, system files,
config.txt blocks):

    python3 scripts/remove-oasis.py            # dry-run: show what would be removed
    python3 scripts/remove-oasis.py --apply    # perform it, then: sudo reboot

Downloaded data (maps, FCC database, ZIMs, wheels) is left in place; the script
prints those paths with sizes so you can delete them manually.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/remove-oasis.py docs/SETUP.md
git commit -m "chore: mark remove-oasis executable and document factory reset"
```

---

## Self-Review

- **Spec coverage:** services ✓ (Task 1/4), files+dirs ✓ (1/4), config.txt blocks+RTC ✓ (2/4), hwclock restore ✓ (4), data advisory (no auto-delete) ✓ (3), warnings/boot-target/apt/reboot ✓ (4), dry-run default + `--apply` + `--check` ✓ (1/3/4), off-Pi guard ✓ (4), tests ✓ (every task). No destructive `--purge-data` — matches the locked decision.
- **Placeholder scan:** Task 1 Step 1 contains a deliberately-replaced placeholder line for the import shim; the working import block directly below it (`importlib.util.spec_from_file_location`) is the real one to use — delete the placeholder line when writing the test.
- **Type consistency:** `strip_oasis_config -> (str, list)` used consistently in Tasks 2/3/4; `data_advisory -> list[(path, size)]` consistent in Task 3 print + test; `_run(..., check=False)` everywhere.
