"""What this station can actually receive.

One home for the radio-band policy, because two modules need it and neither may
import the other: `satnogs.py` already does `import roster` (for OPERATOR_FIELDS),
so a predicate living in either one would close an import cycle the moment the
other called it. This module imports nothing.

The distinction it draws is between what SatNOGS KNOWS about a satellite and what
an OASIS station can DO with it. SatNOGS lists every active transmitter, and an
RTL-SDR can tune a great many of them — the ISS alone has 28 within tuning range,
including Soyuz VHF, three spacesuit channels, Zarya, Zvezda, Regul and Kvant.
None of those is a thing an amateur station tunes, and rendering them as buttons
crowded the nine that matter off the card.
"""

# The amateur-satellite bands: 2 m, 70 cm, 23 cm.
AMATEUR_BANDS_MHZ = ((144.0, 148.0), (420.0, 450.0), (1240.0, 1300.0))

# 137.0-138.0 MHz is the meteorological VHF downlink band (NOAA APT, METEOR
# LRPT, Direct Sounder Broadcast). Not amateur, and deliberately included: an
# amateur-only rule would delete every weather-bird button on the card.
WX_BAND_MHZ = (137.0, 138.0)

# Everything this station can use, for the one question the card asks.
USABLE_BANDS_MHZ = AMATEUR_BANDS_MHZ + (WX_BAND_MHZ,)


def in_bands(freq_mhz, bands):
    """True when freq_mhz falls in any (low, high) pair. False for None —
    an unknown frequency is not evidence of anything."""
    if freq_mhz is None:
        return False
    return any(lo <= freq_mhz <= hi for lo, hi in bands)


def usable_downlink(freq_mhz):
    """True when this station could actually receive and use this downlink.

    Used to decide what reaches the roster card. A frequency we cannot name a
    use for is not a button — it is noise between the operator and the six
    frequencies they came for."""
    return in_bands(freq_mhz, USABLE_BANDS_MHZ)
