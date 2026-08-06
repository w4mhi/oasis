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
