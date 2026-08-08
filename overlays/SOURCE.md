# Vendored device-tree overlays

Compiled `.dtbo` blobs OASIS ships because the stock Raspberry Pi OS image does
not provide a working one. Installed by `common/overlays.py` — one lookup, one
install path, used by every feature that needs an overlay.

A `.dtbo` is compiled *data*, not code: it is architecture-independent and
portable across kernel versions, which is why one committed binary is safe and
durable here (unlike a `.ko`, which is locked to an exact kernel build).

Committing binaries is against this repo's grain, so each one states below where
it came from and how to rebuild it. **If you cannot rebuild it from what is
written here, that is a bug in this file.**

## How these are applied, and how to stop applying them

Two steps, in this order:

1. **Fill a gap.** If the OS ships no overlay of that name, install ours. Nothing
   is displaced, so there is nothing to weigh.
2. **Replace only on evidence.** If the OS ships one, leave it alone. The
   installers write `dtoverlay=draws`, reboot, and re-check; only if the hardware
   *still* has not enumerated do they conclude the loaded overlay is at fault and
   swap ours in.

The rejected alternative was a board/kernel guard ("if Pi 4, override"). It reads
as simpler and is worse: it freezes today's snapshot into code, so the day the
Pi 4 overlay is fixed upstream we would keep overriding it forever and nobody
would notice, because ours works too. "Did the hardware come up?" stays true
without maintenance, and self-heals on boards nobody has tested yet.

Whatever is displaced is kept beside it as `<name>.dtbo.oasis-orig`, and
`overlays.restore()` puts it back. Overwriting a stock file with no record of it
is how a `config.txt` edit once left a Pi with no sound cards after an uninstall.

**To stop overriding an overlay, delete its `.dtbo` from this directory.** The
installer then reports "none vendored — using the OS copy" and leaves the
system's own file alone. That is the intended path once Raspberry Pi OS ships a
kernel whose overlays work: drop ours, and boxes fall back on the next install.
Already-overridden boxes are restored from the `.oasis-orig` backup.

---

## `draws.dtbo`, `udrc.dtbo` — DRAWS HAT (NW Digital Radio)

**Why vendored — and how narrow that reason is.** On a **Pi 4 (BCM2711) running
kernel 6.18.34**, the shipped overlay did not bring the HAT up: neither the
TLV320AIC3204 codec at 0x18 nor the SC16IS752 UART bound. A self-compiled
overlay fixed both, which is what removed the Bookworm-vs-Trixie blocker and let
one box run DRAWS *and* `librtlsdr ≥ 2.0`.

**This is NOT a statement about Trixie.** Verified 2026-08-08: a **Pi 5 (BCM2712)
on 6.18.39+rpt-rpi-2712** drives the HAT correctly with the overlay Raspberry Pi
ships — GPS and all. So the failure belongs to that kernel and/or that board, not
to the distribution, and OASIS does **not** override an overlay that works.

See *How these are applied* above: the installers fill a gap, and only replace
the OS's overlay after the hardware has demonstrably failed to enumerate with
it.

**Built:** 2026-08-06, on the bench Pi (`pi4draws`), kernel 6.18.34, 64-bit
Pi OS Trixie. RF-verified end to end (APRS + Winlink round trip) on 2026-08-07.

**Rebuild:**

```bash
# 1. Tools
sudo apt update
sudo apt install git bc bison flex libssl-dev make device-tree-compiler

# 2. Shallow clone (add -b rpi-6.6.y or similar to pin a branch)
git clone --depth 1 https://github.com/raspberrypi/linux
cd linux

# 3. Configure so the DT include paths resolve
make bcm2711_defconfig      # 64-bit: Pi 4 / CM4  (BCM2711)
# make bcm2712_defconfig    # 64-bit: Pi 5          (BCM2712)
# make bcmrpi3_defconfig    # 32-bit: older Pi

# 4. Build ONLY the device trees + overlays (minutes, not hours)
make dtbs

# 5. Collect the results. The overlay output directory moves between kernel
#    versions — look under arch/*/boot/dts/overlays/ rather than assuming:
find arch -name 'draws.dtbo' -o -name 'udrc.dtbo'
```

Copy the two files into this directory to re-vendor them. The installer puts
them in place; you do not copy them to `/boot` by hand.

**Licence: GPL-2.0 — NOT OASIS's MIT licence.** See the offer at the end of this
file. `draws.dtbo` and `udrc.dtbo` are compiled from the device-tree sources in
the Raspberry Pi Linux kernel tree and carry that tree's licence. **No OASIS
patch is applied** — they are the upstream sources, built unmodified.

---

## `m5stack-cm4.dtbo` — M5Stack CM4Stack panel

**Why vendored:** the stock Pi image does not ship it at all, and a bundle built
without network would otherwise have no copy. Upstream publishes a working
binary, so `scripts/create-oasis-offline.py` also downloads it at
**bundle-build time** (build may use the network; runtime never does) from:

    github.com/m5stack/m5stack-linux-dtoverlays — overlays/cm4stack/bin/

Committed here so a from-scratch install works with no network at any point.
The build-time fetch refreshes it; deleting the file falls back to that fetch,
and deleting both falls back to whatever the OS provides.

**Rebuild:** not built by us — take the published binary from the URL above.

**Licence:** redistributed as published by M5Stack, unmodified. Its terms are
whatever that repository states; OASIS asserts nothing about them and adds no
licence of its own. **Confirm the upstream terms before relying on
redistribution** — this file records what we know, not a legal opinion.

---

## Written offer for corresponding source (GPL-2.0)

This applies to `draws.dtbo` and `udrc.dtbo`. They are **not** covered by the MIT
licence OASIS itself uses: they are compiled from GPL-2.0 device-tree sources in
the Raspberry Pi Linux kernel, and redistributing a binary built from that source
carries an obligation to make the corresponding source available.

**The corresponding source** is the Raspberry Pi Linux kernel tree at
<https://github.com/raspberrypi/linux>, unmodified. OASIS applies no patch of any
kind; the exact procedure that produced these binaries — tools, clone, defconfig,
`make dtbs` — is written out above, so anyone can reproduce them from that public
tree.

**On request**, the OASIS maintainer will supply the complete corresponding
source for these binaries, on a medium customarily used for software interchange,
for no more than the cost of physically performing the distribution. Ask via the
project's issue tracker.

**If you would rather not receive them at all:** delete the `.dtbo` files from
this directory. The installer then reports "none vendored — using the OS copy"
and leaves the system's own overlays alone, and any box already overridden is
restored from its `.oasis-orig` backup. Nothing about OASIS requires you to use
the blobs we ship.

*(This is a plain-language statement of intent and practice, not legal advice.)*
