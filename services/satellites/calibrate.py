"""Is this dongle accurate enough to receive that downlink?

Two separate things live here, and they are separate on purpose:

  * MEASURING the dongle's frequency error, against a transmitter whose
    frequency is known exactly (NOAA weather radio).
  * JUDGING whether a given downlink is receivable with that error, which
    depends entirely on the frequency and the mode — 20 ppm is invisible on
    FM at 145 MHz and fatal on CW at 435 MHz.

WHY THIS EXISTS
---------------
listen.py has always passed `-p 0` to rtl_fm, i.e. "assume the dongle is
perfect". At 144 MHz APRS that is harmless: even 20 ppm is 2.9 kHz inside a
48 kHz window, and nobody notices. At 435 MHz in a 12 kHz SSB window the same
dongle is 8.7 kHz off and the signal is simply not there — with nothing on
screen to say why, which is the expensive kind of wrong.

HOW THE MEASUREMENT WORKS
-------------------------
rtl_fm's FM output IS instantaneous frequency: each sample is proportional to
how far the signal sits from where we tuned. Voice deviation averages to zero
over several seconds, so the MEAN of those samples is the carrier offset. No
numpy, no FFT — sum a few million int16s.

The scale factor from sample units to Hz is not assumed. Measuring twice, once
on frequency and once deliberately detuned by a known amount, gives two
equations and solves for both the scale AND the offset (see solve_offset). An
assumed constant would have been wrong by ~4% and silently biased every result.

ACCURACY, HONESTLY
------------------
Two runs against NOAA weather radio on 2026-08-12 agreed with each other to
0.25 ppm but sat ~2 ppm away from a careful IQ-based measurement of the same
dongle. Residual voice modulation over a finite window is the likely cause. So
this is a SCREENING test: it reliably separates "fine" from "badly off", and it
is not a calibration reference. Thresholds below carry margin accordingly.
"""

# NOAA weather radio: seven channels, continuously transmitting, frequency held
# by the NWS, and audible almost anywhere in the US without internet. That makes
# it the one reference an offline station can actually use. Outside NWR coverage
# an operator can supply any frequency they trust.
NWR_CHANNELS_HZ = [162_400_000, 162_425_000, 162_450_000, 162_475_000,
                   162_500_000, 162_525_000, 162_550_000]
DEFAULT_REFERENCE_HZ = 162_550_000

# The deliberate detune used for the second measurement. Big enough to dominate
# the modulation noise, small enough to stay well inside the demodulator's
# passband so the two readings share one scale factor.
DETUNE_HZ = 3000

MEASURE_RATE = 24_000
MEASURE_SECONDS = 8

# Peak radial velocity of a LEO pass, km/s — about the most a bird can close at.
_LEO_RADIAL_KM_S = 7.0
_C_KM_S = 299792.458


def measure_argv(freq_hz, device_serial=None, gain="40", rate=MEASURE_RATE,
                 seconds=MEASURE_SECONDS):
    """argv for one rtl_fm measurement run, emitting raw s16 on stdout."""
    argv = ["timeout", str(int(seconds) + 4), "rtl_fm"]
    if device_serial:
        argv += ["-d", str(device_serial)]
    # -p 0 deliberately: we are MEASURING the error, so no correction may be
    # applied during the measurement or we would only ever read back zero.
    argv += ["-f", str(int(freq_hz)), "-M", "fm", "-s", str(int(rate)),
             "-g", str(gain), "-p", "0", "-"]
    return argv


def mean_sample(raw_s16):
    """Mean of raw little-endian int16 samples. Returns None for no data."""
    import array
    a = array.array("h")
    usable = len(raw_s16) - (len(raw_s16) % 2)
    if usable < 2:
        return None
    a.frombytes(raw_s16[:usable])
    return sum(a) / float(len(a))


def solve_offset(mean_on, mean_detuned, detune_hz=DETUNE_HZ):
    """(scale_units_per_hz, offset_hz) from two runs.

    Tuning `detune_hz` HIGHER moves the carrier that much LOWER relative to the
    tuning, so the mean moves by -detune_hz * scale. Two readings, two unknowns:

        scale  = (mean_detuned - mean_on) / -detune_hz
        offset = mean_on / scale

    Solving for the scale instead of assuming rtl_fm's internal one is the whole
    point — the assumed constant was ~4% off when checked against hardware, and
    a scale error biases every ppm reading proportionally.

    Returns (None, None) when the two runs are indistinguishable, which means the
    detune had no effect and the measurement is meaningless — better to report
    nothing than a number derived from a division by almost zero."""
    if mean_on is None or mean_detuned is None or not detune_hz:
        return (None, None)
    delta = mean_detuned - mean_on
    scale = delta / float(-detune_hz)
    # A real detune of 3 kHz moves the mean by thousands of units. Anything this
    # small is noise, not signal.
    if abs(scale) < 1e-3:
        return (None, None)
    return (scale, mean_on / scale)


def ppm_from_offset(offset_hz, reference_hz):
    """Parts-per-million error implied by an absolute offset at a frequency."""
    if offset_hz is None or not reference_hz:
        return None
    return offset_hz / float(reference_hz) * 1e6


def offset_at(ppm, carrier_hz):
    """What that ppm error costs in Hz at another frequency. Linear in carrier,
    which is exactly why a dongle that is fine on 2 m can be useless on 70 cm."""
    if ppm is None or not carrier_hz:
        return None
    return ppm / 1e6 * float(carrier_hz)


def max_doppler_hz(carrier_hz):
    """Peak Doppler excursion for a LEO pass at this carrier — the other half of
    the error budget, and the half that tracked capture removes."""
    return _LEO_RADIAL_KM_S / _C_KM_S * float(carrier_hz)


# How far the signal may drift and still be demodulated, per rtl_fm mode.
#
# FM: the discriminator passes the whole +/-rate/2, but the signal has to stay
# inside the IF filter too; a quarter of the sample rate is comfortable.
# SSB/CW: the audio window is 0..rate/2 = 0..6 kHz and the carrier already sits
# near 700 Hz (CW) or 300-2700 Hz (voice), so there is far less room than the
# raw bandwidth suggests. This is why narrowband modes are the ones that care.
TOLERANCE_HZ = {"fm": 12_000, "usb": 3_500, "lsb": 3_500}


def rx_verdict(ppm, carrier_hz, demod, tracked=False):
    """Can this downlink be received? -> dict for the UI.

    level: "green"    usable
           "amber"    the signal will not stay in the window
           "unknown"  never measured — a warning, not a shrug
           "n/a"      mode we cannot demodulate at all

    `tracked` drops Doppler from the budget, because a Doppler-corrected capture
    follows the bird instead of waiting for it to leave. Before that exists, a
    70 cm CW downlink is amber on any dongle, and that is a true statement about
    the station rather than a complaint about the hardware."""
    tol = TOLERANCE_HZ.get(demod)
    if tol is None or not carrier_hz:
        # Not a warning about anything: the dropdown already disables modes we
        # cannot demodulate, so a badge here would be a second complaint about a
        # choice the operator was never offered.
        return {"level": "n/a", "reason": "mode not demodulable"}
    if ppm is None:
        # NOT green and not silent. Never measured is a real risk — you can
        # record an entire pass and get nothing — so it warns, and it is
        # visually distinct from a measured-and-poor result (hollow dot).
        return {"level": "unknown",
                "reason": "dongle not checked — tap to measure"}
    off = abs(offset_at(ppm, carrier_hz))
    dop = 0.0 if tracked else max_doppler_hz(carrier_hz)
    budget = off + dop
    out = {"offset_hz": off, "doppler_hz": dop, "budget_hz": budget,
           "tolerance_hz": tol, "tracked": bool(tracked)}
    if budget <= tol:
        out["level"] = "green"
        out["reason"] = "within the demodulator's window"
        return out
    out["level"] = "amber"
    # Name the DOMINANT term: the fixes are completely different. A dongle error
    # is corrected with -p; a Doppler excursion needs the tracked capture path.
    if dop > off:
        out["reason"] = ("Doppler alone exceeds the window — needs tracked "
                         "capture" if not tracked else "outside the window")
    else:
        out["reason"] = "dongle frequency error is too large for this mode"
    return out


def dongle_verdict(ppm):
    """Is the DONGLE itself good, independent of any particular downlink?

    Separate from rx_verdict because it answers a different question. rx_verdict
    asks "can I work this bird", which mixes in Doppler; this asks "is my
    hardware sound", which is what an operator wants to know the moment a
    measurement finishes.

    Judged at the hardest case we care about — a narrowband mode at 70 cm, where
    the tolerance is tightest and the error is largest. A dongle that passes
    there passes everywhere OASIS captures."""
    if ppm is None:
        return {"level": "unknown", "text": "dongle not checked"}
    worst = abs(offset_at(ppm, 435_000_000))
    tol = TOLERANCE_HZ["usb"]
    if worst <= tol:
        return {"level": "green", "ppm": ppm,
                "text": f"dongle ready — deviation {ppm:+.2f} ppm"}
    return {"level": "amber", "ppm": ppm,
            "text": f"dongle off by {ppm:+.2f} ppm "
                    f"({worst / 1000:.1f} kHz at 70 cm) — narrowband modes will miss"}


# ── Stored result ────────────────────────────────────────────────────────────
# Per-machine runtime data, like the recordings and the hardware inventory: it
# describes THIS dongle on THIS station and means nothing anywhere else.

def calibration_path(repo_root):
    return __import__("os").path.join(repo_root, "configuration", "sdr-calibration.json")


def load(path):
    """Stored calibration, or {} when there is none. Never raises: an unreadable
    or corrupt file means 'not calibrated', which the UI already renders as grey,
    and taking the Satellites page down over it would be absurd."""
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, data):
    """Atomic write — a half-written calibration would read back as garbage on
    the next boot, and this file is read at every roster refresh."""
    import json
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return data


def stored_ppm(path):
    """The ppm to apply, or None. Only a value we actually measured counts —
    a missing or malformed file must not silently become 0.0, because 0.0 is a
    claim ('this dongle is perfect') and absence is an admission ('we do not
    know'). The UI renders those two very differently."""
    v = load(path).get("ppm")
    return float(v) if isinstance(v, (int, float)) else None


def rtl_fm_ppm_arg(path):
    """What to hand `rtl_fm -p` / `rtl_connector -P`. rtl_fm wants an integer,
    and rounding is harmless: 1 ppm is 436 Hz at 70 cm and the measurement is
    only good to a couple of ppm anyway."""
    ppm = stored_ppm(path)
    return "0" if ppm is None else str(int(round(ppm)))


# ── The measurement itself ───────────────────────────────────────────────────
# Bench-exercised, not unit-tested: it needs a dongle and a transmitter. The
# arithmetic it depends on (solve_offset, ppm_from_offset) is pure and covered.

def measure(device_serial=None, reference_hz=DEFAULT_REFERENCE_HZ,
            detune_hz=DETUNE_HZ, gain="40", rate=MEASURE_RATE,
            seconds=MEASURE_SECONDS, run=None):
    """Two rtl_fm runs against a known transmitter -> {ppm, offset_hz, ...}.

    Raises RuntimeError with something an operator can act on. The commonest
    failure by far is 'no reference signal here', which looks exactly like a
    successful run apart from the two means being indistinguishable — hence
    solve_offset returning None rather than a confident number."""
    import subprocess
    run = subprocess.run if run is None else run

    def one(freq):
        r = run(measure_argv(freq, device_serial, gain, rate, seconds),
                capture_output=True)
        return mean_sample(r.stdout or b"")

    m_on = one(reference_hz)
    m_det = one(reference_hz + detune_hz)
    if m_on is None or m_det is None:
        raise RuntimeError("no audio captured — is the dongle free and present?")
    scale, offset = solve_offset(m_on, m_det, detune_hz)
    if offset is None:
        raise RuntimeError(
            f"detuning by {detune_hz} Hz changed nothing, so there is no usable "
            f"signal on {reference_hz / 1e6:.4f} MHz. Pick a reference "
            "transmitter you can actually hear (a NOAA weather channel, or any "
            "frequency you trust).")
    ppm = round(ppm_from_offset(offset, reference_hz), 2)
    return {"ppm": ppm, "dongle": dongle_verdict(ppm), "offset_hz": round(offset, 1),
            "reference_hz": int(reference_hz), "detune_hz": int(detune_hz),
            "scale_units_per_hz": round(scale, 4), "seconds": int(seconds),
            "method": "rtl_fm two-point mean instantaneous frequency",
            "accuracy_note": "screening test, good to a couple of ppm"}
