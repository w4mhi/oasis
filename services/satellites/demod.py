"""Mode string -> demodulator. Pure classification, no I/O, no dongle.

Extracted from listen.py so the ROSTER BUILDER can ask "could this station ever
do anything with this transmission?" without importing the recorder — listen.py
pulls in subprocess, signals, the dongle lock and the disk budget, none of which
an aggregator has any business touching. listen.py imports every name back, so
its public surface (listen.demod_params, listen.SAMPLE_RATE) is unchanged.

TWO DIFFERENT QUESTIONS, DELIBERATELY NOT ONE
---------------------------------------------
`demod_params` answers "can we turn this into audio RIGHT NOW" — it drives the
Record button and the downlink dropdown's enabled state.

`roster_worthy` answers "is this bird worth a seat in the roster at all" — it
drives what build_records keeps, which is a far more expensive commitment: a
roster row costs a card, a pass prediction on every poll, and a Skyfield
propagation on a Pi 3, forever.

They differ on exactly one thing today, and that is the point. LRPT is weather
imagery we cannot demodulate yet and intend to record now and decode offline
later, so it keeps its seat while staying unsupported. Collapsing the two would
either offer a Record button that writes a WAV of noise, or delete the weather
birds — and undeleting them needs the internet the station may not have.
"""
import re

SAMPLE_RATE = 48000       # rtl_fm -s — matches the proven aprs-sdr-feed build
CW_OFFSET_HZ = 700        # CW is tuned this far LOW so the carrier beats as an audible tone (USB)

# Everything demodulated by `rtl_fm -M fm`. SSTV and the packet modes belong here
# on purpose: SSTV IS FM audio, and AFSK/GMSK/FSK packet comes down as tones a
# decoder can be pointed at afterwards.
_FM_TOK = {"FM", "NFM", "FMN", "AFSK", "FSK", "GFSK", "GMSK", "MSK", "SSTV", "APT", "APRS"}

# Undemodulable today, kept in the roster anyway because the capability is
# planned and the record is the expensive thing to recreate. Keep this set small
# and justified: every token here is a bird that shows up with no Record button.
_PLANNED_TOK = {"LRPT"}


def _mode_tokens(mode):
    # Modes are messy ("AFSK1k2", "GMSK USP", "FSK AX.100 Mode 5"); classify on
    # split tokens, not exact strings.
    return [t for t in re.split(r"[^A-Z0-9]+", (mode or "").upper()) if t]


def _starts(toks, prefixes):
    # startswith, not equality: SatNOGS glues the baud on ("AFSK1k2", "GMSK4k8").
    # Prefix matching is also why BPSK is safe next to MSK/FSK — the test runs
    # token-startswith-prefix, never prefix-in-token, so "BPSK1K2" matches
    # neither.
    return any(t.startswith(p) for t in toks for p in prefixes)


def demod_params(dmode):
    """(rtl_fm mode, sample rate, tuning offset Hz) for a downlink mode, or
    (None, None, None) when we can't demodulate it live (LRPT / DVB / PSK …).

    CW → USB tuned 700 Hz low, so the carrier lands as an audible ~700 Hz tone
    (glides in pitch with Doppler — expected for a beacon). USB/SSB → narrow USB;
    LSB → narrow LSB; the FM family → wide FM (48 kHz)."""
    toks = _mode_tokens(dmode)
    if _starts(toks, ("CW",)):              return ("usb", 12000, -CW_OFFSET_HZ)
    if _starts(toks, ("LSB",)):             return ("lsb", 12000, 0)
    if _starts(toks, ("USB", "SSB")):       return ("usb", 12000, 0)
    if _starts(toks, tuple(_FM_TOK)):       return ("fm", SAMPLE_RATE, 0)
    return (None, None, None)


def is_planned(mode):
    """True for a mode we cannot demodulate yet but intend to."""
    return _starts(_mode_tokens(mode), tuple(_PLANNED_TOK))


def roster_worthy(mode):
    """True when a downlink in this mode justifies carrying the bird at all.

    Demodulable now, or planned. Anything else — BPSK/QPSK telemetry, DVB — can
    never become anything on this station, so the record buys nothing and costs a
    pass computation on every poll."""
    return demod_params(mode)[0] is not None or is_planned(mode)
