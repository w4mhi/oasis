"""capture.py's sinks and the backend choice.

The sinks are pure Python and testable without any DSP stack; the chain
orchestration itself is covered by the synthetic harness in
test_satellites_sdrchain.py, which needs pycsdr.
"""
import os
import struct
import sys
import tempfile
import unittest
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))

import capture  # noqa: E402
import listen  # noqa: E402


class WavSinkTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "sub", "pass.wav")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_it_writes_a_playable_wav(self):
        s = capture.WavSink(self.path, 48000)
        s.write(struct.pack("<4h", 1, -1, 2, -2))
        s.close()
        with wave.open(self.path, "rb") as w:
            self.assertEqual(w.getframerate(), 48000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getnframes(), 4)

    def test_the_header_is_finalised_even_if_nothing_was_written(self):
        """A capture killed a second after it started must still leave a file
        that opens. The rtl_fm path gets this by SIGTERM-ing sox and hoping."""
        capture.WavSink(self.path, 12000).close()
        with wave.open(self.path, "rb") as w:
            self.assertEqual(w.getnframes(), 0)

    def test_it_creates_the_directory(self):
        capture.WavSink(self.path, 48000).close()
        self.assertTrue(os.path.exists(self.path))


class StreamSinkTest(unittest.TestCase):
    def test_it_yields_what_was_written(self):
        s = capture.StreamSink()
        s.write(b"abc")
        s.write(b"def")
        s.close()
        self.assertEqual(b"".join(s.generate()), b"abcdef")

    def test_a_stalled_listener_drops_instead_of_blocking(self):
        """THE rule that makes one-capture-many-sinks safe. A browser that
        stopped reading must not be able to back up into the pump thread and
        stall the WAV on disk — so the listener loses audio and the recording
        never does."""
        s = capture.StreamSink(maxsize=4)
        for _ in range(50):
            s.write(b"x")           # must not block, must not raise
        self.assertGreater(s.dropped, 0)
        self.assertEqual(s.q.qsize(), 4)

    def test_close_unblocks_a_waiting_generator(self):
        s = capture.StreamSink()
        g = s.generate()
        s.close()
        self.assertEqual(list(g), [])

    def test_writing_after_close_is_ignored(self):
        s = capture.StreamSink()
        s.close()
        s.write(b"late")
        self.assertTrue(s.closed)


class BackendChoiceTest(unittest.TestCase):
    """Which capture path runs. Falling back is a normal outcome — a station
    without the DSP stack is not broken, it is uncorrected — but it must never
    be a silent one."""

    def test_every_prerequisite_is_reported_separately(self):
        """'tracked capture is unavailable' is not something an operator can act
        on; 'rtl_connector is not installed' is."""
        p = listen.tracked_prereqs(which=lambda b: None, has_tle=False)
        self.assertEqual(set(p), {"pycsdr", "rtl_connector", "predictor", "tle"})
        self.assertFalse(p["rtl_connector"])
        self.assertFalse(p["tle"])

    def test_a_missing_tle_alone_forces_the_uncorrected_path(self):
        """No TLE means no curve, and a tracker with no curve corrects nothing —
        so the honest answer is the baseline path, not a tracked capture that
        silently does not track."""
        self.assertEqual(
            listen.capture_backend(which=lambda b: "/usr/bin/" + b, has_tle=False),
            "rtl_fm")

    def test_a_missing_connector_alone_forces_the_uncorrected_path(self):
        self.assertEqual(listen.capture_backend(which=lambda b: None), "rtl_fm")


class SidecarPruneTest(unittest.TestCase):
    """A recording's sidecar goes with it. Without that, every pruned WAV leaves
    its .json behind forever — invisible, unbounded, and eventually a directory
    of metadata for files that no longer exist."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rec(self, name, size, with_sidecar=True):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(b"\0" * size)
        if with_sidecar:
            with open(os.path.splitext(p)[0] + ".json", "w") as fh:
                fh.write("{}")
        return p

    def test_pruning_takes_the_pair(self):
        old = self._rec("a.wav", 1000)
        os.utime(old, (1, 1))
        self._rec("b.wav", 1000)
        listen.prune_recordings(self.dir, max_bytes=1200)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(os.path.splitext(old)[0] + ".json"))

    def test_a_recording_without_a_sidecar_prunes_cleanly(self):
        old = self._rec("a.wav", 1000, with_sidecar=False)
        os.utime(old, (1, 1))
        self._rec("b.wav", 1000, with_sidecar=False)
        listen.prune_recordings(self.dir, max_bytes=1200)
        self.assertFalse(os.path.exists(old))

    def test_a_surviving_recording_keeps_its_sidecar(self):
        old = self._rec("a.wav", 100)
        os.utime(old, (1, 1))
        keep = self._rec("b.wav", 100)
        listen.prune_recordings(self.dir, max_bytes=10_000)
        self.assertTrue(os.path.exists(os.path.splitext(keep)[0] + ".json"))
        self.assertTrue(os.path.exists(os.path.splitext(old)[0] + ".json"))


if __name__ == "__main__":
    unittest.main()
