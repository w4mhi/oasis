"""A capture stops itself at LOS, and the limit reaches the RECORDING.

Written after an OSCAR 7 pass on 2026-08-12. The file came out 21 min 30 s long
against a MAX_SECONDS of 20 min, which is only possible if the cap never bounded
the capture at all — and it did not. `max_seconds` was handed to the Tracker,
whose run loop breaks on it; TrackedCapture's pump loops on `_stopped` alone.
So at 20 minutes the Doppler correction stopped and the recording carried on,
writing its final 90 seconds with the shift frozen at its last value, past the
end of a curve that was itself only computed for MAX_SECONDS — at the point in a
pass where Doppler moves fastest.

Two separate claims are pinned here, because fixing one without the other leaves
a working-looking system:

  1. the limit stops the CAPTURE, not just the tracker
  2. the limit is the PASS's length, with MAX_SECONDS demoted to a backstop
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, os.path.join(_ROOT, "server"),
          os.path.join(_ROOT, "services", "satellites")):
    if p not in sys.path:
        sys.path.insert(0, p)

import capture as CAP        # noqa: E402  (bare import — see the duplicate-module note)


class _Chain:
    """A chain that yields forever, so nothing but the limit can end the pump."""
    def __init__(self):
        self.stopped = threading.Event()

    def read(self):
        if self.stopped.is_set():
            return b""
        time.sleep(0.005)
        return b"\0\0"

    def stop(self):
        self.stopped.set()


class _Sink:
    def __init__(self):
        self.bytes = 0
        self.closed = 0

    def write(self, data):
        self.bytes += len(data)

    def close(self):
        self.closed += 1


def _cap(max_seconds):
    """A TrackedCapture wired to fakes — no dongle, no pycsdr, no chain build."""
    c = CAP.TrackedCapture(435_100_000, "usb", max_seconds=max_seconds)
    c.chain = _Chain()
    c.conn = mock.MagicMock()
    c.out_rate = 12_000
    c._pump = threading.Thread(target=c._run, daemon=True)
    c._pump.start()
    if c.max_seconds:
        c._timer = threading.Timer(float(c.max_seconds), c._expire)
        c._timer.daemon = True
        c._timer.start()
    return c


class AutoStopTest(unittest.TestCase):
    def test_the_limit_ends_the_capture_not_only_the_tracker(self):
        """THE bug. Before the fix the pump had no time bound of any kind, so
        this ran until the test did."""
        c = _cap(0.15)
        sink = c.add_sink(_Sink())
        # Waits on the SINK, not on poll(). poll() keys on the pump, which stop()
        # halts before it closes the sinks — so a capture reads as finished a
        # beat before its WAV is finalised. That ordering is pre-existing and
        # fine for listen.py, which only asks whether the dongle is still held,
        # but it means poll() is the wrong thing to wait on here.
        deadline = time.monotonic() + 3
        while sink.closed == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(c.poll(), "capture still running past its limit")
        self.assertTrue(c.stopped_at_limit)
        self.assertGreater(sink.bytes, 0, "it should have recorded, then stopped")
        self.assertEqual(sink.closed, 1, "the WAV must be finalised, exactly once")

    def test_an_operator_stop_and_the_timer_cannot_both_tear_down(self):
        """They race in the real world — a Stop pressed as LOS comes up. Running
        the teardown twice closes an already-closed sink and double-stops the
        connector."""
        c = _cap(0.05)
        sink = c.add_sink(_Sink())
        time.sleep(0.2)                     # let the timer fire
        c.stop()                            # ...then press Stop on top of it
        c.stop()                            # and again, for good measure
        self.assertEqual(sink.closed, 1)
        self.assertEqual(c.conn.stop.call_count, 1)

    def test_no_limit_means_no_timer_rather_than_a_zero_one(self):
        """A falsy max_seconds must leave the capture unbounded, not schedule an
        immediate stop — start_tracked's callers can legitimately pass None."""
        c = _cap(None)
        self.assertIsNone(c._timer)
        time.sleep(0.1)
        self.assertIsNone(c.poll(), "an unbounded capture must keep running")
        c.stop()

    def test_stopping_cancels_the_timer(self):
        """Otherwise a pending timer fires minutes after the capture is gone and
        tears down whatever _state now points at."""
        c = _cap(30)
        t = c._timer
        c.stop()
        self.assertFalse(t.is_alive())
        self.assertFalse(c.stopped_at_limit, "a manual stop is not a limit stop")


class CaptureSecondsTest(unittest.TestCase):
    """_capture_seconds: LOS is the duration, MAX_SECONDS is only a backstop."""

    def setUp(self):
        # Dotted, NOT bare `import routes` — the repo has a top-level `routes`
        # package (server/routes/), and under `unittest discover` some other
        # test has usually already put it in sys.modules, so a bare import here
        # silently hands back the SERVER blueprint package instead. It passed
        # alone and errored under discover, which is the same trap documented in
        # test_satellites_routes.py and test_satellites_horizon_route.py.
        from services.satellites import routes
        self.R = routes

    def _seconds(self, los):
        with mock.patch.object(self.R, "_seconds_to_los", return_value=los):
            return self.R._capture_seconds(25544)

    def test_a_pass_ending_soon_records_to_los_plus_the_tail(self):
        self.assertEqual(self._seconds(300), 300 + self.R.LOS_TAIL_S)

    def test_max_seconds_still_caps_an_absurdly_long_pass(self):
        """A geostationary or badly-predicted bird must not record forever just
        because its 'pass' never ends."""
        import listen
        self.assertEqual(self._seconds(99_999), listen.MAX_SECONDS)

    def test_an_unresolvable_pass_falls_back_to_the_cap_not_to_forever(self):
        """None from _seconds_to_los means 'could not predict', and the safe
        reading of that is the old blanket cap — never 'no limit'."""
        import listen
        self.assertEqual(self._seconds(None), listen.MAX_SECONDS)

    def test_arming_seconds_before_los_still_yields_a_usable_file(self):
        """The tail alone would do it here, but the floor is what guarantees it
        if the tail is ever tuned to zero."""
        self.assertGreaterEqual(self._seconds(0), self.R.MIN_CAPTURE_S)


class StreamBoundTest(unittest.TestCase):
    def test_a_stream_is_bounded_too(self):
        """A stream past LOS is a browser tab holding the dongle while nothing is
        coming down. It carried no `timeout` at all before."""
        import listen
        cmd, _mime = listen.stream_command(435_100_000, max_seconds=600,
                                           which=lambda b: "/usr/bin/" + b)
        self.assertIn("timeout 600 rtl_fm", cmd)

    def test_no_limit_keeps_the_old_unbounded_shape(self):
        import listen
        cmd, _mime = listen.stream_command(435_100_000,
                                           which=lambda b: "/usr/bin/" + b)
        self.assertNotIn("timeout", cmd)


if __name__ == "__main__":
    unittest.main()
