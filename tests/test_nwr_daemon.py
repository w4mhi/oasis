import logging
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import daemon  # noqa: E402


class PortTest(unittest.TestCase):
    def test_port_is_8089_not_8087(self):
        # 8087 is claimed by oasis-ai (llama-server) on branch ai_mcp, and 8088
        # is rtl_433's soft claim. See specs/PORT-MAP.md.
        self.assertEqual(daemon.API_PORT, 8089)


class ChooseChannelTest(unittest.TestCase):
    def test_a_pinned_channel_skips_the_scan(self):
        called = []
        hz, result = daemon.choose_channel(
            _ROOT, {"pinned_channel": 162400000}, "0001",
            scan_fn=lambda **kw: called.append(1) or {})
        self.assertEqual(hz, 162400000)
        self.assertIsNone(result)
        self.assertEqual(called, [], "a pinned channel must not sweep the band")

    def test_auto_scan_picks_the_strongest(self):
        def fake_scan(**kw):
            return {"ok": True, "powers": {162400000: -40.0, 162550000: -12.0},
                    "best_hz": 162550000, "best_dbm": -12.0, "error": None}
        hz, result = daemon.choose_channel(_ROOT, {}, "0001", scan_fn=fake_scan)
        self.assertEqual(hz, 162550000)
        self.assertEqual(result["best_dbm"], -12.0)

    def test_a_weak_best_still_starts(self):
        # Refusing to start would leave nothing running, and silence that means
        # "no transmitter" would look identical to silence that means "broken".
        def fake_scan(**kw):
            return {"ok": True, "powers": {162550000: -60.0},
                    "best_hz": 162550000, "best_dbm": -60.0, "error": None}
        hz, result = daemon.choose_channel(_ROOT, {}, "0001", scan_fn=fake_scan)
        self.assertEqual(hz, 162550000)
        self.assertTrue(result["weak"])

    def test_a_failed_scan_falls_back_to_the_configured_channel(self):
        def fake_scan(**kw):
            return {"ok": False, "error": "rtl_power is not installed",
                    "code": "NWR_NO_RTL_POWER", "powers": {},
                    "best_hz": None, "best_dbm": None}
        hz, result = daemon.choose_channel(
            _ROOT, {"channel_hz": 162475000}, "0001", scan_fn=fake_scan)
        self.assertEqual(hz, 162475000)
        self.assertFalse(result["ok"])


class RescanBackoffTest(unittest.TestCase):
    def test_starts_at_fifteen_minutes(self):
        self.assertEqual(daemon.rescan_delay(1), 15 * 60)

    def test_doubles(self):
        self.assertEqual(daemon.rescan_delay(2), 30 * 60)
        self.assertEqual(daemon.rescan_delay(3), 60 * 60)

    def test_caps_at_six_hours(self):
        self.assertEqual(daemon.rescan_delay(99), 6 * 3600)

    def test_a_healthy_scan_resets_it(self):
        self.assertEqual(daemon.rescan_delay(0), 0)


class RetryBackoffTest(unittest.TestCase):
    """The retry curve is separate from the rescan curve on purpose: one
    governs a weak scan while a capture runs, the other a capture that will
    not start at all."""

    def test_the_first_retry_is_one_tick(self):
        self.assertEqual(daemon.retry_delay(1), daemon.SUPERVISE_TICK_S)

    def test_it_doubles(self):
        self.assertEqual(daemon.retry_delay(2), 10)
        self.assertEqual(daemon.retry_delay(3), 20)
        self.assertEqual(daemon.retry_delay(4), 40)

    def test_it_caps_at_five_minutes(self):
        self.assertEqual(daemon.retry_delay(99), daemon.RETRY_MAX_S)
        self.assertEqual(daemon.RETRY_MAX_S, 5 * 60)

    def test_a_success_resets_it_to_one_tick(self):
        self.assertEqual(daemon.retry_delay(0), daemon.SUPERVISE_TICK_S)

    def test_a_held_dongle_makes_the_sweep_pointless(self):
        # The exact text sdr_rx.stderr_summary() produces for usb_claim_interface.
        self.assertTrue(daemon.sweep_is_pointless(
            "another service is already using the RTL-SDR dongle"))
        self.assertTrue(daemon.sweep_is_pointless("No supported devices found."))

    def test_any_other_failure_still_sweeps(self):
        self.assertFalse(daemon.sweep_is_pointless("not installed: rtl_fm"))
        self.assertFalse(daemon.sweep_is_pointless(None))

    def test_the_skipped_sweep_falls_back_to_the_operators_channel(self):
        self.assertEqual(daemon.fallback_channel({"pinned_channel": 162400000}),
                         162400000)
        self.assertEqual(daemon.fallback_channel({"channel_hz": 162475000}),
                         162475000)
        self.assertEqual(daemon.fallback_channel({}),
                         daemon.settings.DEFAULTS["channel_hz"])


class _FakeStop:
    """A stop_event whose wait() advances a simulated clock instead of
    spending the wall clock, and which sets itself once `window` seconds of
    simulated time have gone by.

    The clock ONLY moves in wait(), which is what makes "how much of the
    window went through stop_event.wait()" an assertable property: a
    time.sleep() in the loop would be invisible here and the window would
    never end.
    """

    EPOCH = 1_700_000_000.0

    def __init__(self, window):
        self.now = self.EPOCH
        self.window = window
        self.waits = []

    def is_set(self):
        return (self.now - self.EPOCH) >= self.window

    def wait(self, delay):
        self.waits.append(delay)
        self.now += max(float(delay), 0.001)
        return self.is_set()


class SuperviseRetryCadenceTest(unittest.TestCase):
    """The hot-spin found by a live smoke test on 192.168.1.46: with all
    dongles assigned elsewhere, supervise() respawned rtl_power + rtl_fm +
    multimon-ng every ~4.8 s indefinitely -- 9 attempts in 43 s, roughly
    17,000 journal lines a day, on a box where the condition lasts for days.

    Nothing asserted on retry CADENCE, which is precisely why ten passing
    unit tests missed it.
    """

    HELD = "another service is already using the RTL-SDR dongle"

    def setUp(self):
        # supervise() writes module state; leave it as we found it.
        before = dict(daemon._state)
        self.addCleanup(lambda: daemon._state.update(before))
        # One WARNING per failed attempt is the behaviour under test, and
        # these runs simulate hours of them.
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _run(self, window, start_result, is_listening=False):
        stop = _FakeStop(window)
        attempts = []
        sweeps = []

        def fake_start(hz, **kw):
            attempts.append(hz)
            return dict(start_result)

        def fake_choose(repo_root, cfg, serial, **kw):
            sweeps.append(1)
            return 162550000, {"ok": True, "best_hz": 162550000,
                               "best_dbm": -20.0, "weak": False}

        with mock.patch.object(daemon.settings, "load",
                               return_value=dict(daemon.settings.DEFAULTS)), \
             mock.patch.object(daemon, "_device_serial", return_value=None), \
             mock.patch.object(daemon.listener, "is_listening",
                               return_value=is_listening), \
             mock.patch.object(daemon, "choose_channel", side_effect=fake_choose), \
             mock.patch.object(daemon.listener, "start", side_effect=fake_start):
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)
        return stop, attempts, sweeps

    def test_a_held_dongle_is_retried_a_bounded_number_of_times_in_an_hour(self):
        stop, attempts, _sweeps = self._run(
            3600, {"ok": False, "error": self.HELD})
        # Without the back-off this is 3600/5 = 720 attempts, each one three
        # spawned processes. With it the curve is 5,10,20,40,80,160,300,300...
        self.assertLessEqual(len(attempts), 20,
                             "the supervisor is still hot-spinning")
        self.assertGreaterEqual(len(attempts), 5,
                                "it must keep retrying, not give up")
        self.assertEqual(max(stop.waits), daemon.RETRY_MAX_S,
                         "the back-off must reach, and stop at, the ceiling")

    def test_every_second_of_the_back_off_goes_through_stop_event_wait(self):
        # A time.sleep() would not move this clock, so the window could never
        # elapse -- shutdown must stay immediate no matter how long the delay.
        stop, _attempts, _sweeps = self._run(
            3600, {"ok": False, "error": self.HELD})
        self.assertGreaterEqual(sum(stop.waits), 3600)

    def test_a_retry_into_a_held_dongle_does_not_sweep_the_band(self):
        _stop, attempts, sweeps = self._run(
            3600, {"ok": False, "error": self.HELD})
        self.assertEqual(len(sweeps), 1,
                         "rtl_power cannot claim a tuner rtl_fm just failed to "
                         "claim -- only the first attempt may sweep")
        self.assertTrue(len(attempts) > 1)

    def test_a_failure_that_is_not_the_dongle_keeps_sweeping(self):
        _stop, attempts, sweeps = self._run(
            3600, {"ok": False, "error": "rtl_fm exited immediately"})
        self.assertEqual(len(sweeps), len(attempts))

    def test_status_says_when_the_next_attempt_is_due(self):
        self._run(600, {"ok": False, "error": self.HELD})
        with daemon._lock:
            self.assertEqual(daemon._state["phase"], "retrying")
            self.assertGreater(daemon._state["next_retry"], 0)
            self.assertGreater(daemon._state["retry_failures"], 1)

    def test_a_start_that_succeeds_resets_the_count(self):
        stop, attempts, _sweeps = self._run(60, {"ok": True, "error": None})
        # Every wait is one plain tick: nothing failed, so nothing backs off.
        self.assertEqual(set(stop.waits), {daemon.SUPERVISE_TICK_S})
        with daemon._lock:
            self.assertEqual(daemon._state["retry_failures"], 0)
            self.assertEqual(daemon._state["next_retry"], 0)
            self.assertEqual(daemon._state["phase"], "listening")

    def test_a_healthy_scan_still_sets_no_rescan_timer(self):
        self._run(30, {"ok": True, "error": None})
        with daemon._lock:
            self.assertEqual(daemon._state["next_rescan"], 0)


class OnHeaderContainmentTest(unittest.TestCase):
    """_make_handler's docstring says "Never raises". That has to be true
    locally, not only because listener.decode_lines() happens to wrap the
    callback -- a promise kept by code this module does not own is not a
    promise."""

    REC = {"station": "KEC55", "event": "TOR", "raw": "ZCZC-WXR-TOR"}

    def setUp(self):
        before = dict(daemon._state)
        self.addCleanup(lambda: daemon._state.update(before))

    def _call(self, **patches):
        base = {
            "settings_load": dict(daemon.settings.DEFAULTS),
            "record": (True, dict(self.REC)),
            "should_speak": (True, "watched"),
        }
        base.update(patches)
        with mock.patch.object(daemon.settings, "load",
                               **self._as(base["settings_load"])), \
             mock.patch.object(daemon.alerts, "record", **self._as(base["record"])), \
             mock.patch.object(daemon.bell, "should_speak",
                               **self._as(base["should_speak"])), \
             mock.patch.object(daemon.announce, "speak",
                               **self._as(base.get("speak"))):
            daemon._make_handler(_ROOT)({"raw": "ZCZC-WXR-TOR"})

    @staticmethod
    def _as(value):
        """A mock kwarg dict: an exception instance raises, anything else is
        the return value."""
        if isinstance(value, Exception):
            return {"side_effect": value}
        return {"return_value": value}

    def test_a_missing_voice_does_not_escape(self):
        with self.assertLogs(daemon.log.name, level="ERROR"):
            self._call(speak=RuntimeError("piper is not installed"))

    def test_a_bell_policy_that_raises_does_not_escape(self):
        with self.assertLogs(daemon.log.name, level="ERROR"):
            self._call(should_speak=ValueError("unparseable bell window"))

    def test_unloadable_settings_still_store_the_alert(self):
        stored = []
        with mock.patch.object(daemon.settings, "load",
                               side_effect=OSError("settings.json is a directory")), \
             mock.patch.object(daemon.alerts, "record",
                               side_effect=lambda *a: stored.append(a) or (True, dict(self.REC))), \
             mock.patch.object(daemon.bell, "should_speak", return_value=(False, "off")), \
             mock.patch.object(daemon.announce, "speak"):
            with self.assertLogs(daemon.log.name, level="ERROR"):
                daemon._make_handler(_ROOT)({"raw": "ZCZC-WXR-TOR"})
        self.assertEqual(len(stored), 1, "the alert must be recorded anyway")

    def test_the_happy_path_still_speaks(self):
        spoken = []
        with mock.patch.object(daemon.settings, "load",
                               return_value=dict(daemon.settings.DEFAULTS)), \
             mock.patch.object(daemon.alerts, "record",
                               return_value=(True, dict(self.REC))), \
             mock.patch.object(daemon.bell, "should_speak", return_value=(True, "watched")), \
             mock.patch.object(daemon.announce, "speak",
                               side_effect=lambda *a: spoken.append(a)):
            daemon._make_handler(_ROOT)({"raw": "ZCZC-WXR-TOR"})
        self.assertEqual(len(spoken), 1)


class _FakeWfile:
    """The client socket. Raises once `limit` bytes have been written -- a
    browser closing its tab mid-stream, which is the case this handler's
    teardown exists for."""

    def __init__(self, limit):
        self.data = bytearray()
        self.limit = limit

    def write(self, blob):
        if len(self.data) >= self.limit:
            raise BrokenPipeError("client went away")
        self.data.extend(blob)

    def flush(self):
        pass


class _RecordingHandler(daemon._Handler):
    """_Handler with the socket machinery stubbed out.

    BaseHTTPRequestHandler.__init__ parses a request off a real socket and
    then serves it; _stream() is the whole subject here, so it is deliberately
    not called. close_connection starts False on purpose -- that is CPython's
    HTTP/1.1 default, and the state F3 is about.
    """

    def __init__(self, wfile):
        self.wfile = wfile
        self.headers_out = []
        self.json_out = None
        self.code = None
        self.close_connection = False
        self.close_at_end_headers = None

    def send_response(self, code, message=None):
        self.code = code

    def send_header(self, keyword, value):
        self.headers_out.append((keyword, value))

    def end_headers(self):
        self.close_at_end_headers = self.close_connection

    def _json(self, code, payload):
        self.code = code
        self.json_out = payload


class DaemonStreamTest(unittest.TestCase):
    """The v1 deadlock, ported from tests/test_nwr_routes.py: the stream now
    lives in the daemon, so the regression has to live here too.

    The old code wrote ONE queued chunk to the encoder's stdin and then
    blocked reading its stdout, on the SAME thread. libmp3lame needs several
    times CHUNK (4096) bytes of PCM before its first frame comes out, so the
    generator never looped back to feed it -- both sides waited forever. A
    mock encoder that echoes bytes straight back always "works" and is blind
    to this, so this spawns a REAL subprocess with the same
    withhold-until-threshold behaviour and drives the real handler at it.
    """

    _THRESHOLD = 20000       # comfortably past listener.CHUNK (4096)

    @classmethod
    def setUpClass(cls):
        # A stand-in for libmp3lame's buffering, not ffmpeg itself: this box
        # has no ffmpeg and CI cannot be relied on to either, but the one
        # property that matters -- silence on stdout until a lot of stdin has
        # been consumed -- is exactly what this read loop reproduces.
        script = (
            "import sys\n"
            f"THRESHOLD = {cls._THRESHOLD}\n"
            "buf = bytearray()\n"
            "started = False\n"
            "while True:\n"
            "    chunk = sys.stdin.buffer.read(4096)\n"
            "    if not chunk:\n"
            "        break\n"
            "    if not started:\n"
            "        buf.extend(chunk)\n"
            "        if len(buf) < THRESHOLD:\n"
            "            continue\n"
            "        started = True\n"
            "        sys.stdout.buffer.write(bytes(buf))\n"
            "        sys.stdout.buffer.flush()\n"
            "    else:\n"
            "        sys.stdout.buffer.write(chunk)\n"
            "        sys.stdout.buffer.flush()\n"
        )
        fd, cls._script_path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write(script)

    @classmethod
    def tearDownClass(cls):
        os.remove(cls._script_path)

    def setUp(self):
        self._encoder_cmd = "{} {}".format(shlex.quote(sys.executable),
                                           shlex.quote(self._script_path))
        # Belt and braces: a test that fails before teardown must not leave a
        # real queue in listener's global subscriber list, or a leaked stream
        # slot, for a later test to trip over.
        self.addCleanup(lambda: daemon.listener._state.__setitem__("subs", []))
        self.addCleanup(lambda: daemon._state.__setitem__("streams", 0))

    @staticmethod
    def _close_pipes(proc):
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:                    # noqa: BLE001
                pass

    def _run_stream(self, client_limit=8192):
        """Drive the real _stream() against the real stand-in encoder on its
        own thread, feeding the subscriber queue the way pump() would."""
        procs = []
        captured = {}
        real_popen = subprocess.Popen
        real_subscribe = daemon.listener.subscribe
        # terminate() reaps the process but leaves its pipe objects to the
        # garbage collector, which makes ResourceWarning noise here.
        self.addCleanup(lambda: [self._close_pipes(p) for p in procs])

        def spy_popen(*a, **k):
            p = real_popen(*a, **k)
            procs.append(p)
            return p

        def spy_subscribe():
            q = real_subscribe()
            captured["q"] = q
            return q

        patches = [
            mock.patch.object(daemon.listener, "is_listening", return_value=True),
            mock.patch("common.sdr_rx.stream_encoder",
                       return_value=(self._encoder_cmd, "audio/mpeg")),
            mock.patch.object(daemon.listener, "subscribe", side_effect=spy_subscribe),
            mock.patch("subprocess.Popen", side_effect=spy_popen),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        handler = _RecordingHandler(_FakeWfile(client_limit))
        t = threading.Thread(target=handler._stream, daemon=True,
                             name="nwr-test-stream")
        t.start()

        deadline = time.time() + 3
        while "q" not in captured and time.time() < deadline:
            time.sleep(0.01)
        self.assertIn("q", captured, "the stream never subscribed")

        # pump()'s real delivery shape: many CHUNK-sized pieces, comfortably
        # past _THRESHOLD in total.
        for _ in range(10):
            captured["q"].put(b"a" * 4096)

        t.join(timeout=10)
        self.assertFalse(t.is_alive(),
                         "the stream deadlocked waiting on the encoder -- "
                         "write-then-read on one thread never loops back to "
                         "feed it enough input to produce output")
        return handler, procs, captured

    def test_it_produces_real_encoder_output_without_deadlocking(self):
        handler, procs, _captured = self._run_stream()
        self.assertEqual(handler.code, 200)
        self.assertTrue(handler.wfile.data, "no bytes came back from the encoder")
        self.assertTrue(procs, "the encoder was never spawned")

    def test_the_writer_thread_does_not_outlive_the_request(self):
        # Without the sentinel the writer sits in q.get(timeout=30) after the
        # client hangs up: killing the encoder does not wake a thread parked
        # on a queue, so join(5) times out and the thread lingers ~30 s.
        self._run_stream()
        alive = [th for th in threading.enumerate()
                 if th.name == "nwr-stream-writer"]
        self.assertEqual(alive, [], "the writer thread outlived the request")

    def test_it_unsubscribes_reaps_the_encoder_and_frees_the_slot(self):
        _handler, procs, captured = self._run_stream()
        self.assertNotIn(captured["q"], daemon.listener._state["subs"])
        for _ in range(50):
            if procs[0].poll() is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(procs[0].poll(),
                             "the encoder was not reaped after the client left")
        self.assertEqual(daemon._state["streams"], 0)

    def test_the_response_is_close_delimited(self):
        # HTTP/1.1 with neither Content-Length nor chunked encoding: the
        # connection close IS the framing, and CPython defaults the other way.
        handler, _procs, _captured = self._run_stream()
        self.assertTrue(handler.close_at_end_headers,
                        "close_connection must be set before end_headers()")
        names = [k.lower() for k, _v in handler.headers_out]
        self.assertIn("connection", names)
        self.assertNotIn("content-length", names)

    def test_one_stream_too_many_is_refused_without_spawning_an_encoder(self):
        daemon._state["streams"] = daemon.MAX_STREAMS
        with mock.patch.object(daemon.listener, "is_listening", return_value=True), \
             mock.patch("common.sdr_rx.stream_encoder",
                        return_value=(self._encoder_cmd, "audio/mpeg")), \
             mock.patch.object(daemon.listener, "subscribe",
                               side_effect=AssertionError("must not subscribe")), \
             mock.patch("subprocess.Popen",
                        side_effect=AssertionError("must not spawn an encoder")):
            handler = _RecordingHandler(_FakeWfile(1 << 20))
            handler._stream()
        self.assertEqual(handler.code, 503)
        self.assertEqual(daemon._state["streams"], daemon.MAX_STREAMS,
                         "a refused stream must not consume a slot")


class StatusTest(unittest.TestCase):
    def test_reports_the_keys_the_card_and_flask_read(self):
        s = daemon.status()
        for k in ("phase", "channel_hz", "channel", "scan", "scan_weak",
                  "listening", "alerts_seen", "last_decode", "last_error",
                  "subscribers", "retry_in_s"):
            self.assertIn(k, s)

    def test_a_pending_retry_is_reported_in_seconds(self):
        before = dict(daemon._state)
        self.addCleanup(lambda: daemon._state.update(before))
        with daemon._lock:
            daemon._state["next_retry"] = time.time() + 240
        self.assertGreater(daemon.status()["retry_in_s"], 200)


if __name__ == "__main__":
    unittest.main()
