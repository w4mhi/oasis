"""DRAWS radio audio + PTT. Library half; the CLI entry point is
features/draws-audio/install-draws-audio.py. Mirrors the
features/dra-audio-interface/enable-dra-pi.py split.

The DRAWS codec is a TI TLV320AIC3204 driven by the mainline aic32x4 driver. The
plain `dtoverlay=draws` (already written by draws-gps) registers a simple-card
named `draws` — n7nix's `alsaname=udrc` rename is cosmetic and NOT used here, so
the shared overlay line stays byte-identical across the DRAWS features. All mixer
routing therefore targets card `draws`.

MIXER is NW Digital Radio's known-good baseline (n7nix bin/setalsa-default.sh),
applied to both channels symmetrically (`L,R`). DRAWS is a dual-port stereo codec:
the left channel drives the left mDin6 connector, the right channel the right.
Per-radio deviation tuning is an on-air adjustment left to the operator, exactly
as with the DRA-Pi WM8731.

PTT is a GPIO the TNC (Direwolf/GrayWolf) keys, not something this installer sets
— see PORTS for the reminder and for the port↔channel↔service mapping. DRAWS
swaps the left/right PTT GPIOs relative to the UDRC II."""

CARD = "draws"          # ALSA card name once dtoverlay=draws loads
CARD_MATCH = "draws"    # substring used to detect it in /proc/asound/cards

# Keeps PipeWire/PulseAudio off the radio codec — see build_acp_ignore_rule().
ACP_IGNORE_RULE = "/etc/udev/rules.d/89-draws-radio-audio.rules"

# NW Digital Radio known-good baseline (n7nix bin/setalsa-default.sh). Both
# channels set symmetrically via the codec's `L,R` value syntax. (control, value)
MIXER = [
    # TX path
    # Like the ADC mutes below, the line-output DAC path powers up MUTED. Routing
    # the DAC into the output mixer (LOL/LOR Output Mixer L_DAC/R_DAC) is not
    # enough on its own — without this the mDin6 pins stay silent, PTT keys, the
    # radio transmits an unmodulated carrier, and no station decodes anything.
    # Bench 2026-08-06: zero decodes across a -25dB..-5dB sweep at multiple
    # receiving stations; with this line, all packets decoded at the -25dB
    # baseline. HP DAC is deliberately left alone — the headphone output is not
    # used for radio audio.
    ("LO DAC",                                "on"),
    ("PCM",                                   "-25.0dB,-25.0dB"),
    ("LO Driver Gain",                        "-6.0dB,-6.0dB"),
    ("DAC Left Playback PowerTune",           "P3"),
    ("DAC Right Playback PowerTune",          "P3"),
    ("LOL Output Mixer L_DAC",                "on"),
    ("LOR Output Mixer R_DAC",                "on"),
    # RX path
    # The ADC channel mutes are the codec's POWER-ON DEFAULT and must be cleared
    # explicitly. The aic32x4 driver declares these uninverted, so the control
    # reading `on` means "mute engaged" — a capture stream then runs normally and
    # delivers perfect digital zeros. Bench 2026-08-06: without these two lines
    # RX was silent on both channels with a radio attached and squelch open.
    ("ADCFGA Left Mute",                      "off"),
    ("ADCFGA Right Mute",                     "off"),
    ("ADC Level",                             "0.0dB,0.0dB"),
    ("IN1_L to Left Mixer Positive Resistor", "Off"),
    ("IN1_R to Right Mixer Positive Resistor", "Off"),
    ("IN2_L to Left Mixer Positive Resistor", "10 kOhm"),
    ("IN2_R to Right Mixer Positive Resistor", "10 kOhm"),
]

# DRAWS PTT is GPIO the TNC keys (DRAWS swaps left/right vs UDRC II).
#
# WINLINK IS ON THE LEFT PORT (direwolf channel 0) and that is FORCED: pat 1.0.0
# / wl2k-go v1.0.1 panic ("incorrect port in frame") on any AGW port but 0, and
# direwolf's channel↔audio mapping is fixed by the codec, so channel 0 is the
# left connector. Bench 2026-08-06: radio_port 0 connects, 1 panics, 2 is out of
# range. APRS takes the right port — GrayWolf does not use that library.
#
# This list is the single source of truth for the port↔channel↔service mapping:
# the TNC config, the installer's reminder, and the assignment-console labels in
# common/hardware.DRAWS_PORTS all follow it.
PORTS = [
    {"port": "left",  "gpio": 12, "channel": 0, "service": "Winlink",
     "profile": "oasis-draws-winlink"},
    {"port": "right", "gpio": 23, "channel": 1, "service": "APRS",
     "profile": "oasis-draws-aprs"},
]


def port_for_channel(channel):
    """The PORTS entry owning a direwolf channel. Raises on an unknown channel
    rather than defaulting — a wrong port silently keys the wrong radio."""
    for p in PORTS:
        if p["channel"] == channel:
            return p
    raise ValueError("no DRAWS port for channel %r" % (channel,))


# --- live transmit test -----------------------------------------------------
# Proves the whole TX chain end to end (PTT + codec + mixer + radio) by putting
# one real packet on the air through a running Direwolf. Frames are built here
# rather than shelled out to `kissutil` so the check works with nothing but the
# standard library — no extra package on a go-box, and the frame construction
# unit-tests off-Pi.
LIVETEST_COMMENT = "OASIS DRAWS TEST"
LIVETEST_DEST = "APDW17"        # Direwolf's tocall — it is the TNC on the air
KISS_HOST = "127.0.0.1"
KISS_PORT = 8001                # matches KISSPORT in oasis-draws.conf

_FEND, _FESC, _TFEND, _TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def parse_callsign(text):
    """Split "W4MHI-6" into ("W4MHI", 6). Raises ValueError on anything AX.25
    cannot carry: a 1-6 character alphanumeric base and an SSID of 0-15."""
    text = (text or "").strip().upper()
    base, _, ssid_text = text.partition("-")
    if not base or len(base) > 6 or not base.isalnum():
        raise ValueError(
            "invalid callsign %r — expected 1-6 alphanumerics, e.g. W4MHI-6" % text)
    ssid = 0
    if ssid_text:
        if not ssid_text.isdigit() or int(ssid_text) > 15:
            raise ValueError("invalid SSID in %r — expected 0-15" % text)
        ssid = int(ssid_text)
    return base, ssid


def _ax25_address(call, ssid, last):
    """One 7-byte AX.25 address: the callsign padded to six characters and
    shifted left a bit, then an SSID byte whose bit 0 marks the final address."""
    shifted = bytes(ord(c) << 1 for c in call.ljust(6))
    return shifted + bytes([0x60 | (ssid << 1) | (1 if last else 0)])


def build_ax25_ui_frame(call, ssid, comment=LIVETEST_COMMENT, dest=LIVETEST_DEST):
    """A bare AX.25 UI frame carrying an APRS *status* report (`>`).

    Deliberately a status and not a position: a bench test must not invent
    coordinates for a station that has none. There is also no digipeater path —
    direct RF only, so a test packet is never relayed across the network."""
    return (_ax25_address(dest, 0, False)
            + _ax25_address(call, ssid, True)
            + bytes([0x03, 0xF0])            # UI, no layer 3
            + (">" + comment).encode("ascii"))


def kiss_wrap(frame, channel=0):
    """KISS-encapsulate `frame` for TNC port `channel` (data command 0). The
    channel picks the radio port: 0 = left/Winlink, 1 = right/APRS (see PORTS —
    Winlink must be channel 0, the only AGW port pat can use)."""
    body = bytearray()
    for b in frame:
        if b == _FEND:
            body += bytes([_FESC, _TFEND])
        elif b == _FESC:
            body += bytes([_FESC, _TFESC])
        else:
            body.append(b)
    return bytes([_FEND, (channel << 4) & 0xF0]) + bytes(body) + bytes([_FEND])


def build_livetest_frame(callsign, channel=0, comment=LIVETEST_COMMENT):
    """The complete KISS frame for one on-air test packet. Validates the
    callsign first, so a typo fails before anything touches the radio."""
    call, ssid = parse_callsign(callsign)
    return kiss_wrap(build_ax25_ui_frame(call, ssid, comment), channel)


def build_mixer_commands(card=CARD):
    """Return the `amixer sset` argv vectors that apply the known-good routing to
    `card`. Pure — no subprocess here so it unit-tests off-Pi.

    The `--` end-of-options marker is REQUIRED, not cosmetic: two of the controls
    take negative dB values (`-25.0dB,-25.0dB`, `-6.0dB,-6.0dB`) and amixer's
    getopt parses a leading `-` as a switch, failing with "Invalid switch or
    option". Caught on the bench 2026-08-06 — the two TX-level controls silently
    stayed at their defaults (PCM at +0.5dB instead of -25.0dB, ~25dB too hot into
    the radio) while the other nine applied. The marker goes before the control
    name so the control and value remain the final two argv slots."""
    return [["amixer", "-c", card, "sset", "--", ctrl, val] for ctrl, val in MIXER]


# --- shared 2-channel TNC ---------------------------------------------------
# DRAWS is ONE stereo PCM device, so two direwolf processes cannot both open it
# (the second dies with "device busy"). Both radio ports therefore live in a
# single always-on instance: channel 0 = left/Winlink, channel 1 = right/APRS,
# sharing AGW :8000 / KISS :8001. Apps pick a port by channel number — pat via
# agwpe.radio_port, GrayWolf by attaching to AGW channel 0 instead of spawning
# its own direwolf. See specs/2026-07-28-draws-gobox-p2-tnc-wiring-design.md.
TNC_ADEVICE   = "plughw:draws,0"
TNC_CONF_NAME = "oasis-draws.conf"
# The two ports are PROFILES inside that one file, not separate configs: a second
# direwolf on the same PCM dies with "device busy". These names appear as the
# channel banners in the conf, as the device labels in the assignment console,
# and in the unit description — so a given port is identifiable everywhere.
TNC_PROFILE_WINLINK = "oasis-draws-winlink"   # CHANNEL 0, left mDin6  (see PORTS)
TNC_PROFILE_APRS    = "oasis-draws-aprs"      # CHANNEL 1, right mDin6
TNC_UNIT_NAME = "direwolf-draws.service"
TNC_UNIT_PATH = "/etc/systemd/system/" + TNC_UNIT_NAME
TNC_AGW_PORT  = 8000
TNC_KISS_PORT = 8001


def build_tnc_conf(callsign, ptt_left, ptt_right):
    """The 2-channel Direwolf config for both DRAWS ports. Pure.

    `ptt_left`/`ptt_right` are SYSFS GLOBAL gpio numbers (gpiochip base + BCM,
    e.g. 524/535 — see common.draws.sysfs_gpio), not BCM numbers. Raises
    ValueError if either is unresolved: a guessed PTT number would key the wrong
    line, which is worse than refusing to write the file."""
    if ptt_left is None or ptt_right is None:
        raise ValueError(
            "unresolved PTT gpio (left=%r right=%r) — could not find the 40-pin "
            "gpiochip bank; pass the sysfs numbers explicitly" % (ptt_left, ptt_right))
    call = (callsign or "N0CALL").strip().upper()
    ptt_for = {0: ptt_left, 1: ptt_right}
    chans = "".join(
        "\n# ── {profile} ── {port} mDin6 ── {service} "
        "──────────\n"
        "CHANNEL {channel}\nMYCALL {call}\nMODEM 1200\nPTT GPIO {ptt}\n".format(
            call=call, ptt=ptt_for[p["channel"]], **p)
        for p in sorted(PORTS, key=lambda p: p["channel"]))
    rows = "\n".join(
        "#   {profile:<20} CHANNEL {channel}  {port:<5} mDin6  PTT BCM {gpio:<2} "
        "(sysfs {ptt})".format(ptt=ptt_for[p["channel"]], **p)
        for p in sorted(PORTS, key=lambda p: p["channel"]))
    return """\
# OASIS DRAWS go-box TNC — BOTH radio ports in ONE direwolf instance.
# Generated by features/draws-audio/install-draws-audio.py — edits are lost on
# reinstall.
#
# There are two PROFILES here, not two config files. DRAWS is a single stereo
# PCM ({adevice}); a second direwolf on it dies with "device busy". So each
# radio port is a CHANNEL of this one instance:
#
{rows}
#
# Apps attach over the shared AGW/KISS ports and pick a profile by channel:
#   Winlink / pat   -> AGW 127.0.0.1:{agw}  radio_port 0
#   APRS / GrayWolf -> AGW 127.0.0.1:{agw}  radio_port 1
#
# WINLINK IS ON CHANNEL 0 BY NECESSITY, not preference: pat 1.0.0 / wl2k-go
# v1.0.1 panic ("incorrect port in frame") on any AGW port but 0, and direwolf's
# channel-to-audio mapping is fixed by the codec, so channel 0 is the LEFT
# connector. Put the Winlink radio on the LEFT mDin6.

ADEVICE   {adevice}
ARATE     48000
ACHANNELS 2
{chans}
AGWPORT {agw}
KISSPORT {kiss}

# RMS gateways mishandle the AX.25 v2.2 XID teardown, leaving direwolf
# key-locked retransmitting DISC/XID for ~30s after a QSO. Force v2.0 (matches
# oasis-dra-pi-winlink.conf). Per-station opt-out: 'V20 <call>'.
MAXV22 0
""".format(adevice=TNC_ADEVICE, agw=TNC_AGW_PORT, kiss=TNC_KISS_PORT,
           rows=rows, chans=chans)


def build_tnc_service(user, home, ptt_left, ptt_right):
    """The systemd unit for the shared TNC. Pure.

    Enabled at boot (unlike the exclusive pat-direwolf) because on DRAWS this
    instance IS the radio stack for both ports. ExecStopPost frees BOTH PTT
    sysfs lines so a restart can re-claim them — leaking either leaves the next
    start unable to key that port."""
    conf = "%s/.config/direwolf/%s" % (home.rstrip("/"), TNC_CONF_NAME)
    return """\
[Unit]
Description=OASIS DRAWS TNC — {p_aprs} (ch0) + {p_winlink} (ch1)
Documentation=file://{conf}
After=network.target sound.target
Wants=sound.target

[Service]
Type=simple
User={user}
Environment=HOME={home}
ExecStart=/usr/bin/direwolf -t 0 -c {conf}
ExecStopPost=/bin/sh -c 'for g in {ptt_left} {ptt_right}; do echo $g > /sys/class/gpio/unexport 2>/dev/null || true; done'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
""".format(conf=conf, user=user, home=home.rstrip("/"),
           ptt_left=ptt_left, ptt_right=ptt_right,
           p_aprs=TNC_PROFILE_APRS, p_winlink=TNC_PROFILE_WINLINK)


def build_acp_ignore_rule(card=CARD):
    """udev rule keeping the radio codec out of the desktop audio stack.

    PipeWire/WirePlumber otherwise adopt the card as an ordinary sound device
    and manage its volume, re-applying their own level AFTER alsactl restores
    at boot. Only `PCM` regresses — the routing enums and mutes are left alone —
    so TX deviation silently returns to the codec default (+0.5dB, ~25dB hot)
    on every power cycle while everything else looks correct. `ACP_IGNORE` is
    the standard opt-out and is honoured by both PipeWire and PulseAudio."""
    return (
        "# OASIS - keep the %s radio codec out of the desktop audio stack.\n"
        "#\n"
        "# Without this, PipeWire/WirePlumber claim the card and re-apply their\n"
        "# own volume after alsactl restores it at boot, silently resetting TX\n"
        "# deviation to the codec default (~25dB hot). Installed by\n"
        "# features/draws-audio/install-draws-audio.py.\n"
        'SUBSYSTEM=="sound", KERNEL=="card*", ATTR{id}=="%s", ENV{ACP_IGNORE}="1"\n'
        % (card, card))


def removal_record(repo_root=None):
    """Teardown record (declarative — see common/removal.py). Strip the shared
    DRAWS overlay line and take our own udev rule with us; leave the persisted
    ALSA mixer state in place with an advisory (it is shared board state — no
    safe automatic undo). Reboot to drop the overlay. NOTE (P2): once
    draws-gps/draws-audio coexist, this strip must become ref-safe (strip only
    when the last DRAWS feature is removed)."""
    return {"config_lines": ["dtoverlay=draws"],
            "services": [TNC_UNIT_NAME],
            "files": [ACP_IGNORE_RULE, TNC_UNIT_PATH],
            "notes": ["ALSA mixer state left in place (shared board state — no "
                      "safe automatic undo).",
                      "The direwolf config in ~/.config/direwolf/%s is left in "
                      "place (it may carry operator edits)." % TNC_CONF_NAME],
            "requires_reboot": True}


def decide_exit_code(overlay_changed, card_present):
    """10 = 'config written, reboot required' (_REBOOT_EXIT_CODE) when the overlay
    was just added or the sound card has not enumerated yet; 0 when the card is
    live and nothing changed."""
    return 10 if (overlay_changed or not card_present) else 0
