"""
timekeeping.py — can this station tell the time with no internet?

That is the only question worth asking of an offline-first box, and until now
nothing asked it. Two live stations were found booting weeks stale — one three
days, one twenty-six — with every service reporting healthy, because each part
was individually fine and no one owned the whole.

THREE THINGS CAN CARRY THE CLOCK OFFLINE, and a station needs at least one:

  1. a hardware RTC that HOLDS across a power cut (needs a battery)
  2. fake-hwclock, which saves the time at shutdown and restores it at boot
  3. chrony steering from a GPS source it actually TRUSTS

Each is probed as a CAPABILITY, never as an artifact — this whole area went
wrong by reading artifacts. /dev/rtc0 exists on a Pi 5 with a flat cell.
/dev/pps0 exists with no pulse on the wire. fake-hwclock reports `ii` to dpkg
while masked to /dev/null. A GPS with a 3D fix can be feeding chrony samples
that chrony discards. In every one of those the artifact says yes and the
capability says no.

WHAT NONE OF THIS CAN PROVE: that an RTC holds across a power cut, which is the
property that actually matters. A cell reads perfectly while the Pi is powered.
Only pulling the plug proves it, so `rtc_holds()` is named for what it hopes and
documented for what it knows.
"""

import re

from common import rtc
from common.oasis_lib import _ok, _info, _warn, _run, sudo_apt_cmd

FAKE_HWCLOCK_UNIT = "fake-hwclock"


def rtc_holds():
    """(ok, detail) — is there a hardware RTC that carries the clock offline?

    EVIDENCE ORDER: outcome first, proxy never.

    The strongest proof an RTC held across a power cut is that the kernel used
    it to SEED the system clock at boot (hctosys=1, "setting system clock to
    ..." in dmesg). That happens before any network or GPS exists, so a correct
    date there cannot have come from anywhere else.

    battery_voltage is NOT used to judge, and this is the second time that rule
    has had to be learned. It reads 0 on a Pi 5 with a perfectly good
    non-rechargeable cell, because the measurement is tied to the trickle
    CHARGING configuration and charging is correctly disabled for such a cell.
    server/routes/system.py:_rtc_state() already documented this; this function
    did not, and asserted "NO BACKUP CELL is fitted" on pi5draws — a box whose
    kernel log shows the RTC seeding a correct clock 14 seconds before chrony
    reached its first NTP server. A non-zero reading is informative; a zero is
    silence, and silence is not evidence of absence.

    Falls back to rtc.rtc_is_working() and inherits its honest limit: present
    and readable is not the same as holds-across-a-power-cut."""
    ok, detail = rtc.rtc_is_working()
    if not ok:
        return False, detail
    if rtc.set_system_clock_at_boot():
        return True, ("RTC carried the clock across the last power cycle — the "
                      "kernel seeded the system time from it at boot, before "
                      "any network or GPS")
    # Present and plausible, but never observed seeding the clock. Real, and
    # weaker; say so rather than either claiming or denying that it holds.
    return True, detail + " (not yet observed seeding the clock at boot)"


def fake_hwclock_ready():
    """(ok, detail) — installed AND actually enabled.

    Both halves, because pi4oasis had the package installed with its unit
    symlinked to /dev/null: `systemctl is-enabled` answered "not-found" and it
    read as absent. Present and inert is the same as missing for timekeeping,
    and a completely different repair."""
    installed, enabled = rtc.fake_hwclock_state()
    if installed and enabled:
        return True, "fake-hwclock is installed and enabled"
    if installed:
        return False, "fake-hwclock is installed but disabled or masked"
    return False, "fake-hwclock is not installed"


# chronyc marks each source with a mode char then a state char. Refclocks are
# '#'; '*' is the selected source and '+' a combined one. Anything else means
# chrony is NOT steering from it: 'x' falseticker, '?' unreachable, '-' excluded.
# A GPS that delivers samples chrony throws away is not a time source, and that
# distinction is the entire point of reading the state rather than the presence.
_TRUSTED_REFCLOCK = re.compile(r"^#[*+]")


def gps_disciplined():
    """(ok, detail) — is chrony actually steering from a local refclock?"""
    try:
        r = _run(["chronyc", "-n", "sources"], check=False, capture_output=True, text=True)
    except OSError:
        return False, "chrony is not installed"
    out = getattr(r, "stdout", "") or ""
    if not out.strip():
        return False, "chrony is not running or has no sources"
    refclocks = [ln for ln in out.splitlines() if ln.startswith("#")]
    if not refclocks:
        return False, "chrony has no local refclock (no GPS/PPS configured)"
    if any(_TRUSTED_REFCLOCK.match(ln) for ln in refclocks):
        return True, "chrony is steering from a local refclock"
    return False, ("a GPS refclock is configured but chrony is not using it "
                   "(falseticker, unreachable, or excluded)")


def offline_clock_report():
    """(ok, sources, summary) — everything the station has to keep time offline.

    `sources` is [(name, ok, detail)] so a caller can show the whole picture:
    which one is carrying the clock, and what the others would need."""
    sources = [
        ("Hardware RTC", *rtc_holds()),
        ("fake-hwclock", *fake_hwclock_ready()),
        ("GPS-disciplined chrony", *gps_disciplined()),
    ]
    working = [name for name, ok, _ in sources if ok]
    if working:
        return True, sources, "carried by " + ", ".join(working)
    return False, sources, ("NOTHING can set this clock without the internet — "
                            "it will boot with a stale date at the worst moment")


def ensure_fake_hwclock(dry_run=False):
    """Make fake-hwclock the floor under the clock when nothing else holds it.

    Called AFTER the RTC step on purpose: a box that ended up with a working RTC
    does not need it, and a box that did not must never be left with nothing.
    That ordering is the whole fix — the old code surrendered the fallback
    BEFORE the RTC could possibly work.

    Handles installed-but-masked, not just missing. A plain `apt install` on
    pi4oasis would have reported success and changed nothing, because the
    package was already there with its unit pointed at /dev/null."""
    rtc_ok, rtc_detail = rtc_holds()
    if rtc_ok:
        _ok(f"Hardware RTC carries the clock ({rtc_detail}) — fake-hwclock not needed.")
        return True

    installed, enabled = rtc.fake_hwclock_state()
    if installed and enabled:
        _ok("fake-hwclock already installed and enabled — the clock has a floor.")
        return True

    _info(f"No RTC holding the clock ({rtc_detail}) — ensuring fake-hwclock.")
    if dry_run:
        _info("(dry run) would install/unmask/enable fake-hwclock.")
        return True

    if not installed:
        _run(sudo_apt_cmd("apt", "install", "-y", FAKE_HWCLOCK_UNIT), check=False)
    # Unmask FIRST: a masked unit cannot be enabled, and masking is exactly the
    # state the old RTC step left behind.
    _run(["sudo", "systemctl", "unmask", FAKE_HWCLOCK_UNIT], check=False)
    _run(["sudo", "systemctl", "enable", "--now", FAKE_HWCLOCK_UNIT], check=False)

    installed, enabled = rtc.fake_hwclock_state()
    if installed and enabled:
        _ok("fake-hwclock installed and enabled — the clock survives a reboot.")
        return True
    _warn("Could not enable fake-hwclock. With no RTC either, this box will boot "
          "with a stale date whenever it has no network. Fix with:  "
          "sudo apt install fake-hwclock && sudo systemctl unmask fake-hwclock "
          "&& sudo systemctl enable --now fake-hwclock")
    return False
