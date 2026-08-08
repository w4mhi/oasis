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

`common/overlays.py` copies each blob into `/boot/firmware/overlays` on **every**
install run, replacing the OS's own file when the bytes differ — an "is it
already there?" check would skip the copy in exactly the case that matters, since
Pi OS ships a `draws.dtbo` that does not work on kernel 6.18.34.

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

**Why vendored:** Raspberry Pi OS **Trixie** ships an overlay that does not bring
the HAT up on kernel **6.18.34** — neither the TLV320AIC3204 codec at 0x18 nor
the SC16IS752 UART binds. A self-compiled overlay from the current Raspberry Pi
kernel source fixes both, which is what removed the Bookworm-vs-Trixie blocker
and let one box run DRAWS *and* `librtlsdr ≥ 2.0` at the same time.

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
make bcm2711_defconfig      # 64-bit: Pi 4 / Pi 5
# make bcmrpi3_defconfig    # 32-bit: older Pi

# 4. Build ONLY the device trees + overlays (minutes, not hours)
make dtbs

# 5. Collect the results. The overlay output directory moves between kernel
#    versions — look under arch/*/boot/dts/overlays/ rather than assuming:
find arch -name 'draws.dtbo' -o -name 'udrc.dtbo'
```

Copy the two files into this directory to re-vendor them. The installer puts
them in place; you do not copy them to `/boot` by hand.

**Licence:** these overlays are built from the Raspberry Pi Linux kernel source
(GPL-2.0). The sources are the upstream tree named above at the commit produced
by the shallow clone; no OASIS patch is applied.

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
