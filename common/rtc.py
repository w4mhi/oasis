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
  2. LEAVES fake-hwclock alone (see run() — surrendering the fallback
     before the RTC is proven is what stranded two stations)
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
    # The Pi 5 has an RTC in the SoC — no i2c chip, no overlay, nothing on
    # i2cdetect. It owns a config.txt line ONLY when trickle charging is turned
    # on, which is why `overlay` is None here and supplied at render time.
    "pi5": {
        "label":   "Raspberry Pi 5 · built-in RTC",
        "chip":    "rpi-rtc",
        "addr":    None,
        "bus":     None,
        "overlay": None,
        "prereqs": [],
        "feature": "rtc-pi5",
        "script":  "features/rtc-pi5/enable-rtc.py",
    },
}
DEFAULT_BOARD = "wittypi"

# Trickle-charge voltage for the Pi 5's backup cell, in microvolts, matching the
# kernel's own range (/sys/class/rtc/rtc0/charging_voltage_{min,max} read
# 1300000 and 4400000). 3.0 V suits the ML2032/LIR2032 cells Raspberry Pi ship.
#
# THIS IS OFF UNLESS THE OPERATOR ASKS FOR IT, and that is a safety decision,
# not a default-conservatism one. Charging a RECHARGEABLE ML2032/LIR2032 is
# correct; applying the same voltage to a primary CR2032 can vent or rupture it.
# Nothing on the board can tell them apart: a coin cell has two contacts, no ID
# pin and no thermistor. The one measurement that hints at it — a LIR2032 reads
# ~3.6 V where a CR2032 never does — cannot resolve the dangerous direction,
# because a rechargeable ML2032 sits at ~3.0 V and reads exactly like a CR2032.
# So the chemistry is asked, never inferred. The Pi's own default is off too.
PI5_CHARGE_UV = 3000000
PI5_BATTERY_V = "/sys/class/rtc/rtc0/battery_voltage"


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


def plan_lines(text, board_id, overlay=None):
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
    # `overlay=None` is a board that owns NO config.txt line (the Pi 5 with
    # charging off). render_config already leaves no empty block behind.
    ov = board["overlay"] if overlay is None else overlay
    outside = {_overlay_key(s) for s in _active_lines(strip_block(text, begin, end))}
    owned = [ov] if ov and _overlay_key(ov) not in outside else []
    prereq_add = [ln for ln in board["prereqs"] if _overlay_key(ln) not in outside]
    present = [ln for ln in [*board["prereqs"], ov]
               if ln and _overlay_key(ln) in outside]
    return owned, prereq_add, present


def render_config(text, board_id, overlay=None):
    """Return (new_text, owned, prereq_add, present) with this board's prereqs
    ensured outside the block and the block rewritten to exactly the lines it
    owns. Idempotent: re-rendering unchanged input returns identical text."""
    board = BOARDS[board_id]
    begin, end = block_markers(board_id)
    owned, prereq_add, present = plan_lines(text, board_id, overlay)
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


def write_config(cfg, board_id, overlay=None):
    """Rewrite this board's config.txt block. One whole-file write (via sudo tee,
    the same mechanism remove-oasis.py uses) rather than appends, so the block
    stays exactly in sync with what the board needs."""
    try:
        with open(cfg, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        _fail(f"Could not read {cfg}.")
        return
    new_text, owned, prereq_add, present = render_config(text, board_id, overlay)
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


def fake_hwclock_state():
    """(installed, active) for fake-hwclock — the fallback that saves the clock
    at shutdown and restores it at boot on a machine with no working RTC.

    Both halves matter, and looking at only one is how this went wrong: on
    pi4oasis the package was `ii` (installed) while its unit was symlinked to
    /dev/null, so `systemctl is-enabled` answered "not-found" and it looked
    absent. It was present and inert — which is the same thing for timekeeping
    and a different thing entirely for the repair (unmask, not apt install)."""
    # Tolerate a missing binary: on a non-Debian dev box dpkg-query/systemctl
    # simply are not there, and "cannot ask" must not look like an exception to
    # every caller up the stack.
    def _out(cmd):
        try:
            r = _run(cmd, check=False, capture_output=True, text=True)
        except OSError:
            return ""
        return getattr(r, "stdout", "") or ""

    ok = "install ok installed" in _out(["dpkg-query", "-W", "-f=${Status}", "fake-hwclock"])
    active = type("R", (), {"stdout": _out(["systemctl", "is-enabled", "fake-hwclock"])})
    enabled = (getattr(active, "stdout", "") or "").strip() in ("enabled", "enabled-runtime",
                                                               "static", "indirect")
    return ok, enabled


def pi5_battery_millivolts():
    """The Pi 5 backup cell's voltage in mV, or None when the box cannot report
    one (not a Pi 5, or the attribute is missing).

    ZERO DOES NOT MEAN "NO CELL". Corrected 2026-08-12 against a Pi 5 with a
    healthy coin cell that read 0 here: the reading is tied to the RTC's trickle
    CHARGING configuration, and charging is off unless `rtc_bbat_vchg` is set in
    config.txt — which it should be only for a rechargeable cell. With the
    standard non-rechargeable primary cell, charging is correctly disabled,
    nothing measures the voltage, and this reports 0 forever while the cell works
    perfectly.

    The earlier claim here — that 0 proved no cell was fitted — was wrong, and
    wrong in the direction that matters: it called a working RTC broken. Use
    rtc_is_working() (does it read a plausible date) and set_system_clock_at_boot()
    (did the kernel actually take the time from it) instead. A non-zero value here
    is informative; a zero is silence."""
    try:
        with open(PI5_BATTERY_V, encoding="utf-8") as fh:
            return int(fh.read().strip()) // 1000
    except (OSError, ValueError):
        return None


def set_system_clock_at_boot():
    """Did the kernel set the system clock FROM this RTC at boot?

    The strongest evidence available that an RTC actually held time across a
    power cycle — stronger than any voltage reading, because it is the outcome
    rather than a proxy for it. /sys/class/rtc/rtc0/hctosys is 1 when the kernel
    used this device to seed the clock; dmesg shows the same thing as
    "setting system clock to ...".

    None when it cannot be read, which is not the same as False."""
    try:
        with open("/sys/class/rtc/rtc0/hctosys", encoding="utf-8") as fh:
            return fh.read().strip() == "1"
    except OSError:
        return None


def is_pi5_rtc():
    """True when this box has the Pi 5's built-in RTC.

    Probed from the attribute the driver exposes, not from the model string: a
    model name is a label, and the thing we actually need to know is whether the
    kernel is offering us a battery-backed RTC to configure."""
    return os.path.exists(PI5_BATTERY_V)


def rtc_is_working():
    """(ok, detail) — does a hardware RTC exist and read a plausible date?

    HONEST LIMIT, stated because the whole bug came from overclaiming: this
    proves the RTC is PRESENT and READABLE. It cannot prove the RTC HOLDS across
    a power cut, which is the only property that actually matters and the one
    that needs a battery. A Pi 5 with a flat cell reads perfectly while powered
    and comes up at 1970 the moment you unplug it.

    Reads sysfs rather than `hwclock -r`: no sudo, no util-linux-extra."""
    if not os.path.exists("/dev/rtc0"):
        return False, "/dev/rtc0 is not present"
    try:
        with open("/sys/class/rtc/rtc0/since_epoch", encoding="utf-8") as fh:
            epoch = int(fh.read().strip())
    except (OSError, ValueError):
        return False, "/dev/rtc0 exists but its time could not be read"
    # 2020-01-01. An RTC that lost power reads 1970 (or 2000 on some chips), and
    # that is exactly the state we must not mistake for a working clock.
    if epoch < 1577836800:
        return False, f"RTC reads {epoch} — it has lost power (no battery, or flat)"
    return True, f"RTC present and reads a plausible date (epoch {epoch})"


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


def report_fallback():
    """Say plainly whether this box can still tell the time if the RTC fails."""
    ok, detail = rtc_is_working()
    installed, enabled = fake_hwclock_state()
    if ok and installed and enabled:
        _ok("RTC readable, and fake-hwclock is still in place as a fallback.")
    elif ok:
        _warn("RTC reads fine, but fake-hwclock is not active. That is only safe "
              "once you have PROVEN the RTC holds across a power cut — pull the "
              "power, wait a minute, boot with no network and check the date. "
              "Until then:  sudo systemctl unmask fake-hwclock && "
              "sudo systemctl enable --now fake-hwclock")
    elif installed and enabled:
        _warn(f"No usable RTC ({detail}) — fake-hwclock is carrying the clock.")
    else:
        _fail(f"NO TIME SOURCE OFFLINE: {detail}, and fake-hwclock is not active. "
              "This box will boot with a stale date whenever it has no network. "
              "Fix with:  sudo systemctl unmask fake-hwclock && "
              "sudo systemctl enable --now fake-hwclock")


def run(check_only=False, board_id=DEFAULT_BOARD, charge_uv=None):
    board = BOARDS[board_id]
    print(f"\n  OASIS — enable-rtc  ({board['label']})")
    _hr()
    check_platform()
    # The Pi 5's RTC is in the SoC: there is nothing to configure on a board
    # that does not have one, and a config.txt line naming it would be noise at
    # best. Probed from the driver's own attribute, not the model string.
    if board_id == "pi5" and not is_pi5_rtc():
        _fail("No built-in RTC found (/sys/class/rtc/rtc0/battery_voltage is "
              "absent) — this feature is for the Raspberry Pi 5. For an add-on "
              "RTC use the Witty Pi or BigTreeTech feature instead.")
    overlay = f"dtparam=rtc_bbat_vchg={charge_uv}" if charge_uv else None
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
    # ORDER MATTERS, and the old order cost two stations their clocks.
    #
    # This step used to remove fake-hwclock SECOND — before the RTC could
    # possibly work. The config.txt block written here does nothing until a
    # reboot, and `hwclock` (the tool you would verify with) was not installed
    # until the step AFTER the removal. So a box whose RTC turned out to be
    # absent, unseeded or battery-less was left with no timekeeping at all:
    # strictly worse than before this feature ran. pi4oasis ended up with no
    # /dev/rtc0 and a neutralised fallback, booting three weeks stale.
    #
    # The fallback is no longer surrendered here AT ALL. It is cheap insurance
    # whose worst case is a clock set to the last saved time and corrected
    # seconds later by RTC/GPS/NTP, against a failure mode that is silent and
    # unbounded. `verify()` reports the interaction once the RTC is real.
    _step(1, "Ensuring hwclock is installed");            ensure_hwclock()
    _step(2, "Writing the config.txt RTC block");         write_config(cfg, board_id, overlay)
    _step(3, "Neutralising hwclock-set --systz");         patch_hwclock_set()

    _hr()
    report_fallback()
    print(f"\n  {board['label']} RTC configured — REBOOT to load it.")
    _info("After reboot, once the clock is correct (GPS/NTP):  sudo hwclock -w")
    if board["bus"] is not None:
        _info(f"Verify:  sudo hwclock -r   and   i2cdetect -y {board['bus']}   "
              f"(the chip shows as UU at {board['addr']})")
    else:
        mv = pi5_battery_millivolts()
        _info("Verify:  sudo hwclock -r")
        if mv == 0:
            _warn("No backup cell detected (battery_voltage reads 0). The RTC "
                  "will hold nothing across a power cut until one is fitted — "
                  "which is the whole point of configuring it.")
        elif mv is not None:
            _ok(f"Backup cell detected: {mv} mV.")
        if charge_uv:
            _warn(f"Trickle charging ENABLED at {charge_uv/1e6:.1f} V. This is "
                  "correct for a rechargeable ML2032/LIR2032 and MUST NOT be "
                  "used with a primary CR2032.")
        else:
            _info("Trickle charging is OFF (the safe default). A CR2032 holds "
                  "the RTC for years uncharged; pass --rechargeable only if the "
                  "fitted cell is an ML2032/LIR2032.")
    print()
