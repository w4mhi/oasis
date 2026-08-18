import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SERVER = os.path.join(_ROOT, "server")
for _p in (_ROOT, _SERVER, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as app_module  # noqa: E402
from oasis_testclient import bare_client, csrf_client  # noqa: E402
from services.nwr import routes as nwr_routes  # noqa: E402
from services.nwr.common import bell, settings  # noqa: E402


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "configuration"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_defaults_on_a_fresh_box(self):
        s = settings.load(self.root)
        self.assertEqual(s["channel_hz"], settings.DEFAULTS["channel_hz"])
        self.assertEqual(s["watch_fips"], [])
        self.assertFalse(s["bell"])

    def test_save_and_reload(self):
        settings.save(self.root, {"channel_hz": 162400000,
                                  "watch_fips": ["53033"], "bell": True})
        s = settings.load(self.root)
        self.assertEqual(s["channel_hz"], 162400000)
        self.assertEqual(s["watch_fips"], ["53033"])
        self.assertTrue(s["bell"])

    def test_speak_true_migrates_to_bell_true_on_load(self):
        # v1 operators who had speech on must not silently lose it now that
        # `speak` has dropped out of DEFAULTS.
        from common import atomic_json, config_paths
        atomic_json.write_json(config_paths.nwr_json(self.root), {"speak": True})
        self.assertTrue(settings.load(self.root)["bell"])

    def test_migration_does_not_override_an_explicit_bell(self):
        from common import atomic_json, config_paths
        atomic_json.write_json(config_paths.nwr_json(self.root),
                               {"speak": True, "bell": False})
        self.assertFalse(settings.load(self.root)["bell"])

    def test_rejects_a_frequency_that_is_not_an_nwr_channel(self):
        with self.assertRaises(ValueError):
            settings.save(self.root, {"channel_hz": 145825000})

    def test_watch_fips_normalised_to_five_digits(self):
        settings.save(self.root, {"watch_fips": ["053033", "53053"]})
        self.assertEqual(settings.load(self.root)["watch_fips"],
                         ["53033", "53053"])

    def test_watch_fips_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            settings.save(self.root, {"watch_fips": ["not-a-fips"]})

    def test_unknown_keys_are_ignored_not_persisted(self):
        settings.save(self.root, {"channel_hz": 162400000, "evil": "yes"})
        self.assertNotIn("evil", settings.load(self.root))

    def test_bell_override_until_zero_is_valid_and_means_no_override(self):
        settings.save(self.root, {"bell_override_until": 0})
        self.assertEqual(settings.load(self.root)["bell_override_until"], 0)

    def test_bell_override_until_rejects_negative(self):
        with self.assertRaises(ValueError):
            settings.save(self.root, {"bell_override_until": -1})

    def test_bell_override_until_accepts_exactly_the_boundary(self):
        # The boundary IS the value a well-behaved client sends -- it asks
        # bell.override_until() for "when does this override expire" and
        # posts exactly that back.
        now = datetime.datetime(2026, 8, 17, 23, 30)
        boundary = bell.override_until(now, self.root)
        settings.save(self.root, {"bell_override_until": boundary}, now=now)
        self.assertEqual(settings.load(self.root)["bell_override_until"], boundary)

    def test_bell_override_until_rejects_beyond_the_next_quiet_hours_boundary(self):
        # This is the guarantee Finding 2 is about: an override cannot
        # outlive the next end-of-quiet-hours, so a buggy or direct POST
        # can never leave "quiet hours off" sitting forgotten for months.
        now = datetime.datetime(2026, 8, 17, 23, 30)
        boundary = bell.override_until(now, self.root)
        with self.assertRaises(ValueError):
            settings.save(self.root, {"bell_override_until": boundary + 3600},
                          now=now)


class NwrRouteCsrfTest(unittest.TestCase):
    """Every mutating NWR route MUST reject a request without the CSRF
    header — a plain client, not the header-carrying one, is the only way
    to prove that (csrf_client would mask a missing guard)."""

    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = bare_client(app_module.app)

    def test_post_routes_reject_without_oasis_header(self):
        for path in ("/api/nwr/listen", "/api/nwr/listen/stop",
                    "/api/nwr/config", "/api/nwr/scan"):
            with self.subTest(path=path):
                r = self.c.post(path, json={})
                self.assertEqual(r.status_code, 403)
                self.assertFalse(json.loads(r.data)["ok"])


class NwrStatusRouteTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = csrf_client(app_module.app)

    def test_status_shape_when_nothing_installed_or_listening(self):
        pre = {"missing_deps": ["multimon-ng"], "dongle_present": False,
              "assigned": False, "device": None, "busy": False,
              "holder": None, "can_stream": False, "can_scan": False}
        with mock.patch.object(nwr_routes.listener, "preconditions", return_value=pre), \
             mock.patch.object(nwr_routes.listener, "is_listening", return_value=False):
            r = self.c.get("/api/nwr/status")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertTrue(body["ok"])
        for key in ("preconditions", "capture", "config", "channels"):
            self.assertIn(key, body)
        self.assertFalse(body["capture"]["listening"])
        self.assertEqual(body["preconditions"]["missing_deps"], ["multimon-ng"])


class NwrConfigRouteTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = csrf_client(app_module.app)
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "configuration"))
        self._root_patch = mock.patch.object(nwr_routes, "_root", return_value=self.root)
        self._root_patch.start()

    def tearDown(self):
        self._root_patch.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_bad_channel_hz_rejected_with_400(self):
        r = self.c.post("/api/nwr/config", json={"channel_hz": 145825000})
        self.assertEqual(r.status_code, 400)
        body = json.loads(r.data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "NWR_BAD_CONFIG")

    def test_good_channel_hz_persists(self):
        r = self.c.post("/api/nwr/config", json={"channel_hz": 162400000})
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertTrue(body["ok"])
        self.assertEqual(body["config"]["channel_hz"], 162400000)
        reread = self.c.get("/api/nwr/config")
        self.assertEqual(json.loads(reread.data)["config"]["channel_hz"], 162400000)

    def test_bell_override_until_beyond_boundary_rejected_with_400(self):
        # A far-future epoch is exactly the anti-pattern Finding 2 names: a
        # permanent "quiet hours off" switch set by a buggy client or a
        # direct API call, sitting forgotten until it surprises someone.
        r = self.c.post("/api/nwr/config", json={"bell_override_until": 9999999999})
        self.assertEqual(r.status_code, 400)
        body = json.loads(r.data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "NWR_BAD_CONFIG")


class NwrListenRouteTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = csrf_client(app_module.app)

    def test_listen_returns_409_when_dongle_busy(self):
        cfg = dict(settings.DEFAULTS)
        with mock.patch.object(nwr_routes.settings, "load", return_value=cfg), \
             mock.patch.object(nwr_routes, "_inventory", return_value=None), \
             mock.patch.object(nwr_routes.listener, "preconditions",
                               return_value={"busy": True, "holder": "aprs"}):
            r = self.c.post("/api/nwr/listen", json={})
        self.assertEqual(r.status_code, 409)
        body = json.loads(r.data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "NWR_DONGLE_BUSY")


class NwrStreamRouteTest(unittest.TestCase):
    """The AUDIO branch's cleanup path — Finding 1's territory. The decode
    branch (services/nwr/common/listener.py) is never touched here; these
    tests only exercise the per-subscriber fan-out this route owns."""

    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = csrf_client(app_module.app)

    def test_stream_refuses_when_nothing_is_listening(self):
        with mock.patch.object(nwr_routes.listener, "is_listening", return_value=False):
            r = self.c.get("/api/nwr/listen/stream")
        self.assertEqual(r.status_code, 409)
        body = json.loads(r.data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "NWR_NOT_LISTENING")

    def test_stream_unsubscribes_when_the_encoder_cannot_be_spawned(self):
        # A dongle exhausting file descriptors / no /bin/sh — more plausible
        # on a Pi 3 than a workstation — must not leave the subscriber queue
        # registered forever (Finding 1).
        fake_q = object()
        subscribed = []
        unsubscribed = []

        def fake_subscribe():
            subscribed.append(fake_q)
            return fake_q

        def fake_popen(*a, **k):
            raise OSError("fork failed: resource temporarily unavailable")

        with mock.patch.object(nwr_routes.listener, "is_listening", return_value=True), \
             mock.patch("common.sdr_rx.stream_encoder",
                        return_value=("ffmpeg -f s16le -i - -f mp3 -", "audio/mpeg")), \
             mock.patch.object(nwr_routes.listener, "subscribe", side_effect=fake_subscribe), \
             mock.patch.object(nwr_routes.listener, "unsubscribe",
                               side_effect=unsubscribed.append), \
             mock.patch("subprocess.Popen", side_effect=fake_popen):
            r = self.c.get("/api/nwr/listen/stream")
            r.get_data()   # force the generator to run to completion

        self.assertEqual(subscribed, [fake_q])
        self.assertEqual(unsubscribed, [fake_q])


class NwrStreamDeadlockTest(unittest.TestCase):
    """The AUDIO branch's actual field failure: a real encoder that needs
    far more than one write's worth of input before it produces output.

    The bug: the old `_generate()` wrote ONE queued chunk to the encoder's
    stdin, then blocked on a `read1()` of its stdout, on the SAME thread.
    libmp3lame needs several times CHUNK (4096) bytes of PCM before its
    first MP3 frame comes out, so the generator never looped back to feed
    it more -- both sides waited forever. A mock encoder that echoes bytes
    straight back is blind to this exact shape of bug (it always "works"),
    so these tests spawn a REAL subprocess with the same
    withhold-until-threshold behavior real libmp3lame has, then drive the
    real route generator against it.
    """

    # Comfortably larger than services.nwr.common.listener.CHUNK (4096),
    # so writing exactly one queued chunk -- the old code's ceiling --
    # can never cross it. Only a writer that keeps draining the queue
    # across several chunks, on its own thread, gets there.
    _THRESHOLD = 20000

    @classmethod
    def setUpClass(cls):
        # A stand-in for libmp3lame's buffering, not ffmpeg itself: this
        # box has no ffmpeg (and CI can't be relied on to either), but the
        # one property that matters here -- silence on stdout until a
        # large amount of stdin has been consumed -- is exactly what a
        # plain Python read loop below reproduces, with no vendored
        # binary and no platform dependency.
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
        app_module.app.config["TESTING"] = True
        self._encoder_cmd = "{} {}".format(shlex.quote(sys.executable),
                                           shlex.quote(self._script_path))
        # Belt-and-braces: a test that fails before reaching gen.close()
        # must not leave a real queue sitting in listener's global
        # subscriber list for a later test to trip over.
        self.addCleanup(lambda: nwr_routes.listener._state.__setitem__("subs", []))

    def _start_generator(self):
        """Wire the real route generator to the real stand-in subprocess,
        spying on subscribe()/unsubscribe()/Popen so the test can see the
        real queue, the real encoder process, and every unsubscribe call
        without reaching into listener internals directly."""
        procs = []
        real_popen = subprocess.Popen

        def spy_popen(*a, **k):
            p = real_popen(*a, **k)
            procs.append(p)
            return p

        captured = {}
        real_subscribe = nwr_routes.listener.subscribe

        def spy_subscribe():
            q = real_subscribe()
            captured["q"] = q
            return q

        unsub_calls = []
        real_unsubscribe = nwr_routes.listener.unsubscribe

        def spy_unsubscribe(q):
            unsub_calls.append(q)
            return real_unsubscribe(q)

        patches = [
            mock.patch.object(nwr_routes.listener, "is_listening", return_value=True),
            mock.patch("common.sdr_rx.stream_encoder",
                       return_value=(self._encoder_cmd, "audio/mpeg")),
            mock.patch.object(nwr_routes.listener, "subscribe", side_effect=spy_subscribe),
            mock.patch.object(nwr_routes.listener, "unsubscribe", side_effect=spy_unsubscribe),
            mock.patch("subprocess.Popen", side_effect=spy_popen),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        resp = nwr_routes.api_nwr_stream()
        gen = resp.response
        return gen, procs, captured, unsub_calls

    def _pull_first_chunk(self, gen, captured, result):
        """Start the generator on its own thread (its first blocking read
        can only be unblocked from a different thread than the one that's
        parked in it) and feed it once it has subscribed."""
        def _run():
            try:
                result["out"] = next(gen)
            except StopIteration:
                result["out"] = None
            except Exception as e:              # noqa: BLE001
                result["error"] = e
        t = threading.Thread(target=_run, daemon=True)
        t.start()

        deadline = time.time() + 2
        while "q" not in captured and time.time() < deadline:
            time.sleep(0.01)
        self.assertIn("q", captured, "generator never subscribed")

        # Mimic pump()'s real delivery shape -- many CHUNK-sized pieces,
        # not one big write -- comfortably past _THRESHOLD in total.
        q = captured["q"]
        for _ in range(10):
            q.put(b"a" * 4096)

        t.join(timeout=8)
        self.assertFalse(t.is_alive(),
                         "stream generator deadlocked waiting on the encoder -- "
                         "write-then-read on one thread never looped back to "
                         "feed it enough input to produce output")
        return t

    def test_stream_produces_real_encoder_output_without_deadlocking(self):
        gen, procs, captured, _unsub_calls = self._start_generator()
        result = {}
        self._pull_first_chunk(gen, captured, result)

        self.assertNotIn("error", result, result.get("error"))
        self.assertTrue(result.get("out"), "no bytes came back from the encoder")
        self.assertTrue(procs, "encoder was never spawned")

        gen.close()

    def test_stream_close_unsubscribes_and_reaps_after_early_disconnect(self):
        gen, procs, captured, unsub_calls = self._start_generator()
        result = {}
        self._pull_first_chunk(gen, captured, result)
        self.assertTrue(result.get("out"))
        q = captured["q"]

        # The generator is suspended at `yield out` here, exactly where a
        # closed client connection resumes it with GeneratorExit. Safe to
        # call from this thread: the thread that ran next() has already
        # finished, so nothing is concurrently executing the generator.
        gen.close()

        self.assertEqual(unsub_calls, [q])

        for _ in range(50):
            if procs[0].poll() is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(procs[0].poll(), "encoder was not reaped after early close()")

        alive_writers = [th for th in threading.enumerate()
                         if th.name == "nwr-stream-writer"]
        self.assertEqual(alive_writers, [], "writer thread outlived the request")


if __name__ == "__main__":
    unittest.main()
