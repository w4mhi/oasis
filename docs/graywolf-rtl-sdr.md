# RTL-SDR → GrayWolf (receive-only APRS) on OASIS

Feeding an **RTL-SDR** dongle into **GrayWolf** as a **receive-only** APRS
monitor / iGate on the `oasis` Pi. The SDR demodulates 2 m APRS to audio with
`rtl_fm`; GrayWolf ingests that audio through its **native `sdr_udp` source** —
no virtual sound card, no ALSA loopback.

> **Status: verified on `oasis` 2026-06-19** (GrayWolf `v0.14.5`, RTL-SDR Blog
> R820T). The modem reaches `state=RUNNING` and ingests SDR audio over
> `sdr_udp` with no `cpal`/ALSA errors. No published GrayWolf-specific `rtl_fm`
> recipe existed before this — the established ones all target *Direwolf*. The
> two things that cost the time are called out below: **`socat -b 1920`** (fat
> datagrams are silently dropped) and **never run "Detect Devices"** (it
> auto-creates a soundcard device the channel binds to, causing a `POLLERR`
> loop).

This is a **separate channel** from the DRA-Pi-Zero setup in
[`graywolf-dra-pi.md`](graywolf-dra-pi.md). An RTL-SDR **cannot transmit** — use
this for RX/monitor/iGate only. The DRA path is the one with TX/PTT. You can run
both at once.

---

## Why not a "virtual audio device"

The intuition is to demodulate with `rtl_fm` and pipe the audio into GrayWolf
through a virtual sound card (ALSA `snd-aloop`, or a PulseAudio null sink). That
is the **Direwolf-era workaround** and it is unnecessary here.

GrayWolf's own handbook (`static/graywolf-handbook/audio.html`) lists four
source types — not just `soundcard`:

| `source_type` | What it is |
|---|---|
| `soundcard` | Physical/virtual ALSA device via CPAL |
| `flac` | A FLAC file |
| `stdin` | Raw S16LE PCM from standard input — "piping audio from another process" |
| **`sdr_udp`** | **UDP listener for SDR audio streams — "software-defined radio input"** |

So GrayWolf accepts the SDR audio directly. No loopback device in between.

---

## Prerequisites

- RTL-SDR tools installed: `python3 scripts/install-rtl-sdr.py` — provides
  `rtl_fm`, `rtl_test`, the feed tools (`socat`, `tcpdump`) and the bench-test
  decoder (`multimon-ng`), and blacklists the conflicting DVB driver. (On a
  minimal/Lite image, `multimon-ng`'s X11/audio deps may pull from apt.)
- A 2 m antenna on the dongle. Verify the device first: `rtl_test -t`.

> ⚠️ **Host matters more than you'd think.** The **Pi 400 is a poor RTL-SDR
> host** — the same dongle/antenna/power that decodes far-off stations on a laptop
> can fail to even enumerate, or hear only the closest stations, on a Pi 400. If
> reception is bad or flaky, suspect the host before the dongle. See
> [debug §6](#6-host-matters--the-pi-400-is-a-poor-sdr-host).

---

## Quick path: `enable-rtl-sdr.py`

[`scripts/enable-rtl-sdr.py`](../scripts/enable-rtl-sdr.py) automates everything
below except the browser steps: it tests the dongle, installs + enables
`aprs-sdr-feed.service`, checks GrayWolf's journal, and prints the web-UI steps.

```bash
python3 scripts/enable-rtl-sdr.py            # test + enable + instructions
python3 scripts/enable-rtl-sdr.py --check    # test the SDR only, no changes
```

The rest of this doc explains what it does and how to do it by hand.

---

## Recommended: `sdr_udp` (decoupled feed)

GrayWolf listens on a UDP port; the SDR feed is a **separate process** you can
restart, retune, or tweak without touching GrayWolf. For an always-on RX station
this is the right shape — when the dongle glitches, only the feed restarts; the
iGate, WebSocket, and history DB stay up.

### 1. The feed command

```bash
rtl_fm -f 144.390M -M fm -s 48000 -g <GAIN> -p <PPM> - \
  | socat -u -b 1920 - UDP-SENDTO:127.0.0.1:7355
```

| Flag | Meaning |
|---|---|
| `-f 144.390M` | 2 m APRS calling frequency (North America). **Keep the `M`** — a bare number is parsed as **Hz**, not MHz (Direwolf's bare-MHz convention does not apply here). |
| `-M fm` | Narrowband FM demodulation |
| `-s 48000` | Output rate **= GrayWolf's `sample_rate`**. Set it directly with `-s`; this build **ignores `-r`** (it kept printing `Output at 200000 Hz`). 48 k is ample for 1200-baud AFSK. |
| `-g <GAIN>` | Tuner gain in dB, e.g. `40` (snaps to 40.2 on the R820T); omit for AGC |
| `-p <PPM>` | Frequency-error correction for your dongle (see caveats) |
| `socat -b 1920` | **Critical.** Caps each UDP datagram at one 20 ms audio chunk (960 samples × 2 B). `socat`'s default 8192-byte lumps are silently dropped by GrayWolf's `sdr_udp` reader → bound socket, packets on the wire, but a dead-flat level meter. `-b 960` (10 ms) also works. |

> **The `Tuned to` banner will read ~144.64–144.69 MHz, not 144.390 — that's
> correct.** `rtl_fm` uses offset tuning: it parks the hardware `fs/4` above the
> requested frequency to dodge the RTL2832 DC spike, then digitally shifts back.
> The demodulated audio is still centered on 144.390. (At `-s 48000` the input
> oversamples to ~1.008 MS/s, so the offset is ~252 kHz.)

### 2. GrayWolf audio source

> **Do NOT click "Detect Devices."** It scans ALSA and auto-creates a
> *soundcard*-type device. The channel then binds to that soundcard instead of
> your UDP source, and the modem busy-loops on
> `cpal input stream error: alsa::poll() returned POLLERR` while your UDP stream
> hits a bound-but-unused socket. **Add the device manually** with **+ Add
> Device**, and delete any soundcard device that appears.

| Field | Value |
|---|---|
| `source_type` | `sdr_udp` |
| `source_path` | `127.0.0.1:7355` |
| `direction` | `input` |
| `channels` | `1` (Mono) |
| `sample_rate` | `48000` |
| `format` | `s16le` |
| `gain_db` | `0` |

Port **7355** (the GQRX UDP-audio convention) is what worked here. GQRX has
shifted that binding across versions, so treat it as a default to verify against
whatever the Add Device form shows — but match `socat`'s target to it.

### 2b. Wire the device into a channel

A device does nothing until a channel uses it for RX. Create an **AFSK 1200 / RX**
channel (mark 1200 Hz, space 2200 Hz) and set its **RX input device to the
`sdr_udp` device** — confirm it's the one with path `127.0.0.1:7355`, *not* a
soundcard.

> ⚠️ **Restart GrayWolf after adding the device/channel:**
> `sudo systemctl restart graywolf`. GrayWolf reads channel config when the modem
> **starts** — a device/channel you create in the web UI at runtime is **not live
> until a restart**, and the modem can keep reporting `state=RUNNING` on the old
> config while the new channel does nothing. (This is the classic "I set it up,
> nothing worked, I restarted and it magically worked.")

After the restart, the log prints `modem ready` / `modembridge state=RUNNING` and
the level meter starts moving. If you instead see `cpal`/`POLLERR`, the channel is
still on a soundcard — repoint it.

### 3. systemd unit for the feed

```ini
# /etc/systemd/system/aprs-sdr-feed.service
[Unit]
Description=RTL-SDR APRS audio feed -> GrayWolf (sdr_udp)
After=graywolf.service
Wants=graywolf.service

[Service]
ExecStart=/bin/sh -c 'rtl_fm -f 144.390M -M fm -s 48000 -g 40 -p 0 - | socat -u -b 1920 - UDP-SENDTO:127.0.0.1:7355'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now aprs-sdr-feed.service
```

Separate units give you separate diagnostics: "is the radio working"
(`systemctl status aprs-sdr-feed`) versus "is the modem working"
(`systemctl status graywolf`) — exactly the split you want when a packet *isn't*
decoding.

---

## Fallback: `stdin` (quick test)

One pipe, one process tree — handy to watch packets at a terminal for a few
minutes. Downside: if `rtl_fm` dies, GrayWolf's input dies with it. Not for an
unattended station.

```bash
rtl_fm -f 144.390M -M fm -s 48000 -p <PPM> -g <GAIN> - | graywolf ...
```

| Field | Value |
|---|---|
| `source_type` | `stdin` |
| `sample_rate` | `48000` |
| `format` | `s16le` |
| `channels` | `1` |

---

## Verification

Confirm the chain stage by stage — don't assume. This is the exact sequence that
brought it up on `oasis`.

```bash
# 1. Dongle present and not claimed by the DVB driver.
rtl_test -t
#    R820T dongles end with "[R82XX] PLL not locked! ... No E4000 tuner found,
#    aborting." — that is EXPECTED. -t is an E4000-specific test; the abort just
#    confirms you have an R820T. Device-found + gain table = success.

# 2. Demod produces live audio — measured, no speakers needed.
rtl_fm -f 144.390M -M fm -s 48000 -g 40 - | python3 ./rmsmeter.py
#    rmsmeter.py reads S16LE from stdin and prints a moving RMS. A steady non-zero
#    floor that VARIES = live audio (on oasis: ~1450 with squelch-open hiss).
#    Banner: confirm "Output at 48000 Hz". (rmsmeter.py source is at the end of
#    this doc.)

# 3. Feed reaches the socket AND GrayWolf is listening.
rtl_fm -f 144.390M -M fm -s 48000 -g 40 - | socat -u -b 1920 - UDP-SENDTO:127.0.0.1:7355
sudo tcpdump -ni lo udp port 7355     # packets streaming = feed is emitting
sudo ss -lunp | grep 7355             # shows graywolf-modem bound = it's listening

# 4. GrayWolf accepts it and decodes.
journalctl -u graywolf -f --no-hostname
#    Good: "modem ready" + "modembridge state=RUNNING", NO cpal/POLLERR.
#    The sdr_udp level meter moves; a packet appears in the stream within minutes.
```

If `tcpdump` shows packets and `ss` shows GrayWolf bound but the meter is still
flat, it's one of the two gotchas: missing **`socat -b 1920`**, or the channel is
bound to a **soundcard** (POLLERR loop) — see the table below.

> **Dashboard flow meter.** The OASIS dashboard's *APRS SDR Feed* tile now does
> step 3 automatically: it polls `/api/health/feed-flow`, which runs exactly this
> passive `tcpdump -ni lo … udp port 7355` and shows the live packet rate as a
> meter. A feed that is `systemctl active` but emitting **0 pkt/s** (e.g. the
> dongle was unplugged) reads **SILENT** instead of a false green UP. The probe
> needs the scoped sudo grant from `enable-service-controls.py` (it adds an
> `OASIS_SNIFF` rule for this one fixed read-only capture); without it the meter
> shows "flow: enable controls".

---

## Debugging: nothing decodes, or only nearby stations

A moving audio/level meter only proves audio is *flowing* — `rtl_fm` outputs FM
hiss whether or not a packet is present, so a twitching meter is **not** evidence
of a decode. Work the chain in stages to separate *no signal* from
*misconfiguration*, then push for range.

### 1. Decode independently of GrayWolf

Take GrayWolf out of the loop and feed the SDR straight into a gold-standard
decoder. Stop the feed first — only one process can own the dongle:

```bash
sudo systemctl stop aprs-sdr-feed.service
rtl_fm -f 144.390M -M fm -s 22050 -g 40 -p 0 - | multimon-ng -t raw -a AFSK1200 -
```

- **Packets here but not in GrayWolf** → it's GrayWolf config: `sample_rate` must
  be **48000** (matching `rtl_fm -s`), and the channel must be AFSK **1200** /
  **1200·2200 Hz** bound to the **`sdr_udp`** device, not a soundcard.
- **Nothing here either** → the problem is RF / antenna / noise, *not* software.
  Continue below.

(`multimon-ng` is installed by `install-rtl-sdr.py`; it wants 22050 Hz. For a
quality readout, `direwolf -n 1 -r 48000 -b 1 -` at `-s 48000` works too.)

### 2. Prove the receiver hears a strong signal (NOAA)

`rtl_fm`'s RMS is just audio energy — present even with no signal in. Confirm the
front end actually receives RF using a guaranteed-strong local transmitter, in the
*same* narrowband-FM mode as APRS: NOAA weather radio (162.400–162.550 MHz).

```bash
rtl_fm -f 162.550M -M fm -s 22050 -g 49 - | aplay -r 22050 -f S16_LE -t raw -c 1
```

- **Clear robot weather voice** → RX + antenna + drivers are fine; silence on
  144.390 is just weak/no traffic → it's an antenna/location problem (§5).
- **Hiss / nothing** → a loose SMA, an antenna with no sky view, or Pi self-noise
  (§3). Try the other channels: `162.400 162.425 162.450 162.475 162.500 162.525`.

### 3. Pi self-noise / desense — "worked on the bench, dead in production"

A Raspberry Pi's onboard PWM audio, HDMI audio clocks, SD card, Wi-Fi, and cheap
power supplies radiate broadband hash across VHF. An RTL-SDR plugged straight into
the Pi with the antenna inches away gets **desensed**: the noise floor rises and
swamps weak APRS, while strong locals still punch through. Mitigate, highest
impact first:

- **Silence the Pi's audio engines** — these are genuine RF noise sources, not
  just config. In `/boot/firmware/config.txt`:
  ```ini
  dtparam=audio=off
  dtoverlay=vc4-kms-v3d,noaudio
  ```
  then reboot. (On `oasis`, disabling onboard + HDMI audio measurably lowered the
  noise floor and recovered decodes.)
- **Get the dongle off the Pi.** Put it on a USB extension cable so the dongle
  *and* antenna sit 0.5–1 m away from the Pi, PSU, SD card, and Wi-Fi. Clip a
  **ferrite** on the USB lead.
- **Use a clean supply** — the official Pi PSU, not a random charger; switchers
  inject VHF garbage straight into the receiver.

### 4. Gain is not the noise floor

`-g` amplifies signal **and** noise together — it does **not** lower the noise
floor. On the R820T/R828D, near-max gain often *raises* the floor and *overloads*
on a strong nearby digipeater, which hurts weak/distant decodes. Max is rarely
best — **sweep it and count decodes** over a fixed window:

```bash
for g in 28 30 32 34 36 38 40 44 49; do
  echo "=== gain $g ==="
  timeout 120 sh -c \
    "rtl_fm -f 144.390M -M fm -s 48000 -g $g -p 0 - | multimon-ng -t raw -a AFSK1200 - 2>/dev/null | grep -c APRS"
done
```

Whichever gain decodes the most wins. Lock it in permanently:

```bash
python3 scripts/enable-rtl-sdr.py --gain <best> --ppm <best>
```

### 5. Range is mostly the antenna, not the tuner

Tuner gain buys a few dB; the antenna buys tens. If you only hear nearby stations:

- A **resonant 2 m antenna, up high, with sky view** (roll-up J-pole, mag-mount,
  ground plane) beats the stock whip by a mile — the indoor whip is the #1 reason
  for locals-only reception.
- **Digipeaters extend your reach** — you hear direct *and* digi'd packets, so a
  higher, cleaner antenna multiplies coverage.
- Sanity-check there's traffic to hear at all: open **aprs.fi** and look at your
  area. If it's quiet, silence is expected — nothing is broken.

### 6. Host matters — the Pi 400 is a poor SDR host

If you've worked §1–§5 and reception is still bad **or the dongle won't reliably
detect**, the host itself may be the problem. The single biggest variable in an
RTL-SDR setup — after the antenna — is the computer the dongle is plugged into.

**Verified the hard way on this project:** the *same* dongle, charger, antenna, and
location that decoded distant stations perfectly on a laptop (even on its blue
**USB 3.0** port) — on a **Raspberry Pi 400** either failed to enumerate at all or
heard only the very closest stations. Nothing else changed. The Pi 400 is a Pi 4
board packed into a keyboard with the USB ports hard against the PCB, and its
USB 3.0 controller is a strong broadband RFI source that desenses the receiver.

> ⚠️ **USB 3.0 RFI is host-dependent.** A blue port on a well-shielded laptop can
> be perfectly clean; the same standard on a Pi 400 is not. Don't assume "it
> worked on USB3 over there" carries over.

If the Pi 400 is your only option, try these before giving up — cheapest first:

1. **Move the dongle to the USB 2.0 (black) port**, not USB 3.0 (blue).
2. **USB extension cable** to get the dongle + antenna a metre off the keyboard body.
3. **Powered USB hub** — clean 5 V to the dongle off its own supply, isolated from
   the Pi's rail; this also tends to cure intermittent detection.

But the reliable fix is **a different host**: a Pi 4 in a metal case, or a laptop/mini-PC. When in doubt, change
the computer before you blame the dongle.

---

## Caveats

1. **RX-only.** An RTL-SDR cannot transmit. This makes GrayWolf a receive
   monitor / iGate. For TX/PTT use the DRA-Pi-Zero path.
2. **PPM correction matters.** Cheap dongles drift and APRS is narrow. Find your
   offset once and pass `-p <PPM>`; an uncorrected dongle can sit just
   off-channel and decode nothing.
3. **Leave squelch off** (rtl_fm default). Squelch clips the leading flag bytes
   of an AFSK burst and kills decodes.
4. **No `snd-aloop`.** If you ever wanted the `soundcard` route instead it'd be
   `rtl_fm ... | aplay -D hw:Loopback,0,0` with GrayWolf on
   `plughw:Loopback,1,0` — strictly more moving parts and added latency. Skip it.
5. **Sample rates must match.** `rtl_fm -s` output and GrayWolf's `sample_rate`
   must be identical (48000 here), or the modem hears the wrong baud timing. This
   build ignores `-r`, so set the rate with `-s`.
6. **Config persistence.** An early reboot came up with `no channels configured,
   skipping audio setup` — the channel hadn't persisted. After a known-good
   setup, reboot once and confirm the channel survives; if it keeps evaporating,
   chase where GrayWolf stores its config/DB and whether it's written before
   shutdown.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Tuned to ~144.64–144.69 MHz` | `rtl_fm` offset tuning (hardware parked `fs/4` high) | Expected — audio is still on 144.390. Ignore. |
| `Tuned to ~300 kHz`, `PLL not locked!` | `-f` given a bare number → parsed as **Hz** | Keep the `M`: `-f 144.390M` (or full Hz `144390000`). |
| `Output at 200000 Hz` despite `-r 48000` | this `rtl_fm` build ignores `-r` | Set the rate with `-s 48000`. |
| Level meter flat; `tcpdump` shows packets; `ss` shows GrayWolf bound | `socat` sending 8192-byte datagrams, dropped by `sdr_udp` reader | Add **`-b 1920`** to `socat` (or `-b 960`). |
| Log spams `cpal ... alsa::poll() returned POLLERR` / `rebuilding` | channel bound to an auto-detected **soundcard**, not the `sdr_udp` device | Repoint the channel's RX input to the `sdr_udp` device; delete the soundcard device; never use **Detect Devices**. |
| `no channels configured, skipping audio setup` after reboot | channel config didn't persist | Recreate the channel; verify it survives the next reboot (caveat 6). |
| New device/channel added in the UI does nothing; modem shows `state=RUNNING` | GrayWolf loads channel config at modem **start**; runtime UI changes aren't applied live | `sudo systemctl restart graywolf` after adding/editing a device or channel. The "RUNNING" is the *old* modem. |
| Meter moves but **`multimon-ng` decodes nothing at any PPM** | no decodable RF reaching the demod — the meter is just FM hiss | Not a config bug. Run the NOAA strong-signal test (debug §2); check the SMA connector, antenna sky view, and Pi self-noise (§3). |
| Strong locals decode but nothing distant; high noise floor | Pi self-noise (onboard/HDMI audio, PSU, dongle on-board) desensing RX | `dtparam=audio=off` + `dtoverlay=vc4-kms-v3d,noaudio`; move the dongle off the Pi on a USB extension + ferrite; clean PSU; better/higher antenna (debug §3–5). |
| Cranking `-g` to max gives *fewer* decodes | near-max gain raises the floor / overloads on strong locals | Gain ≠ noise floor. Sweep gain and count decodes; pick the peak, not the max (debug §4). |
| RTL-SDR Blog **V4** fails (PLL not locked / no decode) right after an OASIS install on a **newer OS** | the offline bundle installed an **older `librtlsdr`** than your OS ships (e.g. bookworm `0.6.0` on Trixie); the V4 (R828D) needs **`librtlsdr` ≥ 2.0** | Re-run `python3 scripts/install-rtl-sdr.py` — it's now suite-aware + newest-source-wins and will pull apt's newer driver. Or manually `sudo apt install --only-upgrade rtl-sdr librtlsdr2`. Background: [docs/offline-architecture.md](offline-architecture.md). |

---

## Appendix: `rmsmeter.py`

Speaker-free level meter for step 2 — reads S16LE mono from stdin and prints a
live RMS/peak. Write it to a **file** (multi-line `python3 -c '...'` breaks on
terminal auto-indent). Keep the shebang line, or it'll run under the shell and
fail with `import: command not found`:

```python
#!/usr/bin/env python3
import sys, array, math
peak = 0
while True:
    b = sys.stdin.buffer.read(9600)
    if not b:
        break
    a = array.array("h")
    a.frombytes(b[:len(b)//2*2])
    if not a:
        continue
    rms = math.sqrt(sum(x*x for x in a) / len(a))
    peak = max(peak, rms)
    sys.stdout.write(f"\rRMS {rms:7.0f}   peak {peak:7.0f}   (S16 max 32768)")
    sys.stdout.flush()
```

Run it as `... | python3 rmsmeter.py`, or `chmod +x rmsmeter.py` and pipe into
`./rmsmeter.py`.

---

## Reference

- GrayWolf handbook — Audio Devices (local copy):
  [`static/graywolf-handbook/audio.html`](../static/graywolf-handbook/audio.html)
- GrayWolf handbook (online): <https://chrissnell.com/software/graywolf/>
- GrayWolf source: <https://github.com/chrissnell/graywolf>
- RTL-SDR APRS RX iGate (Direwolf reference recipe):
  <https://www.rtl-sdr.com/setting-up-a-raspberry-pi-based-aprs-rx-igate-with-an-rtl-sdr/>
- OARC wiki — RTL-SDR APRS iGate: <https://wiki.oarc.uk/rtl_sdr_aprs_igate>
- GQRX UDP 7355 binding caveat:
  <https://groups.google.com/g/gqrx/c/jbpqqev9fzg>
