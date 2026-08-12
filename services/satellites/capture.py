"""Tracked capture: connector + chain + tracker + wherever the audio goes.

This is the piece that turns four independent parts into one thing with a
lifetime. listen.py owns the dongle lock and the disk budget; this owns what
happens between "start" and "stop" on the corrected path.

ONE CAPTURE, MANY SINKS
-----------------------
The rtl_fm path is a shell pipeline, so its audio can go to exactly one place:
recording and listening are mutually exclusive because there is one stdout. Here
the audio arrives in-process, so a pump thread reads it once and fans it out —
you can record a pass and listen to it at the same time, and several browsers can
listen at once.

The rule that makes that safe is that the sinks are NOT equal. A recording must
never lose a sample, so it applies backpressure to nothing and is written
synchronously. A listener is allowed to fall behind and drop audio, because a
stalled browser must not be able to corrupt the file on disk. That asymmetry is
the whole design.

WHY THE WAV IS WRITTEN HERE RATHER THAN PIPED TO sox
----------------------------------------------------
Python's wave module needs no subprocess and finalises its header in a finally
block, so a capture that is killed still leaves a playable file. The rtl_fm path
gets that by SIGTERM-ing sox and hoping; this path does not have to.
"""
import os
import queue
import threading
import wave

import connector
import doppler
import sdrchain

# How much audio a listener may fall behind before frames start being dropped.
# ~4 s at 48 kHz mono s16. Generous enough that a browser hiccup is invisible,
# small enough that a dead listener cannot grow memory without bound.
STREAM_QUEUE_CHUNKS = 64


class WavSink:
    """A recording. Never drops, always finalises."""

    def __init__(self, path, rate, channels=1, sampwidth=2):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._w = wave.open(path, "wb")
        self._w.setnchannels(channels)
        self._w.setsampwidth(sampwidth)
        self._w.setframerate(int(rate))
        self.bytes = 0

    def write(self, data):
        self._w.writeframes(data)
        self.bytes += len(data)

    def close(self):
        try:
            self._w.close()
        except Exception:                                    # noqa: BLE001
            pass


class StreamSink:
    """A listener. Allowed to fall behind; never allowed to stall the recorder."""

    def __init__(self, maxsize=STREAM_QUEUE_CHUNKS):
        self.q = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        self.closed = False

    def write(self, data):
        if self.closed:
            return
        try:
            self.q.put_nowait(data)
        except queue.Full:
            # Drop, do not block. A browser that stopped reading must not be able
            # to back up into the pump thread and stall the WAV on disk.
            self.dropped += 1

    def close(self):
        self.closed = True
        try:
            self.q.put_nowait(b"")       # unblock a waiting generator
        except queue.Full:
            pass

    def generate(self):
        """Yield audio until the capture ends or the client disconnects."""
        try:
            while True:
                data = self.q.get()
                if not data:
                    break
                yield data
        finally:
            self.closed = True


class TrackedCapture:
    """One Doppler-corrected capture. Owns the connector, the chain and the
    tracker, and outlives none of them."""

    def __init__(self, downlink_hz, demod, carrier_hz=None, curve=None,
                 device_serial=None, gain=None, ppm="0", tone_offset_hz=0.0,
                 max_seconds=None):
        self.downlink_hz = int(downlink_hz)
        self.demod = demod
        self.carrier_hz = float(carrier_hz or downlink_hz)
        self.curve = curve or []
        self.device_serial = device_serial
        self.gain = gain or connector.DEFAULT_GAIN
        self.ppm = ppm
        self.tone_offset_hz = float(tone_offset_hz)
        self.max_seconds = max_seconds
        self.centre_hz = int(doppler.centre_hz(self.downlink_hz))
        self.conn = None
        self.chain = None
        self.tracker = None
        self._sinks = []
        self._sink_lock = threading.Lock()
        self._pump = None
        self._stopped = threading.Event()
        self.out_rate = None
        self.error = None

    # ── sinks ────────────────────────────────────────────────────────────────
    def add_sink(self, sink):
        with self._sink_lock:
            self._sinks.append(sink)
        return sink

    def remove_sink(self, sink):
        with self._sink_lock:
            if sink in self._sinks:
                self._sinks.remove(sink)
        sink.close()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        """Park the dongle, build the chain, start tracking, start pumping.

        The connector comes up FIRST and its socket is proven live before the
        chain is told to connect to it — TcpSource against a port nothing is
        listening on would fail in a way that reads as a DSP problem."""
        plan = sdrchain.plan(self.demod)
        if plan is None:
            raise RuntimeError(f"no tracked chain for mode {self.demod!r}")
        self.out_rate = plan[0]
        self.conn = connector.Connector(
            self.centre_hz, doppler.INPUT_RATE_HZ, gain=self.gain,
            ppm=self.ppm, device_serial=self.device_serial)
        port = self.conn.start()
        try:
            self.chain = sdrchain.SdrChain(
                port, self.demod, self.carrier_hz,
                tone_offset_hz=self.tone_offset_hz).start()
        except Exception:
            self.conn.stop()
            raise
        if self.curve:
            self.tracker = sdrchain.Tracker(
                self.chain, self.curve, self.carrier_hz,
                max_seconds=self.max_seconds)
            self.tracker.start()
        self._pump = threading.Thread(target=self._run, daemon=True)
        self._pump.start()
        return self

    def _run(self):
        try:
            while not self._stopped.is_set():
                data = self.chain.read()
                if not data:
                    break
                with self._sink_lock:
                    sinks = list(self._sinks)
                for s in sinks:
                    try:
                        s.write(data)
                    except Exception as e:                   # noqa: BLE001
                        # One bad sink must not end the capture. A failed WAV
                        # write is worth reporting; it is not worth also losing
                        # the live audio someone is listening to.
                        self.error = f"{type(s).__name__}: {e}"
                        self.remove_sink(s)
        finally:
            self._stopped.set()

    def is_running(self):
        return self._pump is not None and self._pump.is_alive() \
            and not self._stopped.is_set()

    # Popen-compatible enough for listen.py's _state, which only ever asks
    # whether the thing is still going.
    def poll(self):
        return None if self.is_running() else 0

    def stop(self):
        """Tear down in the order that cannot hang: pump first (via the chain's
        reader, which is what a blocked read is waiting on), then the tracker,
        then the connector, then the sinks."""
        self._stopped.set()
        if self.chain is not None:
            self.chain.stop()
        if self.tracker is not None:
            self.tracker.stop()
        if self._pump is not None:
            self._pump.join(timeout=3)
        if self.conn is not None:
            self.conn.stop()
        with self._sink_lock:
            sinks, self._sinks = list(self._sinks), []
        for s in sinks:
            s.close()

    # ── introspection ────────────────────────────────────────────────────────
    def doppler_now(self, t_s):
        """Doppler in Hz at t_s seconds into the capture, for the status view."""
        f = doppler.curve_at(self.curve, t_s)
        return None if f is None else doppler.shift_hz(f, self.carrier_hz)

    def status(self, t_s=0.0):
        dop = self.doppler_now(t_s)
        return {
            "backend": "csdr",
            "tracked": bool(self.tracker is not None),
            "centre_hz": self.centre_hz,
            "doppler_hz": None if dop is None else round(dop, 1),
            "corrected_hz": None if dop is None else int(round(self.carrier_hz + dop)),
            "ticks": getattr(self.tracker, "ticks", 0) if self.tracker else 0,
            "tracker_error": getattr(self.tracker, "error", None) if self.tracker else None,
            "out_rate": self.out_rate,
            "error": self.error,
        }
