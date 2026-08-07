#!/usr/bin/env python3
"""
rtc.py  (library — CLI entry points are features/rtc-*/enable-rtc.py)
---------------------------------------------------------------------
Configure a battery-backed I²C RTC so the Pi keeps accurate time across reboots
and total power loss with NO network — the clock chrony/GPS then discipline and
ride on. Boards are presets in BOARDS; each feature dir is a thin CLI over one:

  features/rtc-hat/enable-rtc.py     wittypi          UUGear Witty Pi 3
  features/rtc-raspad/enable-rtc.py  bigtreetech-7in  BigTreeTech 7" touchscreen

What a board install does (idempotent · REQUIRES A REBOOT):
  1. writes the board's config.txt lines inside ONE OASIS BEGIN/END block
     (I²C bus enable and/or the DSI display overlay, plus the i2c-rtc overlay)
  2. removes/disables fake-hwclock  (so it can't overwrite the real RTC)
  3. neutralises the `--systz` block in /lib/udev/hwclock-set (the classic
     i2c-rtc fix that otherwise resets the clock at boot)
  4. ensures the `hwclock` tool is installed — Debian 13 (Trixie) moved it out of
     `util-linux` into `util-linux-extra`, so `hwclock -w/-r` are otherwise
     "command not found" on a minimal Pi OS image

CONFIG.TXT POLICY: an RTC feature owns exactly ONE line — its i2c-rtc overlay,
written inside its own per-board BEGIN/END block, which teardown strips. Every
other line the board needs is a PREREQUISITE: added when missing, but always
outside the block and never removed. Prerequisites belong to hardware that
outlives the clock — dtoverlay=vc4-kms-dsi-7inch,dsi1 IS the Raspad's screen, and
dtparam=i2c_arm=on is the GPIO I²C bus every other I²C user shares. Removing
either on uninstall would break something this feature never owned (the classic
version of this mistake left a Pi with no sound cards). A wanted line already
present in config.txt is never duplicated, in or out of the block. See
docs/SETUP.md and the removal_record() docstring.

After the reboot, once the system clock is correct (from GPS/NTP), write it to
the RTC once:   sudo hwclock -w     (read it back with: sudo hwclock -r)

Requires: Linux (Raspberry Pi OS), sudo. The RTC attached on its I²C bus.
"""

import os
import shutil
import sys

from common.oasis_lib import _hr, _step, _ok, _info, _warn, _fail, _run

I2C_PARAM   = "dtparam=i2c_arm=on"
HWCLOCK_SET = "/lib/udev/hwclock-set"

# Per-board RTC facts.
#   overlay   — the i2c-rtc dtoverlay line that creates /dev/rtc0 at boot. The
#               ONLY line this installer owns (inside its block, removable).
#   prereqs   — config.txt lines the board's RTC needs but that this feature must
#               never own, because they belong to hardware that outlives the
#               clock. Added when missing, always OUTSIDE the block, never
#               removed. `i2c_arm=on` is the shared GPIO I²C bus (every other
#               I²C user needs it too); `vc4-kms-dsi-7inch,dsi1` IS the Raspad's
#               screen — removing it on uninstall would blank the display. (The
#               DSI-ribbon boards don't want i2c_arm at all: the RTC overlay's own
#               i2c_csi_dsi flag stands up i2c-10.)
#   bus/addr  — verify hint only (`i2cdetect -y <bus>` shows the chip as UU).
#   feature   — the OASIS feature key that owns this board (block marker + docs).
BOARDS = {
    "wittypi": {
        "label":   "Witty Pi 3 · DS3231",
        "chip":    "ds3231",
        "addr":    "0x68",
        "bus":     1,
        "overlay": "dtoverlay=i2c-rtc,ds3231",
        "prereqs": [I2C_PARAM],
        "feature": "rtc",
        "script":  "features/rtc-hat/enable-rtc.py",
    },
    "bigtreetech-7in": {
        "label":   'BigTreeTech 7" touchscreen · PCF8563',
        "chip":    "pcf8563",
        "addr":    "0x51",
        "bus":     10,
        "overlay": "dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi",
        "prereqs": ["dtoverlay=vc4-kms-dsi-7inch,dsi1"],
        "feature": "rtc-raspad",
        "script":  "features/rtc-raspad/enable-rtc.py",
    },
}
DEFAULT_BOARD = "wittypi"


def block_markers(board_id):
    """The per-board BEGIN/END comment pair that bounds this installer's
    config.txt edits. Per-board (not one shared 'OASIS RTC' block) so removing
    one RTC feature can never strip the other's lines."""
    board = BOARDS[board_id]
    return (f"# --- OASIS RTC {board_id} (managed by {board['script']}) ---",
            f"# --- end OASIS RTC {board_id} ---")


def removal_record(repo_root=None, board_id=DEFAULT_BOARD):
    """Teardown record for an RTC feature: drop this board's config.txt block and
    restore /lib/udev/hwclock-set from the .oasis.bak the installer made.

    The block IS the boundary, and only the i2c-rtc overlay is ever inside it.
    The board's prerequisites live outside by construction — whether they were
    already in config.txt or this installer added them — so teardown cannot reach
    them however the box got set up. That's what keeps an uninstall from deleting
    the DSI display overlay (dtoverlay=vc4-kms-dsi-7inch,dsi1) and blanking the
    Raspad's screen, or a dtparam=i2c_arm=on shared with other I²C users. Reboot
    to drop the overlay."""
    begin, end = block_markers(board_id)
    return {"config_blocks": [[begin, end]],
            "restore": [[HWCLOCK_SET + ".oasis.bak", HWCLOCK_SET]],
            "requires_reboot": True}


def config_path():
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.exists(p):
            return p
    return None


# ── Pure config.txt planning/rendering (unit-tested; no I/O, no sudo) ──────────

def _active_lines(text):
    """The uncommented, stripped lines of config.txt."""
    return [s for s in (ln.strip() for ln in text.splitlines())
            if s and not s.startswith("#")]


def _overlay_key(line):
    """Identity of a config.txt line for "is this already configured?" purposes:
    the overlay name plus its FIRST parameter, ignoring any extra ones.

    Exact-string matching is too strict — `dtoverlay=vc4-kms-dsi-7inch,dsi1` and
    `dtoverlay=vc4-kms-dsi-7inch,dsi1,rotate=180` are the same overlay on the same
    port, and appending our plain version next to a tuned one would load the
    overlay twice with conflicting parameters. Matching on the bare name alone is
    too loose in the other direction: `dtoverlay=i2c-rtc,ds3231` and
    `dtoverlay=i2c-rtc,pcf8563` share the name but are different chips, and
    treating one as satisfying the other would silently skip the RTC overlay. The
    first parameter is the discriminator in both cases (chip name; DSI port)."""
    return ",".join(line.split(",")[:2]).strip()


def strip_block(text, begin, end):
    """Remove one BEGIN/END block (inclusive) from *text*. Idempotent."""
    out, inside = [], False
    for ln in text.splitlines():
        s = ln.strip()
        if not inside and s == begin:
            inside = True
            continue
        if inside:
            if s == end:
                inside = False
            continue
        out.append(ln)
    return "\n".join(out)


def plan_lines(text, board_id):
    """Sort this board's config.txt lines into three buckets, given *text*.

      owned      — goes INSIDE our block, and so is removed on uninstall. Only
                   ever the i2c-rtc overlay: the one line that exists purely
                   because this feature is installed.
      prereq_add — a prereq missing from config.txt: appended OUTSIDE the block,
                   where no teardown can reach it. The RTC needs it, but it
                   belongs to hardware that outlives the clock (the DSI panel;
                   the shared GPIO I²C bus), so removing it later would break
                   something this feature never owned.
      present    — a wanted line already active elsewhere in config.txt. Left
                   exactly where it is; never duplicated into the block.

    Comparison is by _overlay_key(), so a tuned variant of a line counts as
    present."""
    board = BOARDS[board_id]
    begin, end = block_markers(board_id)
    outside = {_overlay_key(s) for s in _active_lines(strip_block(text, begin, end))}
    owned = [board["overlay"]] if _overlay_key(board["overlay"]) not in outside else []
    prereq_add = [ln for ln in board["prereqs"] if _overlay_key(ln) not in outside]
    present = [ln for ln in [*board["prereqs"], board["overlay"]]
               if _overlay_key(ln) in outside]
    return owned, prereq_add, present


def render_config(text, board_id):
    """Return (new_text, owned, prereq_add, present) with this board's prereqs
    ensured outside the block and the block rewritten to exactly the lines it
    owns. Idempotent: re-rendering unchanged input returns identical text."""
    board = BOARDS[board_id]
    begin, end = block_markers(board_id)
    owned, prereq_add, present = plan_lines(text, board_id)
    body = strip_block(text, begin, end).rstrip("\n")

    if prereq_add:
        # Deliberately outside the block, with a note saying why — teardown
        # strips only the block, so these survive every uninstall.
        note = (f"# {board['feature']} prerequisite (added by {board['script']}; "
                "NOT removed on uninstall — it belongs to the hardware, not the clock)")
        added = "\n".join([note, *prereq_add])
        body = f"{body}\n{added}" if body else added

    if not owned:
        # Nothing to own (the RTC overlay already lives outside our block) —
        # leave no empty block behind.
        return ((body + "\n") if body else ""), owned, prereq_add, present
    block = "\n".join([begin, *owned, end])
    return (f"{body}\n{block}\n" if body else f"{block}\n"), owned, prereq_add, present


# ── Install steps ─────────────────────────────────────────────────────────────

def check_platform():
    if sys.platform != "linux":
        _fail("The hardware RTC is configured on Raspberry Pi OS (Linux) only.")
    if not config_path():
        _fail("No /boot/firmware/config.txt or /boot/config.txt — is this Raspberry Pi OS?")


def write_config(cfg, board_id):
    """Rewrite this board's config.txt block. One whole-file write (via sudo tee,
    the same mechanism remove-oasis.py uses) rather than appends, so the block
    stays exactly in sync with what the board needs."""
    try:
        with open(cfg, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        _fail(f"Could not read {cfg}.")
        return
    new_text, owned, prereq_add, present = render_config(text, board_id)
    for ln in present:
        _info(f"'{ln}' already in {cfg} — leaving it exactly where it is.")
    if new_text == text:
        _ok(f"config.txt already correct for {BOARDS[board_id]['label']}.")
        return
    bak = cfg + ".oasis-rtc.bak"
    _run(["bash", "-c", f"test -f {bak} || sudo cp {cfg} {bak}"], check=False)
    r = _run(["sudo", "tee", cfg], input=new_text, check=False,
             capture_output=True, text=True)
    if getattr(r, "returncode", 1) != 0:
        _fail(f"Could not write to {cfg}.")
        return
    for ln in prereq_add:
        _ok(f"Added prerequisite '{ln}' to {cfg} (outside the OASIS block — "
            "uninstalling this feature will NOT remove it).")
    for ln in owned:
        _ok(f"Added '{ln}' to the OASIS RTC block in {cfg}.")
    _info(f"backup: {bak}")


def disable_fake_hwclock():
    _run(["sudo", "apt", "remove", "-y", "fake-hwclock"], check=False)
    _run(["sudo", "systemctl", "disable", "fake-hwclock"], check=False)
    _run(["sudo", "update-rc.d", "-f", "fake-hwclock", "remove"], check=False)
    _ok("fake-hwclock removed/disabled (it can no longer overwrite the hardware RTC).")


def patch_hwclock_set():
    """Comment any active `--systz` line in /lib/udev/hwclock-set (the classic
    i2c-rtc fix). Idempotent; backs the file up once."""
    if not os.path.exists(HWCLOCK_SET):
        _info(f"{HWCLOCK_SET} not present — skipping.")
        return
    _run(["bash", "-c",
          f"test -f {HWCLOCK_SET}.oasis.bak || sudo cp {HWCLOCK_SET} {HWCLOCK_SET}.oasis.bak"],
         check=False)
    patch = (
        "import sys\n"
        "p = sys.argv[1]\n"
        "lines = open(p).read().splitlines()\n"
        "out, changed = [], False\n"
        "for ln in lines:\n"
        "    if '--systz' in ln and not ln.lstrip().startswith('#'):\n"
        "        out.append('#' + ln); changed = True\n"
        "    else:\n"
        "        out.append(ln)\n"
        "open(p, 'w').write('\\n'.join(out) + '\\n')\n"
        "print('changed' if changed else 'nochange')\n"
    )
    r = _run(["sudo", "python3", "-c", patch, HWCLOCK_SET], check=False, capture_output=True, text=True)
    if "changed" in (getattr(r, "stdout", "") or ""):
        _ok("Neutralised the --systz reset in /lib/udev/hwclock-set.")
    else:
        _ok("/lib/udev/hwclock-set already clean.")


def _hwclock_path():
    """Locate the hwclock binary. Debian 13 (Trixie) moved it into the
    `util-linux-extra` package, and it lives in /usr/sbin (not always on the
    Python process's PATH), so probe the standard sbin locations too."""
    return (shutil.which("hwclock")
            or next((p for p in ("/usr/sbin/hwclock", "/sbin/hwclock")
                     if os.path.exists(p)), None))


def ensure_hwclock():
    """Guarantee the `hwclock` tool exists — the RTC is seeded with `hwclock -w`
    and read back with `hwclock -r`. On Debian Trixie it split out of util-linux
    into `util-linux-extra`, which a minimal Pi OS image omits, so the classic
    commands fail 'command not found'. Best-effort apt install (needs internet,
    like the fake-hwclock removal step); warns with guidance if still missing."""
    if _hwclock_path():
        _ok("hwclock present.")
        return
    _info("hwclock not found — installing util-linux-extra (Trixie split it out) …")
    _run(["sudo", "apt", "install", "-y", "util-linux-extra"], check=False)
    if _hwclock_path():
        _ok("Installed util-linux-extra — hwclock is now available.")
    else:
        _warn("hwclock still missing. When online, run:  "
              "sudo apt update && sudo apt install util-linux-extra   "
              "(the RTC read/write tool lives there on Debian Trixie). "
              "Meanwhile the RTC still reads via sysfs — see below.")


def sysfs_rtc_name():
    """The loaded RTC driver from sysfs (e.g. 'rtc-pcf8563 10-0051'), or ''. The
    cheapest proof the overlay took after a reboot — no sudo, no i2cdetect."""
    try:
        with open("/sys/class/rtc/rtc0/name", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def verify(board):
    if not os.path.exists("/dev/rtc0"):
        _warn(f"/dev/rtc0 not present yet — reboot to load the {board['chip'].upper()} overlay.")
        return
    name = sysfs_rtc_name()
    if name and board["chip"] not in name:
        # A different RTC answered — usually the other board's chip, or a stale
        # overlay still in config.txt. Loud, because time will look fine either
        # way until the battery is actually needed.
        _warn(f"/dev/rtc0 is '{name}', not the expected {board['chip']} — "
              "check for a second i2c-rtc overlay in config.txt.")
    elif name:
        _ok(f"RTC driver loaded: {name}")
    hw = _hwclock_path()
    if hw:
        r = _run(["sudo", hw, "-r"], check=False, capture_output=True, text=True)
        _ok(f"/dev/rtc0 present — RTC reads: {(getattr(r, 'stdout', '') or '').strip() or '(unreadable)'}")
        return
    # hwclock absent (Trixie util-linux-extra not installed) — read the RTC via
    # the kernel's sysfs interface instead, which needs no extra package.
    try:
        with open("/sys/class/rtc/rtc0/date") as fd, open("/sys/class/rtc/rtc0/time") as ft:
            _ok(f"/dev/rtc0 present — RTC reads (UTC, via sysfs): {fd.read().strip()} {ft.read().strip()}")
    except OSError:
        _ok("/dev/rtc0 present.")
    _warn("hwclock not installed — install it to seed/read the RTC:  "
          "sudo apt install util-linux-extra")


def run(check_only=False, board_id=DEFAULT_BOARD):
    board = BOARDS[board_id]
    print(f"\n  OASIS — enable-rtc  ({board['label']})")
    _hr()
    check_platform()
    cfg = config_path()

    if check_only:
        _info(f"board  : {board_id}  ({board['label']})")
        _info(f"config : {cfg}")
        try:
            with open(cfg, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            text = ""
        owned, prereq_add, present = plan_lines(text, board_id)
        _info("RTC overlay : " + ("no" if owned else "yes"))
        for ln in present:
            _info(f"already present, left alone: {ln}")
        for ln in prereq_add:
            _info(f"missing, would add as a prerequisite (never removed): {ln}")
        for ln in owned:
            _info(f"missing, would add to our block (removed on uninstall): {ln}")
        verify(board)
        print()
        return

    _info(f"Configures the {board['label']} hardware clock for time across reboots.")
    print()
    _step(1, "Writing the config.txt RTC block");         write_config(cfg, board_id)
    _step(2, "Disabling fake-hwclock");                   disable_fake_hwclock()
    _step(3, "Neutralising hwclock-set --systz");         patch_hwclock_set()
    _step(4, "Ensuring hwclock is installed");            ensure_hwclock()

    _hr()
    print(f"\n  {board['label']} RTC configured — REBOOT to load it.")
    _info("After reboot, once the clock is correct (GPS/NTP):  sudo hwclock -w")
    _info(f"Verify:  sudo hwclock -r   and   i2cdetect -y {board['bus']}   "
          f"(the chip shows as UU at {board['addr']})")
    print()
