"""The pycsdr chain — IQ from the connector in, demodulated audio out.

Knows nothing about orbits. It is handed a Doppler shift in Hz and applies it;
where that number came from is doppler.py's business and predict.py's before
that. Knows nothing about files or HTTP either: it yields s16 mono bytes and
listen.py decides whether those become a WAV or an MP3 stream.

THE TOPOLOGY, AND WHY THESE NUMBERS
-----------------------------------
    TcpSource -> Shift -> FirDecimate(5) -> [mode tail] -> Agc -> Convert(s16)

240 kHz in, because 28.8 MHz / 240000 = 120 exactly (so the RTL2832U's
fractional divider never engages) AND it divides into both 48 kHz and 12 kHz,
the rates demod_params already uses. Integer decimation only; no
FractionalDecimator anywhere in the design.

Two stages rather than one for SSB (/5 then /4, not /20): a single 20:1 FIR needs
a very narrow transition band and therefore a long tap count, and two short
filters cost far less for the same stopband.

Doppler is a SOFTWARE shift. The dongle is parked once at downlink + LFO and
never retuned; Shift.setRate() slides an NCO across the captured window as the
bird moves. No PLL transient, no gap in the audio, no retune latency.

WHAT WAS LEARNED THE HARD WAY (pycsdr 0.18.2, 2026-08-12)
--------------------------------------------------------
* Modules are wired UPSTREAM-WRITER first, then downstream-reader, glued by one
  Buffer per stage. Each Module is both a Source and a Sink.
* BufferReader.read() BLOCKS. It returns variable-size chunks and None when the
  stream ends. reader.stop() is the only way to unblock a waiting read, which is
  why teardown goes through it rather than through a flag the loop polls.
* Writing IQ into a Buffer directly does NOT work as an input path — a large
  write raises BufferError and a chunked one silently produces nothing.
  TcpSource is the input, which is what production uses anyway.
"""
import threading

import connector
import doppler

# Output rates, matching listen.demod_params so both capture paths produce
# identically-shaped audio and the artifact contract holds.
FM_RATE = 48_000
SSB_RATE = 12_000

# CW is tuned so the carrier lands as an audible tone rather than at DC. On the
# rtl_fm path that was done by detuning the dongle; here it folds into the shift,
# which leaves the hardware honest and costs nothing.
CW_TONE_HZ = 700


def _modules():
    """pycsdr's modules + types, imported through the dist-packages shim.

    Raises RuntimeError rather than ImportError so callers get one exception
    type and a message that names the fix. The import failing is expected on any
    station without the DSP stack — that is not an error, it is the uncorrected
    path being the only one available (see listen.capture_backend)."""
    connector.enable_dist_packages()
    try:
        from pycsdr import modules, types
    except Exception as e:                                   # noqa: BLE001
        raise RuntimeError(
            f"pycsdr is not importable ({e}) — run "
            "services/satellites/install-dsp.py, or check that the OASIS venv "
            "was built on the same Python the package was compiled for") from e
    return modules, types


def plan(demod, input_rate=None):
    """(output_rate, [decimation stages]) for a demod mode, or None.

    Pure and importable without pycsdr, so the rate arithmetic is testable on a
    laptop with no DSP stack — which is most of what can go quietly wrong here."""
    rate = doppler.INPUT_RATE_HZ if input_rate is None else int(input_rate)
    if demod == "fm":
        if rate % FM_RATE:
            return None
        return (FM_RATE, [rate // FM_RATE])
    if demod in ("usb", "lsb"):
        if rate % SSB_RATE:
            return None
        total = rate // SSB_RATE                 # 20 for the standard chain
        first = rate // FM_RATE if rate % FM_RATE == 0 else total
        if first and total % first == 0 and first != total:
            return (SSB_RATE, [first, total // first])
        return (SSB_RATE, [total])
    return None


def sideband_cut(demod, tone_hz=CW_TONE_HZ, width_hz=2400):
    """(low_cut, high_cut) in Hz for the Bandpass that selects the sideband.

    USB keeps positive audio frequencies, LSB negative — the Bandpass is complex
    and sits before RealPart, so the sign IS the sideband. CW is USB with a
    narrow window around the tone the shift places the carrier on."""
    if demod == "usb":
        return (300.0, 300.0 + width_hz)
    if demod == "lsb":
        return (-(300.0 + width_hz), -300.0)
    return None


class SdrChain:
    """One capture's worth of DSP. Not thread-safe apart from set_doppler_hz,
    which the tracker calls from its own thread and which only touches an NCO."""

    def __init__(self, port, demod, carrier_hz, input_rate=None,
                 lfo_offset_hz=None, tone_offset_hz=0.0):
        p = plan(demod, input_rate)
        if p is None:
            raise RuntimeError(f"no chain for demod {demod!r}")
        self.port = int(port)
        self.demod = demod
        self.carrier_hz = float(carrier_hz)
        self.input_rate = doppler.INPUT_RATE_HZ if input_rate is None else int(input_rate)
        self.lfo = doppler.LFO_OFFSET_HZ if lfo_offset_hz is None else int(lfo_offset_hz)
        # Where the carrier should LAND. 0 for FM and SSB voice; CW_TONE_HZ for
        # CW, where the carrier is the signal and landing it at DC means hearing
        # nothing (and being rejected by the sideband filter besides).
        self.tone_offset = float(tone_offset_hz)
        self.out_rate, self.decimations = p
        self._src = None
        self._shift = None
        self._reader = None
        self._modules = []
        self._lock = threading.Lock()

    # ── build ────────────────────────────────────────────────────────────────
    def _tail(self, m, t):
        """The mode-specific stages after decimation."""
        if self.demod == "fm":
            return [m.FmDemod(), m.Limit(), m.NfmDeemphasis(self.out_rate),
                    m.Agc(t.Format.FLOAT)]
        cut = sideband_cut(self.demod)
        # Bandpass cutoffs are NORMALISED to the sample rate at this point in the
        # chain, not absolute Hz — confirmed against 0.18.2 by pushing a tone of
        # known frequency through and checking where it came out.
        return [m.Bandpass(cut[0] / self.out_rate, cut[1] / self.out_rate, 0.05),
                m.RealPart(), m.Agc(t.Format.FLOAT)]

    def start(self):
        """Build and wire the chain; the source begins reading immediately."""
        m, t = _modules()
        F = t.Format
        self._src = m.TcpSource(self.port, F.COMPLEX_FLOAT)
        # Start at zero Doppler rather than at some neutral rate: the LFO offset
        # is always present, so 0.0 would put the signal 48 kHz off from the
        # first sample until the tracker's first tick.
        self._shift = m.Shift(doppler.shift_rate(0.0, self.lfo, self.input_rate,
                                                 self.tone_offset))
        chain = [self._shift]
        chain += [m.FirDecimate(d) for d in self.decimations]
        chain += self._tail(m, t)
        chain += [m.Convert(F.FLOAT, F.SHORT)]
        self._modules = chain

        # Upstream writer first, then the downstream reader — the order pycsdr
        # expects. One Buffer per stage.
        prev = m.Buffer(F.COMPLEX_FLOAT)
        self._src.setWriter(prev)
        for mod in chain:
            mod.setReader(prev.getReader())
            out = m.Buffer(mod.getOutputFormat())
            mod.setWriter(out)
            prev = out
        self._reader = prev.getReader()
        return self

    # ── run ──────────────────────────────────────────────────────────────────
    def set_doppler_hz(self, doppler_hz):
        """Re-aim the NCO. Called ~10x a second by the tracker; this is the whole
        Doppler mechanism and it costs an increment update, nothing more."""
        rate = doppler.shift_rate(doppler_hz, self.lfo, self.input_rate,
                                  self.tone_offset)
        if not doppler.shift_rate_in_range(rate):
            # Refusing beats clamping: a rate past Nyquist means the LFO and the
            # input rate disagree, which is a configuration error, and clamping
            # would produce audio that demodulates but is quietly off-frequency.
            raise RuntimeError(f"shift rate {rate:.3f} is outside +/-0.5 — "
                               f"LFO {self.lfo} vs input rate {self.input_rate}")
        with self._lock:
            if self._shift is not None:
                self._shift.setRate(rate)
        return rate

    def read(self):
        """Next chunk of s16 mono audio, or b'' at end of stream. BLOCKS."""
        if self._reader is None:
            return b""
        d = self._reader.read()
        return b"" if d is None else bytes(d)

    def stop(self):
        """Tear down. Idempotent, never raises — a capture must always end.

        The reader is stopped FIRST: read() blocks, and anything waiting on it
        would otherwise never return to notice the chain had gone."""
        r, self._reader = self._reader, None
        if r is not None:
            try:
                r.stop()
            except Exception:                                # noqa: BLE001
                pass
        for mod in (self._modules or []):
            try:
                mod.stop()
            except Exception:                                # noqa: BLE001
                pass
        self._modules = []
        with self._lock:
            self._shift = None
        s, self._src = self._src, None
        if s is not None:
            try:
                s.stop()
            except Exception:                                # noqa: BLE001
                pass


class Tracker(threading.Thread):
    """Drives a chain's Doppler from a precomputed curve.

    A thread rather than a timer because it must die with the capture and must
    not depend on anything else running. It reads a curve (see
    predict.compute_doppler_curve) and NEVER touches a propagator: at 10 Hz,
    Skyfield in the loop would be both wasteful and, on a Pi 3, marginal.

    Time is measured monotonically from start. Wall-clock would let a GPS or RTC
    fix land mid-pass and jump the correction — the same reason curves are
    indexed by seconds since capture start rather than timestamps."""

    def __init__(self, chain, curve, carrier_hz, tick_hz=None, max_seconds=None,
                 clock=None):
        super().__init__(daemon=True)
        self.chain = chain
        self.curve = curve
        self.carrier_hz = float(carrier_hz)
        self.tick_hz = doppler.TICK_HZ if tick_hz is None else float(tick_hz)
        self.max_seconds = max_seconds
        import time as _t
        self.clock = clock or _t.monotonic
        self._stop = threading.Event()
        self.ticks = 0
        self.last_rate = None
        self.error = None

    def stop(self):
        self._stop.set()

    def run(self):
        t0 = self.clock()
        period = 1.0 / self.tick_hz
        while not self._stop.is_set():
            t = self.clock() - t0
            if self.max_seconds is not None and t >= self.max_seconds:
                break
            rate = doppler.tick_shift_rate(self.curve, t, self.carrier_hz,
                                           self.chain.lfo, self.chain.input_rate,
                                           self.chain.tone_offset)
            if rate is not None:
                try:
                    self.chain.set_doppler_hz(
                        doppler.shift_hz(doppler.curve_at(self.curve, t),
                                         self.carrier_hz))
                    self.last_rate = rate
                    self.ticks += 1
                except Exception as e:                       # noqa: BLE001
                    # A tracker that dies must not take the capture with it: an
                    # uncorrected recording is worth far more than none, and the
                    # error is reported rather than raised into a daemon thread
                    # where nothing would ever see it.
                    self.error = str(e)
                    break
            self._stop.wait(period)
