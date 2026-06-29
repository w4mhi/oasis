# Hardware RTC (Witty Pi 3 / DS3231) on OASIS

Giving the `oasis` Pi a **battery-backed hardware clock** so it knows the correct
time the instant it powers on — with **no network and no GPS lock yet**. This is
the steady baseline that GPS + `chrony` then fine-tune; without it, a Pi boots
believing it is whenever it last shut down, which breaks FT8/WSPR/SSTV decode
windows, log timestamps, and TLS-free time-sensitive tooling.

Configured by **`scripts/enable-rtc.py`**.

*Targets the UUGear **Witty Pi 3** (Rev1/Rev2). Any DS3231-based RTC HAT works
with the same overlay.*

---

## Why a Pi needs this

A Raspberry Pi has **no real-time clock of its own** — no coin cell, nothing that
ticks while it's powered off. With internet it papers over this by pulling NTP at
boot. Off-grid there is no NTP, so on every cold start the clock is simply wrong
until something corrects it. GPS fixes it *eventually* (once enough satellites
lock — tens of seconds to minutes from cold), but during that window the station
is timestamping with a bogus clock.

A hardware RTC closes that gap: it keeps accurate time from a small backup battery
across reboots and total power loss, so the clock is right **immediately** at
boot. Order of trust, cold to warm:

```
RTC (instant, ±2 ppm)  →  GPS lock (seconds–minutes)  →  chrony disciplines from GPS
```

---

## Hardware

The **Witty Pi 3** is primarily a power-management board, but it carries a
**DS3231SN** temperature-compensated RTC on the Pi's I²C bus. OASIS configures
**only the RTC**; the Witty Pi power-scheduling software is separate and out of
scope here.

| Item | Value | Notes |
|------|-------|-------|
| RTC chip | **DS3231SN** | TCXO, ±2 ppm — far more stable than a bare DS1307 |
| I²C address | **`0x68`** | Standard DS3231 address |
| Backup battery | CR2032 (on the board) | Keeps the RTC running with the Pi off |
| Kernel overlay | `i2c-rtc,ds3231` | Creates `/dev/rtc0` at boot |
| Bus | I²C-1 (GPIO 2 SDA / GPIO 3 SCL) | Same bus the codec/OLED HATs use |

> ℹ️ **Pi 5 has its own RTC** (with a battery header on-board). On a Pi 5 you may
> not need the Witty Pi RTC at all — check `cat /proc/device-tree/model` and
> `ls /dev/rtc*` before adding an external one.

---

## Gotchas (what bites)

1. **`fake-hwclock` will fight you.** Raspberry Pi OS ships `fake-hwclock`, which
   saves the time to a file on shutdown and restores it at boot — and it will
   happily **overwrite your real RTC** with a stale file timestamp. It must be
   removed/disabled, or the DS3231 is pointless.
2. **The `--systz` reset in `/lib/udev/hwclock-set`.** At boot, udev runs
   `hwclock-set`, whose `--systz` branch can *re-set* the system clock in a way
   that clobbers a freshly-read DS3231. This is the classic "my DS3231 keeps
   getting reset at boot" trap. The fix is to comment that branch out.
3. **`/dev/rtc0` only appears after a reboot.** The overlay loads at boot, so the
   RTC isn't live the moment the script finishes — you **must reboot**.
4. **You have to seed the RTC once.** A brand-new (or dead-battery) DS3231 holds
   garbage. After the first good time sync, write it with `sudo hwclock -w`.
5. **Edit the *active* `config.txt`.** On Bookworm/Trixie the live file is
   `/boot/firmware/config.txt`; editing the old `/boot/config.txt` does nothing.
   `enable-rtc.py` auto-detects the right one.

---

## What `enable-rtc.py` does

Idempotent and safe to re-run — **requires a reboot**:

```bash
python3 scripts/enable-rtc.py            # configure the DS3231 RTC (then reboot)
python3 scripts/enable-rtc.py --check     # report status, change nothing
```

| Step | Action | Why |
|------|--------|-----|
| 1 | Adds `dtparam=i2c_arm=on` to `config.txt` | Enable the I²C bus the RTC lives on |
| 2 | Adds `dtoverlay=i2c-rtc,ds3231` to `config.txt` | Load the DS3231 driver → `/dev/rtc0` at boot |
| 3 | Removes / disables `fake-hwclock` | Stop it overwriting the real RTC |
| 4 | Comments the `--systz` line in `/lib/udev/hwclock-set` | Stop the boot-time clock reset (backs the file up once to `hwclock-set.oasis.bak`) |

Each step is a no-op if already applied, so re-running is harmless.

---

## First-time setup

```bash
# 1 · Configure (writes config.txt + neutralises fake-hwclock / hwclock-set)
python3 scripts/enable-rtc.py

# 2 · Reboot so the DS3231 overlay loads
sudo reboot

# 3 · After reboot, get the system clock correct ONCE (GPS or a one-off NTP sync),
#     then write it into the RTC:
sudo hwclock -w        # system clock  →  RTC
sudo hwclock -r        # read the RTC back to confirm
```

From then on the RTC seeds the system clock at every boot, and — if you also set
up [GPS time sync](SETUP.md#gps-time-sync-gpsd--chrony) — `chrony` disciplines
the system clock from GPS while it's running.

---

## Verification

```bash
# Overlay loaded?  /dev/rtc0 should exist after the reboot.
ls -l /dev/rtc*

# Is the DS3231 on the bus?  Expect 'UU' at 0x68 (a driver has claimed it),
# or '68' if the overlay isn't loaded yet.
sudo i2cdetect -y 1

# Read the RTC and compare to the system clock — they should match.
sudo hwclock -r
date

# Confirm fake-hwclock is gone/disabled (should NOT be 'active'/'enabled').
systemctl status fake-hwclock 2>/dev/null || echo "fake-hwclock not present — good"
```

`python3 scripts/enable-rtc.py --check` summarises the same: whether I²C is on,
whether the overlay line is present, and what the RTC currently reads.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/dev/rtc0` missing after reboot | Overlay not in the active `config.txt`, or no reboot yet | Re-run `enable-rtc.py`; confirm `dtoverlay=i2c-rtc,ds3231` is in `/boot/firmware/config.txt`; **reboot** |
| `i2cdetect` shows `--` everywhere | RTC not on the bus / HAT not seated / I²C off | Reseat the Witty Pi on all 40 pins; confirm `dtparam=i2c_arm=on`; reboot. A blank grid is almost always physical contact |
| `i2cdetect` shows `68` (not `UU`) | Chip present but the driver hasn't claimed it | The overlay didn't load — check the overlay line is uncommented in the **active** `config.txt`, reboot |
| Clock is wrong again after every reboot | `fake-hwclock` still active, or the `--systz` reset wasn't neutralised | Re-run `enable-rtc.py` (steps 3–4); verify `systemctl is-enabled fake-hwclock` is **not** enabled |
| `hwclock -r` returns a clearly bogus date | RTC never seeded, or dead backup battery | Get the system clock correct, then `sudo hwclock -w`. If it won't hold across a power-off, replace the CR2032 |
| `hwclock: ... can't open /dev/rtc0` | Overlay not loaded (see row 1) | Fix the overlay/reboot first |

> 🔎 **Capture this when asking for help:**
> `ls /dev/rtc*`, `sudo i2cdetect -y 1`, `sudo hwclock -r`, `date`, and
> `grep -nE 'i2c_arm|i2c-rtc' /boot/firmware/config.txt`.

---

## Reverting

```bash
# Remove the two lines from the active config.txt (or comment them):
#   dtoverlay=i2c-rtc,ds3231
#   dtparam=i2c_arm=on        # only if nothing else needs I²C

# Restore the original hwclock-set if needed:
sudo cp /lib/udev/hwclock-set.oasis.bak /lib/udev/hwclock-set

# (Optional) bring fake-hwclock back:
sudo apt install -y fake-hwclock
sudo reboot
```

---

## Reference

- **DS3231 datasheet** — Maxim/Analog Devices (TCXO RTC, ±2 ppm, I²C `0x68`).
- **Raspberry Pi RTC overlay** — `i2c-rtc` in
  `/boot/firmware/overlays/README` (`dtoverlay=i2c-rtc,ds3231`).
- **UUGear Witty Pi 3** — power-management + DS3231 RTC HAT. OASIS configures
  only the RTC; the power-scheduling software is separate.
- Pairs with [GPS time sync (gpsd + chrony)](SETUP.md#gps-time-sync-gpsd--chrony)
  — the RTC is the cold-start baseline, GPS is the running discipline source.
