import io
import os
import queue
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import listener  # noqa: E402

TOR = "ZCZC-WXR-TOR-053033+0100-2291200-KSEW/NWS-"


class _FakeProc:
    """Stand-in for a subprocess.Popen handle.

    exit_code=None means "still running" (poll() -> None), matching a live
    rtl_fm; a real exit code models the ~200 ms usb_claim_interface death a
    dongle-busy start hits (Finding 3). `stderr` is a bytes source for the
    real drain thread that reads it -- BytesIO, not a fake, since that thread
    calls the real .read().
    """
    def __init__(self, exit_code=None, stderr=b""):
        self.signals = []
        self.killed = False
        self.waits = 0
        self._exit_code = exit_code
        self.stderr = io.BytesIO(stderr)
        # Empty by default: a fully "still running" fake would otherwise send
        # the real _reader/_decoder threads into an AttributeError on
        # .stdout/.stdin that only ever shows up as noise in the test log,
        # never as a test failure.
        self.stdout = io.BytesIO()
        self.stdin = io.BytesIO()

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits += 1
        return 0

    def poll(self):
        return self._exit_code


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
                                 "alerts_seen": 0, "scanning": False})

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


class RealSubprocessRegressionTest(_StateResetMixin, unittest.TestCase):
    """Finding 1: every pump/decode test above uses a fake sink or a plain
    Python list of strings, both of which happily accept whatever pump()
    or decode_lines() hands them. `text=True, bufsize=1` on multimon-ng's
    REAL Popen call turned its real stdin into a TextIOWrapper -- so pump()
    writing rtl_fm's raw bytes into it raised TypeError, which is not in
    pump()'s caught exception tuple, silently killing the nwr-pump thread on
    the first chunk. None of the fake-based tests above could ever have
    caught that, because none of them go through a real OS pipe.

    This spawns REAL subprocess.Popen objects in exactly the kwarg shape
    start() uses (argv swapped for `cat`, which echoes stdin to stdout
    unmodified byte for byte) and drives an actual SAME header through the
    actual pump -> mm.stdin -> mm.stdout -> decode_lines -> on_header path.
    """

    @staticmethod
    def _cat_popen(argv, **kwargs):
        # `cat` stands in for both rtl_fm and multimon-ng. Forwarding the
        # REAL kwargs start() passes (rather than hand-picking them) is the
        # whole point: this is what breaks if text=True/bufsize=1 come back,
        # or if some future change swaps stdin=PIPE for something else.
        kwargs.setdefault("stdin", subprocess.PIPE)
        return subprocess.Popen(["cat"], **kwargs)

    def test_a_real_same_header_survives_a_real_pipe_end_to_end(self):
        seen = []
        with mock.patch.object(listener.sdr_rx, "missing_deps", lambda *a, **k: []):
            result = listener.start(162550000, popen=self._cat_popen,
                                    on_header=seen.append, startup_check_s=0)
        self.assertTrue(result["ok"], result)
        rtl, mm = listener._state["rtl"], listener._state["mm"]
        try:
            rtl.stdin.write(("EAS: " + TOR + "\n").encode("ascii"))
            rtl.stdin.flush()
            rtl.stdin.close()          # EOF -> cat exits -> pump() ends cleanly
            deadline = time.time() + 5
            while not seen and time.time() < deadline:
                time.sleep(0.05)
        finally:
            listener.stop()
            # Belt and suspenders on top of stop(): _decoder's finally can
            # null _state["mm"] the instant mm's stdout hits EOF, which can
            # race ahead of this thread reaching stop() -- and stop() only
            # reaps whatever is still IN _state. Reaping our own local
            # references directly means the cat processes are never left for
            # the GC to warn about, regardless of which side of that race
            # this thread lands on.
            for proc in (rtl, mm):
                try:
                    proc.wait(timeout=2)
                except Exception:      # noqa: BLE001
                    pass
                for stream in (proc.stdout, proc.stderr):
                    try:
                        stream.close()
                    except Exception:  # noqa: BLE001
                        pass
        self.assertEqual(len(seen), 1, "no SAME header reached on_header -- "
                         "the decode pipeline never delivered a byte")
        self.assertEqual(seen[0]["event"], "TOR")
        self.assertEqual(seen[0]["station"], "KSEW/NWS")


class StartLivenessTest(_StateResetMixin, unittest.TestCase):
    """Finding 3: a dongle-busy start used to report {"ok": True} because
    that only meant Popen() executed, not that rtl_fm actually claimed the
    tuner. stderr=DEVNULL threw away the one line (usb_claim_interface) that
    would have explained it."""

    def test_a_dongle_claimed_by_another_service_is_reported_not_silent(self):
        rtl_proc = _FakeProc(exit_code=1, stderr=b"usb_claim_interface error -6\n")
        mm_proc = _FakeProc()

        def fake_popen(argv, **kwargs):
            return rtl_proc if argv[0] == "rtl_fm" else mm_proc

        with mock.patch.object(listener.sdr_rx, "missing_deps", lambda *a, **k: []):
            result = listener.start(162550000, popen=fake_popen, startup_check_s=0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NWR_START_FAILED")
        self.assertIn("another service is already using the RTL-SDR dongle",
                      result["error"])
        self.assertEqual(listener.status()["last_error"], result["error"])
        self.assertIsNone(listener._state["rtl"])
        self.assertIsNone(listener._state["mm"])
        # multimon-ng must not be left running with a dead rtl_fm behind it
        self.assertIn(signal.SIGTERM, mm_proc.signals)

    def test_a_dongle_that_stays_up_is_reported_ok(self):
        rtl_proc = _FakeProc(exit_code=None)   # still running past the check
        mm_proc = _FakeProc(exit_code=None)

        def fake_popen(argv, **kwargs):
            return rtl_proc if argv[0] == "rtl_fm" else mm_proc

        with mock.patch.object(listener.sdr_rx, "missing_deps", lambda *a, **k: []):
            result = listener.start(162550000, popen=fake_popen, startup_check_s=0)

        self.assertTrue(result["ok"], result)
        self.assertIsNone(listener._state["last_error"])
        listener.stop()


class ScanClaimTest(_StateResetMixin, unittest.TestCase):
    """Finding 4: a scan blocks the Flask worker for up to `seconds + 30`
    while rtl_power owns the dongle, but nothing said so -- is_listening()
    was False and the nwr-listen synthetic token answered False right
    through the sweep."""

    def test_the_synthetic_token_answers_true_while_a_scan_is_claimed(self):
        w = listener.is_active_wrapper(lambda u: False)
        self.assertFalse(w("nwr-listen"))
        listener._state["scanning"] = True
        try:
            self.assertTrue(w("nwr-listen"))
        finally:
            listener._state["scanning"] = False
        self.assertFalse(w("nwr-listen"))

    def test_start_is_rejected_while_a_scan_holds_the_claim(self):
        listener._state["scanning"] = True
        try:
            with mock.patch.object(listener.sdr_rx, "missing_deps", lambda *a, **k: []):
                result = listener.start(162550000,
                                        popen=lambda *a, **k: _FakeProc())
        finally:
            listener._state["scanning"] = False
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NWR_BUSY")
        self.assertIn("scan", result["error"])
        self.assertIsNone(listener._state["rtl"], "start() must not have "
                          "spawned anything while the claim was held")


if __name__ == "__main__":
    unittest.main()
