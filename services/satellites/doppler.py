"""Pure Doppler arithmetic for satellite capture: numbers in, numbers out.

Nothing here imports skyfield, pycsdr or anything else optional, and nothing
here does I/O. That is deliberate — this is the module that decides where a
signal actually is, and it must be testable on a laptop, in the minimal CI
server-setup job, and on a Pi with no dongle attached, without any of them.

The orbital half lives in predict.py (it needs a propagator) and the DSP half in
sdrchain.py (it needs pycsdr). This module is the arithmetic between them.

WHAT THE CAPTURE CHAIN ACTUALLY DOES
------------------------------------
The dongle is parked ONCE per capture and never retuned to chase the bird.
Doppler is a software NCO shift on a running chain — csdr's Shift.setRate() —
exactly as OpenWebRX follows a signal without touching the device. Retuning
hardware mid-pass buys nothing and costs a PLL transient and a gap in the audio.

So the geometry is:

    centre        = downlink + LFO_OFFSET_HZ        (parked, fixed for the capture)
    signal is at  = downlink + doppler(t)
    baseband      = signal - centre = doppler(t) - LFO_OFFSET_HZ
    shift rate    = -baseband / INPUT_RATE_HZ       (normalised, csdr's units)

WHY THE DONGLE IS PARKED OFF-TARGET
-----------------------------------
An RTL-SDR puts a DC-offset artefact at its centre frequency. Park exactly on
the downlink and the signal lands on that spike. Parking LFO_OFFSET_HZ high and
letting the Shift cover the difference moves the spike 48 kHz away, where the
decimation FIR (a +/-24 kHz passband after the first /5) removes it outright.

Numbers derived on paper 2026-08-12, in specs/2026-08-12-satellite-doppler-spec.md.
NOT bench-verified: no part of this has met a dongle yet.
"""

C_KM_S = 299792.458

# Requested from rtl_connector. 240 kHz and not the more obvious 250 kHz for two
# independent reasons that happen to agree: 28.8 MHz / 240000 = 120 exactly, so
# the RTL2832U's fractional divider never engages; and 240000 / 5 = 48000 with
# 240000 / 20 = 12000, the rates demod_params already uses, so the chain
# decimates by integers alone and FractionalDecimator drops out of the design.
INPUT_RATE_HZ = 240_000

# How far above the downlink the dongle parks. Anything in roughly 30-60 kHz
# works; 48000 is chosen because it puts the nominal shift rate at 0.2 exactly,
# which makes the arithmetic inspectable by eye. If the bench shows the DC spike
# bleeding through, raise this — it is the only thing that has to change.
LFO_OFFSET_HZ = 48_000

# csdr's Shift takes a rate normalised to the input rate, so it is meaningful
# only within +/-0.5 (Nyquist). Everything we do sits near 0.2.
MAX_SHIFT_RATE = 0.5

# How often the tracker re-aims the NCO. The 10 s track sample step is far too
# coarse to drive this: Doppler rate at closest approach is ~215 Hz/s at 437 MHz,
# so 10 s steps leave ~2 kHz of error — wider than an entire SSB channel. At
# 10 Hz the worst-case residual is ~21 Hz, below notice, and setRate() only
# changes an NCO increment so the tick costs nothing.
TICK_HZ = 10.0


def shift_hz(factor, carrier_hz):
    """The Doppler shift in Hz for a dimensionless factor and a carrier.

    The factor (see predict.range_rate_factor) is where the frequency-independence
    lives: one orbital sample serves 145.8 and 437.8 MHz alike, and the carrier
    is applied at the point of use — which is the only place that knows which
    downlink the operator actually armed."""
    return factor * carrier_hz


def centre_hz(downlink_hz, lfo_offset_hz=LFO_OFFSET_HZ):
    """Where to park the dongle for a given downlink. Fixed for the whole
    capture: coarse retuning is for changing satellite or band, never Doppler."""
    return downlink_hz + lfo_offset_hz


def baseband_offset_hz(doppler_hz, lfo_offset_hz=LFO_OFFSET_HZ):
    """Where the signal sits relative to the parked centre. Negative throughout a
    normal capture, since the dongle parks ABOVE the downlink and Doppler never
    approaches the offset in size (~10 kHz against 48 kHz at 70 cm)."""
    return doppler_hz - lfo_offset_hz


def shift_rate(doppler_hz, lfo_offset_hz=LFO_OFFSET_HZ, input_rate_hz=INPUT_RATE_HZ):
    """The normalised NCO rate to hand csdr's Shift.setRate().

    Negated because the shift must move the signal TO zero, not by its offset —
    the sign error this guards is silent: the audio still demodulates, it just
    tracks the bird backwards and walks out of the passband twice as fast."""
    return -baseband_offset_hz(doppler_hz, lfo_offset_hz) / float(input_rate_hz)


def shift_rate_in_range(rate, limit=MAX_SHIFT_RATE):
    """Is a shift rate physically meaningful? Outside +/-0.5 the NCO is asking
    for a frequency the input rate cannot represent, which means the LFO offset
    and the input rate have drifted out of agreement — a configuration error, not
    a runtime condition, so callers should refuse the capture rather than clamp
    and produce audio that is quietly wrong."""
    return -limit < rate < limit


def curve_at(curve, t_s):
    """The Doppler factor at t_s seconds into a capture, linearly interpolated
    between a curve's samples (see predict.compute_doppler_curve).

    This is what runs at TICK_HZ, so it must never touch a propagator — the whole
    reason the curve is precomputed. Clamps at both ends: a capture that outlives
    its curve holds the last value rather than failing, because the alternative
    mid-pass is no correction at all.

    None for an empty curve — a caller with no curve must fall back to the
    uncorrected path, and a plausible 0.0 would hide that decision.
    """
    if not curve:
        return None
    if len(curve) == 1 or t_s <= curve[0][0]:
        return curve[0][1]
    if t_s >= curve[-1][0]:
        return curve[-1][1]
    # Uniform step by construction, so the index is arithmetic rather than a
    # search — this runs 10x a second for the length of a pass.
    step = (curve[-1][0] - curve[0][0]) / (len(curve) - 1)
    if step <= 0:
        return curve[0][1]
    i = int((t_s - curve[0][0]) / step)
    i = max(0, min(i, len(curve) - 2))
    (ta, fa), (tb, fb) = curve[i], curve[i + 1]
    span = tb - ta
    return fb if span <= 0 else fa + (fb - fa) * ((t_s - ta) / span)


def tick_shift_rate(curve, t_s, carrier_hz,
                    lfo_offset_hz=LFO_OFFSET_HZ, input_rate_hz=INPUT_RATE_HZ):
    """One tracker tick, whole: curve + clock + armed carrier -> the rate to hand
    Shift.setRate(). None when there is no curve to read.

    The carrier is a parameter and not baked into the curve for the same reason
    the server serves a factor and not a frequency — the curve does not know, and
    must not have to know, which downlink was armed."""
    factor = curve_at(curve, t_s)
    if factor is None:
        return None
    return shift_rate(shift_hz(factor, carrier_hz), lfo_offset_hz, input_rate_hz)
