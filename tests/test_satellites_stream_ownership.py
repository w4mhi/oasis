"""Who stops the capture when a listener goes away.

Listening with nothing recording used to drop to the rtl_fm shell pipeline,
which sounds audibly different from the tracked chain — that chain ends in an
Agc and rtl_fm ends in nothing, so the same pass arrived quiet on one path and
level on the other, and it never retuned for Doppler either. The fix is to let
a listener start a tracked capture of its own, with no WavSink.

That introduces the one thing the attach path never had to think about:
OWNERSHIP. A stream attached to somebody's recording must NOT stop it when the
tab closes; a stream that started its own capture MUST, or a closed browser tab
holds the dongle until the box is rebooted, with the UI showing nothing amiss.
Both directions are wrong in a way no other test would notice, so both are
pinned here.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
# server/ is on the list because routes.py imports appconfig from there. Bare
# imports throughout, matching how routes.py imports its own siblings — see the
# duplicate-module note in tests/test_satellites_autostop.py.
for _p in (_ROOT, os.path.join(_ROOT, "server"),
           os.path.join(_ROOT, "services", "satellites")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import capture  # noqa: E402
import listen  # noqa: E402


class _FakeCap:
    """Enough of a TrackedCapture for the sink bookkeeping."""

    out_rate = 48000

    def __init__(self):
        self.sinks = []
        self.removed = []

    def add_sink(self, sink):
        self.sinks.append(sink)
        return sink

    def remove_sink(self, sink):
        self.removed.append(sink)


class StreamOwnership(unittest.TestCase):
    def setUp(self):
        # No MP3 encoder → the raw-PCM branch, which needs no subprocess and is
        # the same ownership logic. stream_encoder is consulted by the helper.
        self._enc = listen.stream_encoder
        listen.stream_encoder = lambda rate, **kw: (None, None)
        self._stop = listen.stop
        self.stopped = []
        listen.stop = lambda: self.stopped.append(True)

    def tearDown(self):
        listen.stream_encoder = self._enc
        listen.stop = self._stop

    def _helper(self):
        # Loaded BY PATH under a unique name. A bare `import routes` passes when
        # this file runs alone and picks up whichever service's routes.py another
        # test imported first under `unittest discover` — here it resolved to a
        # module with no _stream_from_capture, so the suite failed only in
        # discovery mode. Same trap as the satellites duplicate-module note.
        import importlib.util
        name = "_sat_routes_stream_ownership"
        mod = sys.modules.get(name)
        if mod is None:
            path = os.path.join(_ROOT, "services", "satellites", "routes.py")
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        return mod._stream_from_capture

    def test_an_attached_listener_never_stops_the_recording(self):
        """own=False: the recording owns the capture. The listener leaving may
        only detach the sink — stopping here would end the operator's recording
        from a browser they closed, and the pass is not coming back."""
        cap = _FakeCap()
        gen, mime, release = self._helper()(cap, own=False)
        self.assertEqual(mime, "audio/L16")
        release()                        # what Response.call_on_close does
        self.assertEqual(cap.removed, cap.sinks, "the sink must be detached")
        self.assertEqual(self.stopped, [], "must NOT stop a capture it does not own")

    def test_an_owning_listener_stops_its_own_capture(self):
        """own=True: nothing else refers to this capture. Unstopped, a closed tab
        holds the dongle indefinitely and every later capture fails with
        'already capturing' for no reason the operator can see."""
        cap = _FakeCap()
        _gen, _mime, release = self._helper()(cap, own=True)
        release()
        self.assertEqual(cap.removed, cap.sinks, "the sink must be detached")
        self.assertEqual(len(self.stopped), 1, "must stop the capture it started")

    def test_release_is_idempotent(self):
        """It is wired to BOTH the response close and the generator's finally,
        because a generator closed before its first next() never runs finally at
        all — Python discards it — so relying on the generator alone loses the
        dongle when a client disconnects before reading a byte. Both firing must
        not stop the capture twice."""
        cap = _FakeCap()
        _gen, _mime, release = self._helper()(cap, own=True)
        release()
        release()
        release()
        self.assertEqual(len(self.stopped), 1, "stop must happen exactly once")
        self.assertEqual(len(cap.removed), 1, "the sink must be detached once")

    def test_a_generator_never_consumed_still_releases(self):
        """The regression that motivated the flag above: build the response,
        client vanishes, nothing is ever read."""
        cap = _FakeCap()
        gen, _mime, release = self._helper()(cap, own=True)
        gen.close()                      # finally does NOT run — never started
        release()                        # ...so this is what saves the dongle
        self.assertEqual(len(self.stopped), 1)

    def test_the_sink_is_attached_before_the_generator_is_consumed(self):
        """The sink has to exist from the moment the response is built, or audio
        produced between building it and the first read is dropped on the floor."""
        cap = _FakeCap()
        _gen, _mime, _release = self._helper()(cap, own=True)
        self.assertEqual(len(cap.sinks), 1)
        self.assertIsInstance(cap.sinks[0], capture.StreamSink)


if __name__ == "__main__":
    unittest.main()
