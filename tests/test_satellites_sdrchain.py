"""The DSP chain, verified against a SYNTHETIC signal — no dongle, no antenna,
no satellite, no weather.

Adopted after an afternoon of real passes produced four null recordings, none of
which tested any part of this design. Correctness of the chain must not depend on
whether a cubesat happened to be transmitting or where it was in the sky.

The generator plays the part of rtl_connector: it serves complex float IQ over a
local socket carrying a tone that walks exactly where a real downlink would,
given the parked geometry (baseband = doppler - LFO). The chain then has to put
that tone where it belongs and keep it there.

THE PHASE MUST BE INTEGRATED. exp(2j*pi*f(t)*t) with a changing f puts a
discontinuity at every sample, smears the tone across the spectrum, and turns a
working chain into a failing test. Every hand-rolled sweep generator hits this
once; it is written down here so this one does not.

Guarded on pycsdr AND numpy: this needs the DSP stack, so it does not run in the
minimal CI job. The point was never CI — it is that the test runs on demand, on
any box with the stack, without waiting for the sky.
"""
import math
import os
import socket
import struct
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))

import doppler  # noqa: E402  — pure, always importable
import sdrchain  # noqa: E402  — pure until start() is called

try:
    import connector
    connector.enable_dist_packages()
    import pycsdr  # noqa: F401
    _HAS_PYCSDR = True
except Exception:  # noqa: BLE001
    _HAS_PYCSDR = False

try:
    import numpy as np
    _HAS_NUMPY = True
except Exception:  # noqa: BLE001
    _HAS_NUMPY = False


class PlanTest(unittest.TestCase):
    """Rate arithmetic — pure, so it runs everywhere."""

    def test_fm_decimates_once_by_five(self):
        self.assertEqual(sdrchain.plan("fm"), (48_000, [5]))

    def test_ssb_decimates_twice_never_once_by_twenty(self):
        """A single 20:1 FIR needs a very narrow transition band and a long tap
        count; two short filters are far cheaper for the same stopband."""
        rate, stages = sdrchain.plan("usb")
        self.assertEqual(rate, 12_000)
        self.assertEqual(stages, [5, 4])
        self.assertEqual(math.prod(stages), doppler.INPUT_RATE_HZ // 12_000)

    def test_lsb_matches_usb(self):
        self.assertEqual(sdrchain.plan("lsb"), sdrchain.plan("usb"))

    def test_unknown_mode_has_no_chain(self):
        self.assertIsNone(sdrchain.plan("lrpt"))
        self.assertIsNone(sdrchain.plan(None))

    def test_a_rate_that_does_not_divide_is_refused(self):
        """250 kHz — the obvious choice, and the one that divides into neither
        48 kHz nor 12 kHz. Refusing beats silently resampling."""
        self.assertIsNone(sdrchain.plan("fm", 250_000))
        self.assertIsNone(sdrchain.plan("usb", 250_000))

    def test_sideband_sign_is_the_sideband(self):
        lo, hi = sdrchain.sideband_cut("usb")
        self.assertGreater(lo, 0)
        self.assertGreater(hi, lo)
        lo, hi = sdrchain.sideband_cut("lsb")
        self.assertLess(hi, 0)
        self.assertLess(lo, hi)


class _IQServer(threading.Thread):
    """Stands in for rtl_connector: serves complex float32 IQ on a socket.

    Feeds at REAL TIME. An earlier version ran at 2x to keep the test short, and
    the tracker — which measures wall-clock like it does in production — then
    corrected for a moment the signal had already left. 17 kHz of residual, and
    entirely the harness's fault."""

    def __init__(self, baseband_fn, rate, seconds, port):
        super().__init__(daemon=True)
        self.baseband_fn = baseband_fn
        self.rate = rate
        self.seconds = seconds
        self.port = port
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(1)
        self.stop_flag = threading.Event()
        # Rendered HERE, in the caller's thread, before anything can connect.
        # Doing it inside run() delayed accept() by however long the render took,
        # so the chain connected, the tracker started ticking, and the signal only
        # began seconds later — the tracker then corrected for a moment the
        # waveform had not reached, and over-shot by exactly that much.
        self.data = self._render()

    def _render(self):
        """Build the whole waveform BEFORE serving any of it.

        Generating 240k complex samples per second with per-sample struct.pack is
        CPU-bound in pure Python and cannot keep up with the clock. A server that
        falls behind emits a signal whose Doppler lags the wall clock, while the
        tracker corrects for the wall clock — so the chain gets blamed for the
        harness being slow. Precomputing makes the send loop pure I/O, and the
        pacing honest."""
        total = self.rate * self.seconds
        phase = 0.0
        pack = struct.Struct("<ff").pack
        cos, sin, two_pi = math.cos, math.sin, 2.0 * math.pi
        parts = []
        for n in range(total):
            phase += two_pi * self.baseband_fn(n / self.rate) / self.rate
            parts.append(pack(cos(phase), sin(phase)))
        return b"".join(parts)

    def run(self):
        data = self.data
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        step = (self.rate // 20) * 8                 # 50 ms of complex float
        for i in range(0, len(data), step):
            if self.stop_flag.is_set():
                break
            try:
                conn.sendall(data[i:i + step])
            except OSError:
                break
            time.sleep(0.05)                         # real time, deliberately
        try:
            conn.close()
        except OSError:
            pass

    def close(self):
        self.stop_flag.set()
        try:
            self.sock.close()
        except OSError:
            pass


def _drain(chain, seconds):
    """Pump the chain for a while and return the audio it produced."""
    out = bytearray()
    done = threading.Event()

    def pump():
        while not done.is_set():
            d = chain.read()
            if not d:
                break
            out.extend(d)
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    time.sleep(seconds)
    done.set()
    chain.stop()
    t.join(timeout=3)
    return bytes(out)


def _samples(audio):
    n = len(audio) // 2
    return struct.unpack(f"<{n}h", audio[:n * 2]) if n else ()


def _tone_hz(audio, rate, skip=0.25):
    """Dominant audio frequency by zero-crossing rate.

    Frequency, not level, because the chain ends in an Agc: any amplitude-based
    measure reads back the AGC's opinion rather than the signal's. Where the tone
    LANDS is exactly what the shift is responsible for, so it is what to measure.
    The first quarter is skipped — filter transients and AGC settling."""
    v = _samples(audio)
    mid = v[int(len(v) * skip):]
    if len(mid) < 200:
        return None
    crossings = sum(1 for a, b in zip(mid, mid[1:]) if (a < 0) != (b < 0))
    return crossings / 2.0 / (len(mid) / float(rate))


def _rms(audio, skip=0.25):
    v = _samples(audio)
    mid = v[int(len(v) * skip):]
    if not mid:
        return 0.0
    return math.sqrt(sum(x * x for x in mid) / len(mid))


@unittest.skipUnless(_HAS_PYCSDR, "pycsdr/DSP stack not installed")
class SyntheticChainTest(unittest.TestCase):
    """The acceptance test for the whole DSP design.

    Everything here runs on the CW/USB chain, because there the transmitted tone
    survives to the audio as a tone and its FREQUENCY can be measured. On the FM
    chain the demodulator's output is instantaneous frequency, which the
    deemphasis and AGC stages then rescale by an unknown factor — so a mean-based
    residual measured after them is not in hertz and not in anything else."""

    CARRIER = 435_575_000.0
    TONE = float(sdrchain.CW_TONE_HZ)
    # Sweep rate for the tracked/untracked pair. Chosen, not arbitrary: the
    # residual a working tracker leaves is PIPELINE LATENCY x SWEEP RATE, because
    # the audio coming out now was received ~60 ms ago while the correction is
    # aimed at now. At 4000 Hz/s that is 235 Hz and the test failed; at the real
    # peak Doppler rate of ~215 Hz/s it is ~13 Hz and invisible. 1000 Hz/s keeps
    # the run short while staying close enough to reality to mean something.
    SWEEP_HZ_S = 1000.0

    def _serve(self, doppler_fn, port, seconds):
        lfo = doppler.LFO_OFFSET_HZ
        # Exactly where a real downlink sits: the dongle parks at carrier + LFO,
        # so the signal appears at doppler - LFO.
        srv = _IQServer(lambda t: doppler_fn(t) - lfo,
                        doppler.INPUT_RATE_HZ, seconds + 2, port)
        # Constructing it renders the waveform; starting it only opens the door.
        srv.start()
        time.sleep(0.1)
        return srv

    def _chain(self, port):
        return sdrchain.SdrChain(port, "usb", self.CARRIER,
                                 tone_offset_hz=self.TONE).start()

    def test_a_stationary_carrier_lands_on_the_cw_tone(self):
        """Zero Doppler. The shift has the LFO to undo and the CW tone offset to
        apply, and the carrier must come out at 700 Hz — audible, and inside the
        sideband filter. Landing it at DC would be silence."""
        srv = self._serve(lambda t: 0.0, 45990, 4)
        chain = self._chain(45990)
        try:
            audio = _drain(chain, 4)
        finally:
            chain.stop(); srv.close()
        self.assertGreater(len(audio), 10_000, "chain produced almost no audio")
        tone = _tone_hz(audio, sdrchain.SSB_RATE)
        self.assertAlmostEqual(tone, self.TONE, delta=60,
                               msg=f"carrier landed at {tone:.0f} Hz, not {self.TONE:.0f}")

    def test_an_untracked_sweep_walks_out_of_the_window(self):
        """THE NEGATIVE CONTROL. A test that passes with correction and without
        it measures nothing. Uncorrected, a 4 kHz/s sweep leaves the 300-2700 Hz
        sideband filter within a second and the audio collapses — which is the
        XW-3 failure, reproduced on the bench in four seconds."""
        srv = self._serve(lambda t: self.SWEEP_HZ_S * t, 45991, 5)
        chain = self._chain(45991)
        try:
            audio = _drain(chain, 5)
        finally:
            chain.stop(); srv.close()
        self.assertGreater(len(audio), 10_000)
        self._untracked_rms = _rms(audio)
        tone = _tone_hz(audio, sdrchain.SSB_RATE)
        self.assertFalse(tone is not None and abs(tone - self.TONE) < 60,
                         "an UNTRACKED sweep must not hold the tone in place — "
                         "if it does, the tracked test proves nothing")

    def test_tracking_holds_the_tone_where_it_belongs(self):
        """The whole feature: a tone that moves, followed by the tracker driving
        set_doppler_hz off a curve, exactly as a pass does. Same sweep as the
        negative control above — the only difference is the correction."""
        def dop(t):
            return self.SWEEP_HZ_S * t
        srv = self._serve(dop, 45992, 7)
        chain = self._chain(45992)
        # A curve shaped like predict.compute_doppler_curve's: the factor is
        # dimensionless, so it is the shift divided by the carrier.
        curve = [(i * 0.5, dop(i * 0.5) / self.CARRIER) for i in range(19)]
        tr = sdrchain.Tracker(chain, curve, self.CARRIER, tick_hz=20)
        tr.start()
        try:
            audio = _drain(chain, 5)
        finally:
            tr.stop(); chain.stop(); srv.close()
        self.assertGreater(tr.ticks, 30, "tracker barely ran")
        self.assertIsNone(tr.error)
        tone = _tone_hz(audio, sdrchain.SSB_RATE)
        self.assertIsNotNone(tone, "no audio survived the sideband filter")
        # Tolerance covers the latency term above (~60 Hz at this sweep rate)
        # with room to spare, and is far tighter than the ~2 kHz the untracked
        # control drifts to.
        self.assertAlmostEqual(tone, self.TONE, delta=100,
                               msg=f"tracked tone drifted to {tone:.0f} Hz")

    def test_the_fm_chain_produces_audio(self):
        """FM's tail (Limit, NfmDeemphasis, Agc) cannot be checked by tone
        frequency — an FM discriminator fed a steady carrier emits DC. This only
        asserts the tail builds and runs; the shift itself is proven above, and
        it is the same Shift in both chains."""
        srv = self._serve(lambda t: 0.0, 45993, 4)
        chain = sdrchain.SdrChain(45993, "fm", self.CARRIER).start()
        try:
            audio = _drain(chain, 3)
        finally:
            chain.stop(); srv.close()
        self.assertGreater(len(audio), 10_000, "FM chain produced almost no audio")
        self.assertEqual(chain.out_rate, sdrchain.FM_RATE)


class _NoisyFmServer(_IQServer):
    """An FM-modulated carrier buried in additive complex noise.

    The plain _IQServer serves a clean constant-amplitude tone, which is the
    right instrument for "where did the shift put it" and useless for "how much
    noise reached the discriminator". This one carries a real modulated signal
    and a real noise floor, so the FM chain can be asked the only question that
    matters for a marginal pass: how much of what comes out is signal.

    Noise is added to the SAMPLES, not to the frequency argument. Frequency noise
    would be a differently-modulated carrier at full amplitude — the one thing an
    FM limiter is designed to ignore — and the test would measure nothing."""

    def __init__(self, rate, seconds, port, dev_hz, tone_hz, noise_amp, offset_hz):
        # Set before super().__init__, which renders immediately.
        self.dev_hz, self.tone_hz = float(dev_hz), float(tone_hz)
        self.noise_amp, self.offset_hz = float(noise_amp), float(offset_hz)
        super().__init__(lambda t: 0.0, rate, seconds, port)

    def _render(self):
        n = self.rate * self.seconds
        t = np.arange(n, dtype=np.float64) / self.rate
        # Integrated phase — never exp(2j*pi*f(t)*t); see the module docstring.
        inst = self.offset_hz + self.dev_hz * np.sin(2 * math.pi * self.tone_hz * t)
        phase = 2 * math.pi * np.cumsum(inst) / self.rate
        sig = np.exp(1j * phase)
        rng = np.random.default_rng(20260813)      # fixed: an A/B needs the SAME noise
        noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / math.sqrt(2)
        iq = (sig + self.noise_amp * noise).astype(np.complex64)
        return iq.view(np.float32).astype("<f4").tobytes()


@unittest.skipUnless(_HAS_PYCSDR and _HAS_NUMPY, "pycsdr/numpy not installed")
class ChannelFilterTest(unittest.TestCase):
    """Does a channel filter before FmDemod actually buy anything?

    THE CLAIM UNDER TEST. FirDecimate(5) anti-aliases at output Nyquist, so the
    discriminator is handed +/-24 kHz for a signal about +/-6 kHz wide (Carson:
    2 x (3 kHz deviation + 3 kHz audio)). Pre-detection noise power scales with
    bandwidth, so narrowing to +/-8 kHz should be worth around 10*log10(48/16),
    i.e. ~4.8 dB — and FM has a threshold, so near it a few dB is not "quieter",
    it is the difference between packets that demodulate and packets that do not.

    WHY A RATIO AND NOT A LEVEL. The chain ends in an Agc. Any absolute
    measurement reads back the AGC's opinion; the AGC scales signal and noise
    together, so their RATIO survives it. That is the same reason the tests above
    measure tone FREQUENCY rather than amplitude.

    WHAT WOULD MAKE THIS TEST A LIE. Comparing two runs against different noise —
    hence the fixed seed. And measuring with the filter so narrow it clips the FM
    sidebands, which shows up as the tone itself weakening, not just the noise;
    the sweep below prints the tone level for exactly that reason.

    WHAT TO EXPECT, so a wrong answer is recognisable as one. An ideal-brickwall
    numpy model of this same experiment predicts, at NOISE_AMP 1.4:

        none  -7.0 dB | 12k +6.2 | 10k +8.4 | 8k +11.0 | 6k +14.2 | 5k +16.0

    Far more than the ~4.8 dB a pure bandwidth argument gives, because FM is
    non-linear: post-detection noise power rises with the square of frequency, so
    the high end of the IF is worth disproportionately more than its share, and
    near threshold the improvement compounds. Expect the real chain to land BELOW
    those figures — pycsdr's Bandpass has a genuine transition band (0.05
    normalised is ~2.4 kHz at 48 kHz) where the model has none. A result far
    ABOVE them, or a negative one, means the filter is not doing what is written
    on it and the number should not be trusted."""

    TONE_HZ = 1200.0            # Bell 202 mark — the tone PCSAT actually carries
    DEV_HZ = 3000.0             # NBFM deviation for APRS
    NOISE_AMP = 1.4             # a marginal pass, not a strong one
    SECONDS = 4

    def _run(self, port, half_hz):
        srv = _NoisyFmServer(doppler.INPUT_RATE_HZ, self.SECONDS + 2, port,
                             self.DEV_HZ, self.TONE_HZ, self.NOISE_AMP,
                             -doppler.LFO_OFFSET_HZ)
        srv.start()
        time.sleep(0.1)
        chain = sdrchain.SdrChain(port, "fm", 145_825_000.0,
                                  channel_half_hz=half_hz).start()
        try:
            audio = _drain(chain, self.SECONDS - 1)
        finally:
            chain.stop(); srv.close()
        return audio

    def _tone_to_noise_db(self, audio):
        """Power at the modulating tone against everything else in the audio.

        Returns (ratio_db, tone_power_db) — the second so a filter that improves
        the ratio by destroying the signal is visible rather than celebrated."""
        v = np.array(_samples(audio), dtype=np.float64)
        v = v[len(v) // 4:]                       # drop AGC settling + transients
        self.assertGreater(len(v), 20000, "not enough audio to measure")
        v = v[:len(v) - (len(v) % 4096)]
        fr = v.reshape(-1, 4096) * np.hanning(4096)
        p = (np.abs(np.fft.rfft(fr, axis=1)) ** 2).mean(axis=0)
        f = np.fft.rfftfreq(4096, 1.0 / sdrchain.FM_RATE)
        tone = (f > self.TONE_HZ - 60) & (f < self.TONE_HZ + 60)
        # Everything audible that is not the tone or DC rumble. Harmonics of the
        # tone are deliberately left in the noise term: they are distortion, and
        # a filter that trades noise for distortion has not helped.
        rest = (f > 200) & (f < 20000) & ~tone
        tp, npow = p[tone].sum(), p[rest].sum()
        return (10 * np.log10(tp / npow), 10 * np.log10(tp))

    def test_a_channel_filter_beats_no_channel_filter(self):
        wide = self._run(46010, None)
        narrow = self._run(46011, 8000)
        w_snr, w_tone = self._tone_to_noise_db(wide)
        n_snr, n_tone = self._tone_to_noise_db(narrow)
        gain, tone_loss = n_snr - w_snr, w_tone - n_tone
        print(f"\n  no filter      : tone/noise {w_snr:6.2f} dB   tone {w_tone:7.2f} dB"
              f"\n  +/-8 kHz filter: tone/noise {n_snr:6.2f} dB   tone {n_tone:7.2f} dB"
              f"\n  GAIN           : {gain:+.2f} dB   (tone changed {-tone_loss:+.2f} dB)",
              file=sys.stderr)
        # Deliberately weak: this test exists to MEASURE, and the number it
        # measures is what decides the default. Asserting the theoretical 4.8 dB
        # would encode a prediction as a requirement. What must not happen is the
        # filter making things worse, or gutting the tone to flatter the ratio.
        self.assertGreater(gain, 0.0, f"channel filter did not help ({gain:+.2f} dB)")
        self.assertLess(tone_loss, 3.0,
                        f"filter cost {tone_loss:.2f} dB of tone — too narrow")

    @unittest.skipUnless(os.environ.get("OASIS_DSP_SWEEP"),
                         "set OASIS_DSP_SWEEP=1 — several captures, minutes not seconds")
    def test_sweep_reports_the_best_half_width(self):
        """The instrument that picks the constant. Opt-in: one capture per width.

        Run it on the box that will do the recording — the answer is allowed to
        differ between a Pi 3 and a Pi 5 only in how long it takes, not in what it
        says, and a disagreement there is itself worth knowing."""
        base_snr, base_tone = self._tone_to_noise_db(self._run(46020, None))
        print(f"\n  {'half-width':>12} {'tone/noise':>11} {'gain':>8} {'tone':>9}",
              file=sys.stderr)
        print(f"  {'none':>12} {base_snr:10.2f} dB {'—':>8} {base_tone:8.2f} dB",
              file=sys.stderr)
        best = (0.0, None)
        # Down through 4000 deliberately: the model turns over there (at low
        # noise +12.3 dB against +14.7 at 5 kHz, with the tone itself starting to
        # drop), and a sweep that stops before the knee cannot show where the
        # knee is. Carson for real APRS audio is 2 x (3000 + 2200) = 10.4 kHz,
        # i.e. +/-5.2 kHz, so the turnover is expected to sit right about there.
        for i, half in enumerate((12000, 10000, 8000, 6000, 5000, 4000)):
            snr, tone = self._tone_to_noise_db(self._run(46021 + i, half))
            print(f"  {half:9d} Hz {snr:10.2f} dB {snr - base_snr:+7.2f} dB "
                  f"{tone:8.2f} dB", file=sys.stderr)
            if snr - base_snr > best[0] and base_tone - tone < 3.0:
                best = (snr - base_snr, half)
        print(f"  best: {best[1]} Hz at {best[0]:+.2f} dB", file=sys.stderr)
        self.assertIsNotNone(best[1], "no half-width improved on no filter at all")


@unittest.skipUnless(_HAS_PYCSDR, "pycsdr/DSP stack not installed")
class ChainLifecycleTest(unittest.TestCase):
    def test_an_impossible_mode_is_refused_before_any_hardware(self):
        with self.assertRaises(RuntimeError):
            sdrchain.SdrChain(45999, "lrpt", 435e6)

    def test_a_shift_past_nyquist_is_refused_not_clamped(self):
        """Clamping would produce audio that demodulates and is quietly
        off-frequency, which is the worst of both outcomes."""
        c = sdrchain.SdrChain(45999, "fm", 435e6, lfo_offset_hz=200_000)
        with self.assertRaises(RuntimeError):
            c.set_doppler_hz(0.0)

    def test_stop_is_idempotent_before_start(self):
        sdrchain.SdrChain(45999, "fm", 435e6).stop()


if __name__ == "__main__":
    unittest.main()
