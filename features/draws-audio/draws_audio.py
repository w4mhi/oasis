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
— see PORTS for the reminder. DRAWS swaps the left/right PTT GPIOs relative to the
UDRC II."""

CARD = "draws"          # ALSA card name once dtoverlay=draws loads
CARD_MATCH = "draws"    # substring used to detect it in /proc/asound/cards

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

# DRAWS PTT is GPIO the TNC keys (DRAWS swaps left/right vs UDRC II). Assigned to
# OASIS services: left connector = APRS, right connector = Winlink.
PORTS = [
    {"port": "left",  "gpio": 12, "service": "APRS"},
    {"port": "right", "gpio": 23, "service": "Winlink"},
]


# --- live transmit test -----------------------------------------------------
# Proves the whole TX chain end to end (PTT + codec + mixer + radio) by putting
# one real packet on the air through a running Direwolf. Frames are built here
# rather than shelled out to `kissutil` so the check works with nothing but the
# standard library — no extra package on a go-box, and the frame construction
# unit-tests off-Pi.
LIVETEST_COMMENT = "OASIS DRAWS TEST"
LIVETEST_DEST = "APDW17"        # Direwolf's tocall — it is the TNC on the air
KISS_HOST = "127.0.0.1"
KISS_PORT = 8001                # matches KISSPORT in the direwolf-draws conf

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
    channel picks the radio port: 0 = left/APRS, 1 = right/Winlink."""
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


def removal_record(repo_root=None):
    """Teardown record (declarative — see common/removal.py). Strip the shared
    DRAWS overlay line; leave the persisted ALSA mixer state in place with an
    advisory (it is shared board state — no safe automatic undo). Reboot to drop
    the overlay. NOTE (P2): once draws-gps/draws-audio coexist, this strip must
    become ref-safe (strip only when the last DRAWS feature is removed)."""
    return {"config_lines": ["dtoverlay=draws"],
            "notes": ["ALSA mixer state left in place (shared board state — no "
                      "safe automatic undo)."],
            "requires_reboot": True}


def decide_exit_code(overlay_changed, card_present):
    """10 = 'config written, reboot required' (_REBOOT_EXIT_CODE) when the overlay
    was just added or the sound card has not enumerated yet; 0 when the card is
    live and nothing changed."""
    return 10 if (overlay_changed or not card_present) else 0
