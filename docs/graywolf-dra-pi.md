# GrayWolf + DRA-Pi-Zero on OASIS

Getting the **MastersCommunications DRA-Pi-Zero** radio interface working with
**GrayWolf** on the `oasis` Pi. This is the known-good configuration plus a
helper to light the green RX LED that GrayWolf does not drive on its own.

*Verified 2026-06-17 on `oasis`.*

---

## Hardware

The DRA-Pi-Zero (REV2) is **not** a USB sound card. It carries a **Wolfson
WM8731 I²S codec** and is electrically compatible with the **AudioInjector
Zero** sound card. It presses onto the Pi's 40-pin header (remove any SoC
heatsink first).

| Signal | Header pin | BCM GPIO | Notes |
|--------|-----------|----------|-------|
| PTT (red LED) | 32 | GPIO 12 | Drives a relay — GrayWolf keys this |
| Carrier Detect (green "RX" LED) | 36 | GPIO 16 | Software-driven; **not** driven by audio |
| Status (blue LED) | 29 | GPIO 5 | hardware-specific |
| Audio | — | I²S (GPIO 18–21) | WM8731 codec |

---

## Gotchas (what cost the time)

1. **It's an I²S WM8731 card, not USB** — needs the AudioInjector overlay, not
   a `dwc2` USB-host fix.
2. **RX audio is wired to the codec's `Mic` input, not `Line In`.** This was the
   killer. With `Input Mux = Line In` the capture meter sits dead-flat at 0 even
   with the radio receiving. The fix is `Input Mux = Mic`.
3. **GrayWolf needs `plughw` + a name-based device.** Plain `hw:` is
   exact-match/exclusive and opens silent; the card number also shifts (e.g.
   after disabling HDMI) so reference it by name.
4. **PTT is the Pi's GPIO 12**, not C-Media `CM108`. That `PTT CM108` advice is
   for the *USB* DRA models, not this one.
5. **The green LED is GPIO 16, software-driven.** With Direwolf,
   `DCD GPIO 16` blinks it. GrayWolf has no DCD→GPIO feature, so the pin stays
   dark unless you drive it yourself (see below).

---

## Working configuration

### 1. `/boot/firmware/config.txt`

```ini
# --- I2C / SPI buses ---
dtparam=i2c_arm=on    # enable ARM I2C (GPIO 2/3); the WM8731's control interface rides on I2C
dtparam=spi=on        # enable SPI (GPIO 7-11); NOT used by the DRA — here for other OASIS peripherals

# --- hand the I2S audio bus to the WM8731 ---
dtparam=audio=off                       # disable default Pi (PWM/HDMI) audio so it can't grab the I2S bus
dtoverlay=i2s-mmap                      # enable mmap DMA on the I2S bus; only needed by JACK/mmap apps — harmless to keep
dtoverlay=vc4-kms-v3d,noaudio           # KMS graphics, but HDMI audio OFF — frees I2S + drops the vc4hdmi cards
dtoverlay=audioinjector-wm8731-audio    # THE card: loads & clocks the WM8731 I2S codec (creates 'audioinjectorpi')
```

Key points:

- `dtparam=audio=off` + `vc4-kms-v3d,noaudio` together clear the I²S bus — that's
  why the `vc4hdmi` cards vanish and the WM8731 lands on `card 0`.
- `dtoverlay=audioinjector-wm8731-audio` is the only *required* line for the card;
  it creates and clocks the codec.
- `dtoverlay=i2s-mmap` is optional — GrayWolf uses normal read/write ALSA access,
  so it only matters if a JACK/mmap app also uses the bus. Harmless to keep.
- `dtparam=spi=on` is unrelated to the DRA — it's for other OASIS hardware.

### 2. ALSA mixer — the WM8731 routing

The codec boots with capture unrouted. These settings mirror the known-good
this hardware configuration. **The critical line is `Input Mux = Mic`.** The card is usually
`card 2` with HDMI enabled, `card 0` with HDMI off — use the name
`audioinjectorpi`, not the number.

```bash
CARD=audioinjectorpi

# RX path — radio audio arrives on the MIC input
amixer -c "$CARD" sset 'Input Mux' 'Mic'
amixer -c "$CARD" sset 'Mic' cap
amixer -c "$CARD" sset 'Line' nocap
amixer -c "$CARD" sset 'Mic Boost' 0
amixer -c "$CARD" sset 'Capture' 100%
amixer -c "$CARD" sset 'ADC High Pass Filter' on

# TX path
amixer -c "$CARD" sset 'Output Mixer HiFi' on
amixer -c "$CARD" sset 'Master' 98%
amixer -c "$CARD" sset 'Sidetone' 100%

sudo alsactl store
```

Persist across reboots:

```bash
sudo alsactl store
sudo systemctl is-enabled alsa-restore   # should say "enabled"
```

### 3. GrayWolf audio source

| Field | Value |
|-------|-------|
| `source_type` | `soundcard` |
| `source_path` | `plughw:CARD=audioinjectorpi,DEV=0` |
| `direction` | `input` |
| `channels` | `1` |
| `input_channel` | `0` |
| `sample_rate` | `48000` |
| `format` | `s16le` |
| `gain_db` | `0` |

`plughw` (not `hw`) lets ALSA convert formats so GrayWolf opens the card; the
`CARD=` name survives renumbering.

> ⚠️ **Restart GrayWolf after adding/changing the device or channel:**
> `sudo systemctl restart graywolf`. GrayWolf applies channel config when the
> modem **starts** — a device/channel created in the web UI at runtime isn't live
> until a restart, and the modem can keep showing `state=RUNNING` on the old
> config while nothing actually works. Restart, then it picks up the new channel.

### 4. GrayWolf PTT

| Field | Value |
|-------|-------|
| `method` | `gpio` |
| `device` | `/dev/gpiochip0` |
| `gpio_line` | `12` |
| `invert` | `false` |

`gpio_line` is a kernel line offset (verify with `gpiofind GPIO12`); it equals
BCM 12 on Pi 3 / Pi 4, but re-check on a Pi 5.

---

## Verification

```bash
# RX — open the radio's squelch so there's constant hiss, then watch the meter
arecord -D plughw:CARD=audioinjectorpi,DEV=0 -c 2 -f S16_LE -r 48000 -V stereo -d 15 /dev/null
# Good RX reads roughly +6 to +10 on the VU bars.

# TX — trigger transmit in GrayWolf: red LED lights + radio keys.
```

If the RX meter is flat: confirm `Input Mux` is `Mic` (`amixer -c audioinjectorpi
sget 'Input Mux'`), and that the squelch is actually open.

---

## Troubleshooting — the card doesn't appear

`enable-dra-pi.py` picks its phase from whether the card is present
(`audioinjector` in `/proc/asound/cards`). If after a reboot it still prints
*"DRA-Pi sound card not detected"*, or `aplay -l` never lists `audioinjectorpi`,
work down this ladder — each rung tells you where the chain broke.

```bash
# 0 · Did the Pi actually reboot after the config phase? The codec only
#     enumerates at boot, so a fresh uptime is required.
uptime

# 1 · Is the OASIS block in the ACTIVE config.txt? On Bookworm/Trixie the live
#     file is /boot/firmware/config.txt — editing /boot/config.txt does nothing.
grep -nE 'audioinjector|dtparam=audio|vc4-kms|i2s-mmap|i2c_arm' /boot/firmware/config.txt

# 2 · Does the overlay binary exist for THIS kernel? No .dtbo = card can't load.
ls /boot/firmware/overlays/ | grep -i audioinjector

# 3 · Did the overlay load and the WM8731 probe cleanly? (the decisive one)
sudo dmesg | grep -iE 'wm8731|audioinjector|asoc|i2s|simple-card' | tail -40

# 4 · Is the WM8731 answering on its I²C control bus? Expect 1a (sometimes 1b).
sudo apt-get install -y i2c-tools   # if i2cdetect is missing
sudo i2cdetect -y 1

# 5 · What does ALSA actually see now?
cat /proc/asound/cards ; echo '---' ; aplay -l ; arecord -l
```

### Reading the results

| What you see | Likely cause | Fix |
|---|---|---|
| Step 1 prints nothing / no OASIS block | edited the wrong file, or the script wrote `/boot/config.txt` while the Pi boots `/boot/firmware/config.txt` | re-run `python3 scripts/enable-dra-pi.py --config-only`; confirm it reports `Boot config: /boot/firmware/config.txt`; **reboot** |
| Step 1 still shows an active `dtparam=audio=on` | on-board audio is holding the I²S bus | make sure that line is commented and `dtparam=audio=off` is present (the script does this); reboot |
| Step 2 lists no `audioinjector*.dtbo` | overlay not shipped on this kernel/image | `sudo apt update && sudo apt full-upgrade` to refresh `linux-image`/overlays, or install the AudioInjector overlay manually (see Reference); reboot |
| Step 3 shows `wm8731 1-001a: Failed to issue reset: -110` then `probe … failed with error -110` and `deferred probe pending` | overlay loaded and the codec device exists, but its **I²C control write timed out** (`-110` = `ETIMEDOUT`) — contact or I²C clock-stretching, **not** config. The `supply … dummy regulator` lines above it are normal | 1) power off and **reseat** the HAT on all 40 pins (SDA/SCL = pins 3/5; remove a heatsink that lifts the board). 2) still failing → slow the bus: add `dtparam=i2c_arm_baudrate=50000` (then `10000`) to the OASIS block, reboot. 3) on a **Pi 5** the I²C lives on RP1 and may need a different bus/overlay — check `cat /proc/device-tree/model` |
| Step 3 shows `wm8731 … -ENODEV` / `-EREMOTEIO`, or nothing at all | codec not responding — seating/power, or I²C off | power off; reseat the HAT firmly on all 40 pins (remove the SoC heatsink if it fouls the board); confirm `dtparam=i2c_arm=on`; reboot |
| Step 4 shows `--` everywhere (no `1a`) | the WM8731 isn't on the bus — hardware/seating, or I²C disabled | reseat the board; verify `dtparam=i2c_arm=on`. A blank grid here is almost always physical contact, not software |
| Step 4 shows `UU` at `1a` | a driver already claimed the codec — **this is good** | proceed; the card should show in step 5 |
| Step 5 lists `audioinjectorpi` | it's working — the earlier "not detected" was a stale check | run `python3 scripts/enable-dra-pi.py --mixer-only` to apply the RX/TX routing |

### Still nothing?

- **Confirm the board revision / overlay.** This doc targets the **REV2** WM8731
  board using `dtoverlay=audioinjector-wm8731-audio`. The USB `CM108` DRA models
  do **not** use this overlay at all — they enumerate as a USB sound card with no
  `config.txt` change. Check which board you actually have.
- **Rule out a heatsink or standoff shorting/lifting the header** — a classic for
  press-on HATs.
- **Capture the full overlay + audio boot view** when asking for help:
  `sudo dmesg | grep -i overlay` and `sudo dmesg | grep -iE 'asoc|snd'`.

---

## Green RX LED solution

GrayWolf does not drive GPIO 16, so the green carrier-detect LED stays dark by
default — even though RX is working. GrayWolf *does* expose a **live WebSocket
packet stream** (and a SQLite history DB at
`/var/lib/graywolf/graywolf-history.db`), so a small helper can pulse the LED on
receive activity. GPIO 16 is free — GrayWolf only uses GPIO 12 (PTT).

### Install it (recommended)

`scripts/enable-dra-rx-led.py` ships this as a systemd service — no manual file
copying. It polls the GrayWolf history DB (the reliable, offline source the APRS
API already uses) and pulses the green LED on each decoded packet:

```bash
python3 scripts/enable-dra-rx-led.py             # write + enable the service
python3 scripts/enable-dra-rx-led.py --self-test  # blink GPIO 16 to verify wiring
python3 scripts/enable-dra-rx-led.py --uninstall  # stop + remove it
```

The daemon waits for the DB to appear, reads it **read-only**, and runs as root
so it can drive the GPIO via `pinctrl`. Override the DB path with `$APRS_DB_PATH`
and the LED pin with `--gpio`. Because it watches the `positions` table, the LED
tracks **decoded** RX (APRS positions), not raw carrier/COS.

### Red TX LED (GPIO 12) — nothing to install

The red LED *is* GrayWolf's PTT line (GPIO 12), keyed automatically whenever
GrayWolf transmits (see §4 *GrayWolf PTT*). It already lights on TX — do **not**
run a second process against GPIO 12, or it will fight GrayWolf for the pin.

### Helper: `dra-rx-led.py` (WebSocket — reference only)

> Kept for reference. This hand-rolled variant uses GrayWolf's WebSocket, whose
> `WS_URL` is an **unconfirmed placeholder** — prefer the script above.

Pulses GPIO 16 (line 16 on `gpiochip0`) each time GrayWolf reports a decoded
packet. The pin is toggled via `pinctrl` to avoid libgpiod binding-version
churn.

> **Confirm the WebSocket URL** against GrayWolf's API reference
> (handbook → *REST API Reference* / *Monitoring*) and set `WS_URL` below. The
> path/port here is a placeholder.

```python
#!/usr/bin/env python3
"""dra-rx-led.py — blink the DRA-Pi-Zero green LED (GPIO 16) on GrayWolf RX."""
import subprocess, time, json
from websocket import create_connection   # pip install websocket-client

WS_URL    = "ws://127.0.0.1:8080/ws/packets"   # CONFIRM in GrayWolf API docs
LED_LINE  = 16          # BCM GPIO 16 = header pin 36 = green CD LED
PULSE_SEC = 0.12

def led(state):
    # 'dh' = drive high (on), 'dl' = drive low (off)
    subprocess.run(["pinctrl", "set", str(LED_LINE), "op", "dh" if state else "dl"])

def main():
    led(False)
    while True:
        try:
            ws = create_connection(WS_URL)
            while True:
                ws.recv()              # any packet event = RX activity
                led(True); time.sleep(PULSE_SEC); led(False)
        except Exception:
            led(False); time.sleep(3)  # reconnect on drop

if __name__ == "__main__":
    main()
```

### systemd unit: `/etc/systemd/system/dra-rx-led.service`

```ini
[Unit]
Description=DRA-Pi-Zero green RX LED (GrayWolf)
After=graywolf.service
Wants=graywolf.service

[Service]
ExecStart=/usr/bin/python3 /home/mihaim/dra-rx-led.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now dra-rx-led.service
```

### Alternatives

- **DB-poll (no API docs needed):** tail
  `/var/lib/graywolf/graywolf-history.db` for new rows and pulse the LED — uses
  the integration already in `scripts/enable-graywolf-api.py`. Higher latency, APRS-only.
- **Audio-presence (COS-style):** drive GPIO 16 from the WM8731 capture level
  instead of GrayWolf events. Fully independent of GrayWolf, but lights on noise
  unless the radio's squelch gates the audio.

---

## Reference

- DRA-Pi-Zero docs (schematic, jumpers, LED pinout):
  <https://www.masterscommunications.com/products/radio-adapter/dra/drapizero_docs.html>
- AudioInjector Zero / WM8731 setup:
  <https://github.com/Audio-Injector/stereo-and-zero>
- GrayWolf handbook: <https://chrissnell.com/software/graywolf/>
