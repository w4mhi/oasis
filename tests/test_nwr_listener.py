import io
import os
import queue
import signal
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import listener  # noqa: E402

TOR = "ZCZC-WXR-TOR-053033+0100-2291200-KSEW/NWS-"


class _FakeProc:
    """Stand-in for a subprocess.Popen handle."""
    def __init__(self):
        self.signals = []
        self.killed = False
        self.waits = 0

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits += 1
        return 0

    def poll(self):
        return None


class _Sink:
    """Stand-in for multimon-ng's stdin."""
    def __init__(self, fail_after=None):
        self.written = b""
        self.fail_after = fail_after
        self.flushed = 0

    def write(self, b):
        if self.fail_after is not None and len(self.written) >= self.fail_after:
            raise BrokenPipeError("decoder died")
        self.written += b

    def flush(self):
        self.flushed += 1


class ChannelsTest(unittest.TestCase):
    def test_seven_nwr_channels(self):
        self.assertEqual(len(listener.CHANNELS), 7)
        self.assertEqual(listener.CHANNELS[0], ("WX1", 162400000))
        self.assertEqual(listener.CHANNELS[-1], ("WX7", 162550000))

    def test_channels_are_25_khz_apart(self):
        hz = [h for _, h in listener.CHANNELS]
        self.assertEqual({b - a for a, b in zip(hz, hz[1:])}, {25000})


class CommandTest(unittest.TestCase):
    def test_rtl_command_is_argv_not_a_shell_string(self):
        argv = listener.rtl_command(162550000, "auto", 0)
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], "rtl_fm")
        self.assertIn("-f", argv)
        self.assertIn("162550000", argv)
        self.assertIn("22050", argv)
        self.assertNotIn("-d", argv)

    def test_rtl_command_pins_the_dongle_by_serial(self):
        argv = listener.rtl_command(162550000, "auto", 0, device_serial="00000002")
        i = argv.index("-d")
        self.assertEqual(argv[i + 1], "00000002")

    def test_multimon_command(self):
        self.assertEqual(listener.multimon_command(),
                         ["multimon-ng", "-t", "raw", "-a", "EAS", "-"])


class PumpTest(unittest.TestCase):
    def test_every_chunk_reaches_the_decoder(self):
        src = io.BytesIO(b"x" * 10000)
        sink = _Sink()
        listener.pump(src, sink, [], chunk=4096)
        self.assertEqual(len(sink.written), 10000)

    def test_subscribers_get_the_same_bytes(self):
        src = io.BytesIO(b"y" * 8192)
        sink = _Sink()
        q = queue.Queue(maxsize=10)
        listener.pump(src, sink, [q], chunk=4096)
        got = b""
        while not q.empty():
            got += q.get_nowait()
        self.assertEqual(got, b"y" * 8192)

    def test_a_full_subscriber_queue_never_stalls_the_decoder(self):
        # A browser tab that stops reading must lose audio, not the decode.
        src = io.BytesIO(b"z" * 40960)
        sink = _Sink()
        q = queue.Queue(maxsize=1)
        listener.pump(src, sink, [q], chunk=4096)
        self.assertEqual(len(sink.written), 40960)

    def test_a_dead_decoder_ends_the_pump(self):
        src = io.BytesIO(b"w" * 40960)
        sink = _Sink(fail_after=8192)
        listener.pump(src, sink, [], chunk=4096)
        self.assertLessEqual(len(sink.written), 12288)


class DecodeTest(unittest.TestCase):
    def test_valid_headers_are_delivered(self):
        seen = []
        listener.decode_lines(["EAS: " + TOR, "\n"], seen.append)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["event"], "TOR")

    def test_noise_lines_are_ignored(self):
        seen = []
        listener.decode_lines(
            ["multimon-ng 1.2.0", "", "Enabled demodulators: EAS", "garbage"],
            seen.append)
        self.assertEqual(seen, [])

    def test_a_handler_that_raises_does_not_stop_decoding(self):
        # An unwritable alert store must lose a record, not the watch.
        seen = []

        def handler(rec):
            if not seen:
                seen.append(rec)
                raise OSError("disk full")
            seen.append(rec)

        listener.decode_lines(["EAS: " + TOR, "EAS: " + TOR], handler)
        self.assertEqual(len(seen), 2)


class WrapperTest(unittest.TestCase):
    def test_answers_its_own_token_and_delegates_the_rest(self):
        w = listener.is_active_wrapper(lambda u: u == "dump1090-fa")
        self.assertFalse(w("nwr-listen"))          # not listening in a fresh process
        self.assertTrue(w("dump1090-fa"))
        self.assertFalse(w("pat-direwolf"))


class PreconditionsTest(unittest.TestCase):
    def test_reports_missing_binaries_by_name(self):
        p = listener.preconditions(which=lambda b: None, run=lambda *a, **k: None)
        self.assertIn("rtl_fm", p["missing_deps"])
        self.assertIn("multimon-ng", p["missing_deps"])
        self.assertFalse(p["dongle_present"])


class _StateResetMixin:
    """start()/stop() touch module-level _state; leave it as found."""
    def setUp(self):
        super().setUp()
        self._saved = dict(listener._state)
        listener._state.update({"rtl": None, "mm": None, "channel_hz": None,
                                 "started": 0.0, "subs": [], "last_error": None,
                                 "alerts_seen": 0})

    def tearDown(self):
        listener._state.clear()
        listener._state.update(self._saved)
        super().tearDown()


class StartPartialFailureTest(_StateResetMixin, unittest.TestCase):
    def test_a_dead_multimon_spawn_terminates_the_already_running_rtl_fm(self):
        # rtl_fm spawns fine; multimon-ng then fails to spawn (missing binary
        # raced past preconditions, or an OS-level fork failure). The already
        # running rtl_fm must not be orphaned holding the dongle.
        rtl_proc = _FakeProc()

        def fake_popen(argv, **kwargs):
            if argv[0] == "rtl_fm":
                return rtl_proc
            raise OSError("multimon-ng: no such file or directory")

        # REQUIRED_BINARIES may genuinely be missing on the dev machine (no
        # multimon-ng on a Mac) — that's not what this test is about, so
        # force preconditions past the missing-deps gate.
        with mock.patch.object(listener.sdr_rx, "missing_deps",
                                lambda *a, **k: []):
            result = listener.start(162550000, popen=fake_popen)

        self.assertEqual(result,
                          {"ok": False,
                           "error": "multimon-ng: no such file or directory",
                           "code": "NWR_START_FAILED"})
        self.assertIn(signal.SIGTERM, rtl_proc.signals)
        self.assertGreaterEqual(rtl_proc.waits, 1)
        # and the orphan must not linger in _state either
        self.assertIsNone(listener._state["rtl"])
        self.assertIsNone(listener._state["mm"])


class StopSentinelTest(_StateResetMixin, unittest.TestCase):
    def test_a_full_subscriber_queue_still_receives_the_sentinel(self):
        q = queue.Queue(maxsize=1)
        q.put_nowait(b"stale-audio")   # already full: the stalled-reader case
        listener._state["subs"] = [q]

        listener.stop()

        self.assertEqual(q.get_nowait(), b"")


if __name__ == "__main__":
    unittest.main()
