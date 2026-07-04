#!/usr/bin/env python3
"""
enable-cm4stack.py
------------------
Configure a Raspberry Pi CM4 / M5Stack CM4Stack to run the OASIS panel
display (ST7789V2 SPI framebuffer + GT911 touch + GPIO fan).

Two phases, auto-detected by whether the ST7789 panel framebuffer is live:

  Phase 1 — panel NOT live (first run):
    Verify the M5Stack overlay binaries are in place, patch config.txt with the
    OASIS-managed overlay block, set headless boot, install Python runtime deps.
    Exits 10 (reboot required) — the panel only appears after a reboot.

  Phase 2 — panel live (after reboot):
    Build + install the GT911 touch-fix overlay (resolves the i2c0 mux issue
    described in §4 of cm4stack/cm4stack-oasis-panel.md), append it to the
    OASIS block in config.txt, install + enable the oasis-panel.service systemd
    unit. Exits 10 if a second reboot is needed for the touch fix, else 0.

Reference: cm4stack/cm4stack-oasis-panel.md

Usage:
  python3 scripts/enable-cm4stack.py                # auto-detect phase
  python3 scripts/enable-cm4stack.py --dry-run       # preview config.txt changes
  python3 scripts/enable-cm4stack.py --config-only   # only config.txt + headless
  python3 scripts/enable-cm4stack.py --service-only  # only install the service
  python3 scripts/enable-cm4stack.py --no-enable     # write the unit, don't start

Exit codes: 0 = done · 10 = reboot required · 1 = error.
Requires: Linux (Raspberry Pi OS), sudo.
"""

import argparse
import difflib
import getpass
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run

# ── Constants ────────────────────────────────────────────────────────────────

REBOOT_EXIT = 10

# config.txt locations (Trixie: /boot/firmware; older: /boot)
CONFIG_CANDIDATES = ("/boot/firmware/config.txt", "/boot/config.txt")
OVERLAY_DIR       = "/boot/firmware/overlays"
OVERLAY_DIR_OLD   = "/boot/overlays"

# OASIS managed block markers
BLOCK_BEGIN = "# --- OASIS CM4Stack (managed by scripts/enable-cm4stack.py) ---"
BLOCK_END   = "# --- end OASIS CM4Stack ---"

# Base overlay lines (gt911-cm4 is added in phase 2).
# dtparam=i2c_vc=on enables i2c0 (GPIO0/1) so the m5stack-cm4 mux can attach and
# expose the GT911 touch bus (i2c-10). Kernels through 6.12 enabled it implicitly;
# 6.18+ does not — without this line there is no i2c-10 and touch can't bind. It's
# idempotent/harmless on older kernels, so it stays unconditional.
BASE_OVERLAY_LINES = [
    "[all]",
    "dtparam=i2c_vc=on",
    "dtoverlay=m5stack-cm4",
    "dtoverlay=gpio-fan,gpiopin=13,temp=60000",
]

# Python runtime deps for oasis-panel.py
PYTHON_DEPS = ["python3-pil", "python3-numpy"]

# Systemd service name
SERVICE_NAME = "oasis-panel"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE_NAME}.service"

# oasis-panel.py lives in the repo's cm4stack/ dir (sibling of scripts/)
REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL_SCRIPT = os.path.join(REPO_ROOT, "cm4stack", "oasis-panel.py")

# Known-good m5stack-cm4.dtbo shipped with the OASIS bundle. A .dtbo is a compiled
# device-tree blob — kernel-version portable (unlike the arch/kernel-locked
# aw882xx .ko, §7) — so one binary is safe and durable. The stock Pi image does
# not include it, and a kernel/firmware update can wipe a hand-copied one;
# installing from here on every run covers both, fully offline. create-oasis-offline
# downloads it into offline-packages/cm4stack/ (not committed to the repo); the
# other paths are manual-drop fallbacks. Checked in order.
VENDORED_M5_OVERLAY_CANDIDATES = (
    os.path.join(REPO_ROOT, "offline-packages", "cm4stack", "m5stack-cm4.dtbo"),
    os.path.join(REPO_ROOT, "cm4stack", "overlays", "m5stack-cm4.dtbo"),
    os.path.join(REPO_ROOT, "cm4stack", "m5stack-cm4.dtbo"),
)


def vendored_m5_overlay():
    """Path to the vendored m5stack-cm4.dtbo, or None if it isn't shipped yet."""
    for p in VENDORED_M5_OVERLAY_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def find_config_txt():
    for p in CONFIG_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def find_overlay_dir():
    for p in (OVERLAY_DIR, OVERLAY_DIR_OLD):
        if os.path.isdir(p):
            return p
    return None


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except (PermissionError, OSError):
        r = subprocess.run(["sudo", "cat", path], capture_output=True, text=True)
        if r.returncode != 0:
            _fail(f"Cannot read {path}: {r.stderr.strip()}")
        return r.stdout


def write_text(path, content, backup_suffix=".oasis-bak"):
    bak = path + backup_suffix
    if not os.path.exists(bak):
        if subprocess.run(["sudo", "cp", path, bak]).returncode == 0:
            _ok(f"Backed up {os.path.basename(path)} → {os.path.basename(bak)}")
        else:
            _warn("Could not create a backup — aborting to be safe.")
            return False
    proc = subprocess.Popen(
        ["sudo", "tee", path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    proc.communicate(content.encode())
    return proc.returncode == 0


def panel_fb_live():
    """True when the ST7789 panel framebuffer is present and registered."""
    base = "/sys/class/graphics"
    try:
        for name in os.listdir(base):
            if not name.startswith("fb"):
                continue
            try:
                nm = open(f"{base}/{name}/name").read().strip().lower()
            except OSError:
                continue
            if "st7789" in nm:
                return True
    except OSError:
        pass
    return False


def gt911_overlay_installed(overlay_dir):
    """True when gt911-cm4.dtbo is already in the overlay directory."""
    return os.path.exists(os.path.join(overlay_dir, "gt911-cm4.dtbo"))


def gt911_in_config(text):
    """True when dtoverlay=gt911-cm4 is already in config.txt."""
    return bool(re.search(r"^\s*dtoverlay\s*=\s*gt911-cm4\b", text, re.MULTILINE))


def i2c10_of_path():
    """
    Return the device-tree path for the i2c-10 bus, e.g.
    '/soc/i2c@7e205000/i2c-mux@70/i2c@1'.
    Returns None if i2c-10 is not present.
    """
    link = "/sys/bus/i2c/devices/i2c-10/of_node"
    if not os.path.exists(link):
        return None
    try:
        real = os.path.realpath(link)
        # Strip the kernel's sysfs devicetree mount point
        mount = "/sys/firmware/devicetree/base"
        if real.startswith(mount):
            return real[len(mount):]
        return real
    except OSError:
        return None


def _i2c_buses():
    """Sorted list of i2c bus numbers currently present, for diagnostics."""
    out = []
    try:
        for name in os.listdir("/sys/bus/i2c/devices/"):
            if name.startswith("i2c-") and name[4:].isdigit():
                out.append(int(name[4:]))
    except OSError:
        pass
    return sorted(out)


def running_user():
    """
    Return the real (non-root) username even when invoked via sudo.
    Falls back to getpass.getuser().
    """
    for var in ("SUDO_USER", "USER", "LOGNAME"):
        u = os.environ.get(var)
        if u and u != "root":
            return u
    return getpass.getuser()


# ── config.txt transform ─────────────────────────────────────────────────────

def build_block(include_gt911):
    lines = [BLOCK_BEGIN] + BASE_OVERLAY_LINES
    if include_gt911:
        lines.append("dtoverlay=gt911-cm4")
    lines.append(BLOCK_END)
    return "\n".join(lines)


def transform_config(text, include_gt911=False):
    """
    Upsert the OASIS CM4Stack managed block.  If the block already exists it
    is replaced (idempotent).  Returns (new_text, changes_list).
    """
    changes = []
    block = build_block(include_gt911)

    if BLOCK_BEGIN in text:
        pre, _, rest = text.partition(BLOCK_BEGIN)
        _, _, post   = rest.partition(BLOCK_END)
        new_text = pre.rstrip("\n") + "\n" + post.lstrip("\n")
        changes.append("refreshed the OASIS CM4Stack managed block")
    else:
        new_text = text
        changes.append("added the OASIS CM4Stack managed block")

    new_text = new_text.rstrip("\n") + "\n\n" + block + "\n"
    return new_text, changes


# ── Step implementations ─────────────────────────────────────────────────────

def step_prereqs():
    _step(1, "Checking prerequisites")

    if sys.platform != "linux":
        _fail("This script configures a Raspberry Pi — Linux only.")

    if not shutil.which("systemctl"):
        _fail("systemctl not found. This script targets systemd systems.")

    # dtc — needed in phase 2 to compile the GT911 overlay.  Install now so the
    # user doesn't hit a missing-tool error mid-phase-2.
    if shutil.which("dtc"):
        _ok(f"dtc: {shutil.which('dtc')}")
    else:
        _info("dtc (device-tree-compiler) not found — installing via apt...")
        r = subprocess.run(
            ["sudo", "apt-get", "install", "-y", "device-tree-compiler"],
            capture_output=False,
        )
        if r.returncode != 0:
            _fail("Could not install device-tree-compiler. "
                  "Run: sudo apt-get install -y device-tree-compiler")
        if shutil.which("dtc"):
            _ok("dtc installed.")
        else:
            _fail("dtc still not found after install attempt.")


def step_check_overlays(overlay_dir):
    _step(2, "Checking M5Stack overlay files")

    m5_dtbo = os.path.join(overlay_dir, "m5stack-cm4.dtbo")
    if not os.path.exists(m5_dtbo):
        # Missing from the active boot overlays dir — stock Pi images don't ship
        # it, and a kernel/firmware update (linux-image-rpi* / raspi-firmware) can
        # wipe a hand-copied one. Heal it, preferring the copy vendored with OASIS
        # (works fully offline), then the other boot overlays dir, then give up.
        vendored = vendored_m5_overlay()
        other_dir = OVERLAY_DIR_OLD if overlay_dir == OVERLAY_DIR else OVERLAY_DIR
        other = os.path.join(other_dir, "m5stack-cm4.dtbo")
        if vendored:
            _info(f"Installing vendored m5stack-cm4.dtbo → {overlay_dir} "
                  f"(from {os.path.relpath(vendored, REPO_ROOT)}).")
            r = subprocess.run(["sudo", "cp", vendored, m5_dtbo])
            if r.returncode != 0 or not os.path.exists(m5_dtbo):
                _fail(f"Could not install m5stack-cm4.dtbo to {overlay_dir}.")
        elif os.path.exists(other):
            _info(f"m5stack-cm4.dtbo found in {other_dir} (not the active "
                  f"{overlay_dir}) — copying it across.")
            r = subprocess.run(["sudo", "cp", other, m5_dtbo])
            if r.returncode != 0 or not os.path.exists(m5_dtbo):
                _fail(f"Could not copy m5stack-cm4.dtbo from {other_dir} to {overlay_dir}.")
        else:
            _fail(
                f"m5stack-cm4.dtbo not found in {overlay_dir}, {other_dir}, or\n"
                f"  vendored in cm4stack/. OASIS ships this overlay — if it's missing,\n"
                "  drop the built binary at cm4stack/overlays/m5stack-cm4.dtbo, or\n"
                "  rebuild it and install manually, then re-run this script:\n\n"
                "    git clone https://github.com/m5stack/m5stack-linux-dtoverlays\n"
                "    cd m5stack-linux-dtoverlays\n"
                "    make\n"
                f"    sudo cp overlays/m5stack-cm4.dtbo {overlay_dir}/\n\n"
                "  (M5Stack's Makefile targets /boot/overlays; on Trixie the active\n"
                f"  directory is {overlay_dir} — copy it there.)"
            )
    _ok(f"m5stack-cm4.dtbo: {m5_dtbo}")

    # AW882xx audio: not driven on aarch64 (§7 of the .md), warn only.
    aw_dtbo = os.path.join(overlay_dir, "aw88xx.dtbo")
    if os.path.exists(aw_dtbo):
        _ok(f"aw88xx.dtbo: {aw_dtbo} (present — leave it out of config.txt per §7)")
    else:
        _info("aw88xx.dtbo absent — audio amp is deferred (§7), that is expected.")


def step_patch_config(dry_run, include_gt911=False):
    _step(3, "Patching /boot/firmware/config.txt")

    path = find_config_txt()
    if not path:
        _fail("config.txt not found (looked in /boot/firmware and /boot). "
              "Is this a Raspberry Pi?")
    _info(f"Boot config: {path}")

    text     = read_text(path)
    new_text, changes = transform_config(text, include_gt911=include_gt911)

    if new_text == text:
        _ok("config.txt already up to date — no change needed.")
        return False   # no change

    if dry_run:
        _info("Planned changes (--dry-run — nothing written):")
        for c in changes:
            _info(f"  - {c}")
        diff = difflib.unified_diff(
            text.splitlines(), new_text.splitlines(),
            fromfile=path, tofile=path + " (new)", lineterm="",
        )
        print("\n".join(diff))
        _info("Re-run without --dry-run to apply.")
        return False

    if not write_text(path, new_text):
        _fail("Could not write config.txt.")
    _ok(f"Updated {path}")
    for c in changes:
        _info(f"  - {c}")
    return True   # wrote changes


def step_set_headless():
    _step(4, "Setting headless (multi-user) boot target")

    r = subprocess.run(["systemctl", "get-default"], capture_output=True, text=True)
    current = r.stdout.strip()
    if current == "multi-user.target":
        _ok("Already set to multi-user.target — no change.")
        return

    r2 = subprocess.run(
        ["sudo", "systemctl", "set-default", "multi-user.target"],
        capture_output=True, text=True,
    )
    if r2.returncode == 0:
        _ok("Boot target set to multi-user.target (headless).")
        _info("The desktop compositor reclaims the panel and blanks it under X — "
              "headless is required for the display to stay on.")
    else:
        _warn(f"Could not set default target: {r2.stderr.strip()}")


def step_python_deps():
    _step(5, "Installing Python runtime dependencies")
    for pkg in PYTHON_DEPS:
        r = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg],
            capture_output=True, text=True,
        )
        if "install ok installed" in r.stdout:
            _ok(f"{pkg}: already installed")
            continue
        _info(f"{pkg}: installing via apt...")
        result = subprocess.run(
            ["sudo", "apt-get", "install", "-y", pkg],
            capture_output=False,
        )
        if result.returncode == 0:
            _ok(f"{pkg}: installed.")
        else:
            _warn(f"{pkg}: apt install failed — oasis-panel.py may fail to start. "
                  f"Install manually: sudo apt-get install -y {pkg}")


def step_build_gt911_overlay(overlay_dir):
    """
    Build and install the GT911 touch-fix overlay (phase 2).
    Resolves the i2c0 mux issue: the chip lives on i2c-10 (mux channel 1),
    not the i2c-0 node that m5stack-cm4 targets (§4 of the .md).
    Returns True when gt911-cm4.dtbo is present in overlay_dir afterwards
    (already there or freshly built), False when it could not be built.
    """
    _step(5, "Building + installing GT911 touch-fix overlay")

    if gt911_overlay_installed(overlay_dir):
        _ok(f"gt911-cm4.dtbo already in {overlay_dir}.")
        return True

    dtpath = i2c10_of_path()
    if not dtpath:
        buses = ", ".join(f"i2c-{b}" for b in _i2c_buses()) or "none"
        _warn("i2c-10 bus not found — cannot build the GT911 touch overlay yet.")
        _warn(f"  i2c buses present: {buses}")
        _warn("  The m5stack-cm4 overlay must be loaded first: finish phase 1, then")
        _warn("  REBOOT, then re-run this script. Touch stays off until the overlay builds;")
        _warn("  the config line is NOT added while the overlay is missing (no dangling ref).")
        return False

    _info(f"GT911 bus DT path: {dtpath}")

    dts = f"""/dts-v1/;
/plugin/;
/ {{
    compatible = "brcm,bcm2711";
    fragment@0 {{
        target-path = "{dtpath}";
        __overlay__ {{
            #address-cells = <1>;
            #size-cells = <0>;
            gt911@5d {{
                compatible = "goodix,gt911";
                reg = <0x5d>;
                interrupt-parent = <&gpio>;
                irq-gpios = <&gpio 22 0>;
                reset-gpios = <&gpio 24 0>;
                status = "okay";
            }};
        }};
    }};
}};
"""

    with tempfile.TemporaryDirectory() as tmp:
        dts_path = os.path.join(tmp, "gt911-cm4.dts")
        dtbo_tmp = os.path.join(tmp, "gt911-cm4.dtbo")
        with open(dts_path, "w") as f:
            f.write(dts)

        _info("Compiling gt911-cm4.dts...")
        r = subprocess.run(
            ["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", dtbo_tmp, dts_path],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            _warn(f"dtc failed:\n{r.stderr.strip()}")
            _fail("GT911 overlay compile failed.")

        dest = os.path.join(overlay_dir, "gt911-cm4.dtbo")
        r2 = subprocess.run(["sudo", "cp", dtbo_tmp, dest])
        if r2.returncode != 0:
            _fail(f"Could not copy gt911-cm4.dtbo to {overlay_dir}.")

    _ok(f"gt911-cm4.dtbo installed → {dest}")
    return True   # newly installed, config.txt needs dtoverlay=gt911-cm4


def _current_flip():
    """OASIS_PANEL_FLIP ('0'/'1') from the installed unit, default '0'."""
    try:
        with open(SERVICE_FILE) as f:
            m = re.search(r"^Environment=OASIS_PANEL_FLIP=(\d)", f.read(), re.MULTILINE)
            return m.group(1) if m else "0"
    except OSError:
        return "0"


def step_install_service(user, no_enable, flip=None):
    _step(6, "Installing oasis-panel systemd service")

    if not os.path.exists(PANEL_SCRIPT):
        _fail(
            f"oasis-panel.py not found at {PANEL_SCRIPT}.\n"
            "  Ensure the OASIS repo is checked out at the expected path."
        )
    _ok(f"Panel script: {PANEL_SCRIPT}")

    # Panel rotation. flip=None preserves whatever the installed unit has, so a
    # plain re-run never resets it; --flip / --no-flip set it explicitly.
    flip_val = ("1" if flip else "0") if flip is not None else _current_flip()
    _info(f"Panel rotation: OASIS_PANEL_FLIP={flip_val} "
          f"({'180° flipped' if flip_val == '1' else 'normal'})")

    unit = f"""\
[Unit]
Description=OASIS panel display (ST7789V2 CM4Stack)
After=multi-user.target network.target

[Service]
# Rotate the panel 180°: set to 1 (or run enable-cm4stack.py --flip), then
# `sudo systemctl restart oasis-panel`. 0 = normal orientation.
Environment=OASIS_PANEL_FLIP={flip_val}
ExecStart=/usr/bin/python3 {PANEL_SCRIPT}
WorkingDirectory={os.path.dirname(PANEL_SCRIPT)}
Restart=always
RestartSec=5
User={user}
SupplementaryGroups=video input

[Install]
WantedBy=multi-user.target
"""

    _info(f"Writing {SERVICE_FILE} ...")
    proc = subprocess.Popen(
        ["sudo", "tee", SERVICE_FILE],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    proc.communicate(unit.encode())
    if proc.returncode != 0:
        _fail(f"Could not write {SERVICE_FILE}.")
    _ok(f"Unit file written: {SERVICE_FILE}")

    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)

    if no_enable:
        _info("--no-enable: service unit written but not started.")
        _info(f"  Start manually: sudo systemctl enable --now {SERVICE_NAME}")
        return

    r = subprocess.run(
        ["sudo", "systemctl", "enable", "--now", SERVICE_NAME],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        _ok(f"{SERVICE_NAME} enabled and started.")
    else:
        _warn(f"systemctl enable --now failed:\n{r.stderr.strip()}")
        _info(f"  Try manually: sudo systemctl enable --now {SERVICE_NAME}")


def print_health_check():
    _hr()
    print("  One-glance health check:")
    print()
    checks = [
        ("Panel framebuffer",
         "ls /dev/fb*"),
        ("Touch bound on i2c-10",
         "ls /sys/bus/i2c/devices/ | grep 10-005d"),
        ("Touch claimed (UU)",
         "i2cdetect -y 10 | grep -q ' UU ' && echo 'touch: UU (bound)'"),
        ("Backlight device",
         "ls /sys/class/backlight/"),
        ("Headless boot",
         "systemctl get-default   # expect: multi-user.target"),
        ("Fan overlay in config",
         "grep -E 'gpio-fan|gpio=13' /boot/firmware/config.txt"),
        ("Panel service",
         f"systemctl status {SERVICE_NAME}"),
    ]
    for label, cmd in checks:
        print(f"  # {label}")
        print(f"    {cmd}")
        print()
    _hr()


# ── Phase implementations ────────────────────────────────────────────────────

def do_phase1(dry_run):
    """Phase 1: configure config.txt + headless, install Python deps. Reboot required."""
    overlay_dir = find_overlay_dir()
    if not overlay_dir:
        _fail("Overlay directory not found (tried /boot/firmware/overlays and /boot/overlays).")

    step_prereqs()
    step_check_overlays(overlay_dir)
    changed = step_patch_config(dry_run, include_gt911=False)
    step_set_headless()
    step_python_deps()

    print()
    if dry_run:
        _info("Dry run complete — re-run without --dry-run to apply changes.")
        return 0

    _warn("Reboot required — the ST7789V panel framebuffer only appears after a reboot.")
    _info("After rebooting, re-run this script to complete the installation:")
    _info(f"  python3 scripts/enable-cm4stack.py")
    return REBOOT_EXIT


def do_phase2(dry_run, no_enable, flip=None):
    """Phase 2: GT911 overlay, systemd service. May need a second reboot for touch."""
    overlay_dir = find_overlay_dir()
    if not overlay_dir:
        _fail("Overlay directory not found.")

    # Check prereqs again (dtc may not have been installed in phase 1 if --service-only).
    step_prereqs()

    # Source of truth is whether gt911-cm4.dtbo actually exists — the config line
    # (dtoverlay=gt911-cm4) must never be present without the overlay behind it.
    had_before  = gt911_overlay_installed(overlay_dir)
    have_gt911  = step_build_gt911_overlay(overlay_dir)   # True iff the .dtbo is present now
    freshly_built = have_gt911 and not had_before

    needs_reboot = False
    path = find_config_txt()
    if have_gt911:
        # Always refresh the whole managed block (i2c_vc + m5stack + fan + gt911),
        # even if dtoverlay=gt911-cm4 is already present — otherwise a pre-existing
        # gt911 line would skip the refresh and drop newer lines like i2c_vc.
        # step_patch_config is idempotent: it returns False when nothing changed.
        _step(6, "Refreshing config.txt managed block (i2c_vc + overlays + gt911)")
        changed = step_patch_config(dry_run, include_gt911=True)
        # Reboot if the block changed, or the overlay was just built (it only
        # loads on the next boot even when the config line was already there).
        if (changed or freshly_built) and not dry_run:
            needs_reboot = True
    else:
        # No overlay — make sure config.txt does NOT carry a dangling reference.
        if path and gt911_in_config(read_text(path)):
            _warn("config.txt has 'dtoverlay=gt911-cm4' but the overlay is not built —")
            _warn("touch cannot work. Removing the dangling line; re-run after a reboot")
            _warn("so i2c-10 is present and the overlay can build.")
            if not dry_run:
                new_text = re.sub(r"^\s*dtoverlay\s*=\s*gt911-cm4\b.*\n?", "",
                                  read_text(path), flags=re.MULTILINE)
                if write_text(path, new_text):
                    needs_reboot = True

    user = running_user()
    _info(f"Installing service for user: {user}")
    step_install_service(user, no_enable, flip)

    print()
    print_health_check()

    if needs_reboot and not dry_run:
        _warn("One more reboot is recommended so the GT911 touch overlay takes effect.")
        _info("The panel display will work immediately; touch events need the reboot.")
        return REBOOT_EXIT

    _ok("CM4Stack panel setup complete.")
    return 0


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Configure the M5Stack CM4Stack panel for OASIS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/enable-cm4stack.py             # auto-detect phase\n"
            "  python3 scripts/enable-cm4stack.py --dry-run   # preview config.txt edits\n"
            "  python3 scripts/enable-cm4stack.py --service-only  # install service only\n"
        ),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show config.txt changes without writing anything.")
    parser.add_argument("--config-only", action="store_true",
                        help="Only update config.txt + headless target (phase 1 only).")
    parser.add_argument("--service-only", action="store_true",
                        help="Only install/enable the systemd service (skip config.txt).")
    parser.add_argument("--no-enable", action="store_true",
                        help="Write the service unit but do not start or enable it.")
    parser.add_argument("--flip", dest="flip", action="store_const", const=True, default=None,
                        help="Rotate the panel 180° (OASIS_PANEL_FLIP=1 in the service).")
    parser.add_argument("--no-flip", dest="flip", action="store_const", const=False,
                        help="Restore normal panel orientation (OASIS_PANEL_FLIP=0).")
    args = parser.parse_args()

    print()
    print("  OASIS — enable-cm4stack")
    _hr()
    print("  Reference: cm4stack/cm4stack-oasis-panel.md")
    print()

    if sys.platform != "linux":
        _fail("This configures a Raspberry Pi — Linux only.")

    if args.config_only and args.service_only:
        _fail("--config-only and --service-only are mutually exclusive.")

    if args.service_only:
        user = running_user()
        _info(f"Installing service for user: {user}")
        step_prereqs()
        step_install_service(user, args.no_enable, args.flip)
        print()
        print_health_check()
        sys.exit(0)

    if args.config_only:
        overlay_dir = find_overlay_dir()
        if not overlay_dir:
            _fail("Overlay directory not found.")
        step_prereqs()
        step_check_overlays(overlay_dir)
        step_patch_config(args.dry_run, include_gt911=False)
        step_set_headless()
        sys.exit(0)

    # Auto-detect phase.
    if panel_fb_live():
        _info("ST7789V panel framebuffer detected — running phase 2 (post-reboot).")
        sys.exit(do_phase2(args.dry_run, args.no_enable, args.flip))
    else:
        _info("Panel framebuffer not detected — running phase 1 (initial setup).")
        sys.exit(do_phase1(args.dry_run))


if __name__ == "__main__":
    main()
