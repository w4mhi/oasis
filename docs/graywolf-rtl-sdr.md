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

- RTL-SDR tools installed: `python3 scripts/install-rtl-sdr.py` (provides
  `rtl_fm`, `rtl_test`; blacklists the conflicting DVB driver).
- `socat` for the UDP feed: `sudo apt install socat`.
- A 2 m antenna on the dongle. Verify the device first: `rtl_test -t`.

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
soundcard. When the channel goes live the log prints `modem ready` /
`modembridge state=RUNNING` and the level meter starts moving. If you instead
see `cpal`/`POLLERR`, the channel is still on a soundcard — repoint it.

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

---

## Appendix: `rmsmeter.py`

Speaker-free level meter for step 2 — reads S16LE mono from stdin and prints a
live RMS/peak. Write it to a **file** (multi-line `python3 -c '...'` breaks on
terminal auto-indent):

```python
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
