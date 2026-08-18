import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
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




class _FakeUpstream:
    """The watch daemon's HTTP response, the way urllib hands it back.

    Doubles as the /status context manager and as the /stream body, because the
    route uses the same urlopen() for both.
    """

    def __init__(self, body=b"", status=200, headers=None):
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.closed = False
        self.reads = 0

    def read(self, n=-1):
        self.reads += 1
        return self._body.read(n)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _StallingUpstream(_FakeUpstream):
    """A stream that goes quiet after its first chunk: the daemon's writer
    tolerates 30 s of silence on a subscriber queue, this socket gives up at
    10, and a real gap between the two lands here."""

    def read(self, n=-1):
        if self.reads:
            self.reads += 1
            raise TimeoutError("timed out")
        return super().read(n)


def _http_error(code, payload):
    """What urlopen() actually raises for a daemon refusal -- an HTTPError, not
    a response object carrying a status. Every non-2xx arrives this way."""
    body = io.BytesIO(json.dumps(payload).encode())
    return urllib.error.HTTPError(WATCH_STREAM, code, "refused", {}, body)


WATCH_STREAM = nwr_routes.WATCH_BASE + "/stream"

# What the daemon's status() reports on a healthy box, trimmed to the keys the
# route reads. Field names come from services/nwr/common/daemon.py, not from a
# guess -- tests/test_nwr_daemon.py's StatusTest pins the same set.
WATCH_UP = {
    "phase": "listening", "channel_hz": 162450000, "channel": "WX3",
    "scan": {"ok": True, "best_hz": 162450000, "best_dbm": -18.0, "weak": False},
    "scan_weak": False, "consecutive_weak": 0, "retry_failures": 0,
    "next_retry": 0, "streams": 0, "started": 1_700_000_000.0,
    "alerts_seen": 4, "last_decode": {"station": "KEC55", "event": "TOR",
                                      "at": 1_700_000_500},
    "last_error": None, "listening": True, "subscribers": 1, "elapsed_s": 92,
    "retry_in_s": 0, "port": 8089,
}

PRE_READY = {"missing_deps": [], "dongle_present": True, "assigned": True,
             "device": "RTL-SDR #1", "busy": False, "holder": None,
             "can_stream": True, "can_scan": True}


class NwrRouteCsrfTest(unittest.TestCase):
    """Every mutating NWR route MUST reject a request without the CSRF
    header — a plain client, not the header-carrying one, is the only way
    to prove that (csrf_client would mask a missing guard).

    Only one mutating route is left: capture control moved to the daemon, so
    /listen, /listen/stop and /scan are gone rather than guarded.
    """

    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = bare_client(app_module.app)

    def test_post_routes_reject_without_oasis_header(self):
        for path in ("/api/nwr/config",):
            with self.subTest(path=path):
                r = self.c.post(path, json={})
                self.assertEqual(r.status_code, 403)
                self.assertFalse(json.loads(r.data)["ok"])


class NwrRetiredRoutesTest(unittest.TestCase):
    """Flask no longer starts, stops or sweeps anything.

    A stale page still holds these URLs, and 404 is the one answer that cannot
    be mistaken for success: silently starting a second capture would fight the
    daemon for the same dongle, and a Flask-side rtl_power sweep would take the
    tuner out from under a running watch for six seconds.
    """

    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = csrf_client(app_module.app)

    def test_the_capture_control_routes_are_gone(self):
        # 405, not 404: this app serves its static tree from "/", so a POST to
        # ANY path with no route of its own matches that GET-only rule and
        # Werkzeug answers MethodNotAllowed before Flask ever reports a missing
        # URL. What matters to a stale page is identical either way -- a 4xx
        # with a conformant envelope and a stable code, and nothing started.
        for path in ("/api/nwr/listen", "/api/nwr/listen/stop", "/api/nwr/scan"):
            with self.subTest(path=path):
                r = self.c.post(path, json={})
                self.assertEqual(r.status_code, 405)
                body = json.loads(r.data)
                self.assertFalse(body["ok"])
                self.assertEqual(body["code"], "METHOD_NOT_ALLOWED")

    def test_a_stale_page_asking_for_them_gets_a_conformant_404(self):
        for path in ("/api/nwr/listen", "/api/nwr/scan"):
            with self.subTest(path=path):
                r = self.c.get(path)
                self.assertEqual(r.status_code, 404)
                body = json.loads(r.data)
                self.assertFalse(body["ok"])
                self.assertEqual(body["code"], "NOT_FOUND")

    def test_the_blueprint_declares_no_capture_endpoint(self):
        # A 404 could also mean "renamed"; this says the endpoints do not exist
        # anywhere on the map.
        rules = {str(r) for r in app_module.app.url_map.iter_rules()}
        for gone in ("/api/nwr/listen", "/api/nwr/listen/stop", "/api/nwr/scan"):
            self.assertNotIn(gone, rules)


class NwrStatusRouteTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = csrf_client(app_module.app)

    def test_a_watch_that_is_down_is_an_answer_not_a_failure(self):
        # Contract §2: the probe succeeded; the watch being down is the news.
        # A station whose daemon has crashed must still render a dashboard.
        pre = {"missing_deps": ["multimon-ng"], "dongle_present": False,
               "assigned": False, "device": None, "busy": False,
               "holder": None, "can_stream": False, "can_scan": False}
        with mock.patch.object(nwr_routes.listener, "preconditions", return_value=pre), \
             mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("Connection refused")):
            r = self.c.get("/api/nwr/status")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertTrue(body["ok"])
        for key in ("preconditions", "watch", "config", "channels"):
            self.assertIn(key, body)
        self.assertFalse(body["watch"]["reachable"])
        self.assertIn("Connection refused", body["watch"]["detail"])
        self.assertFalse(body["watch"]["listening"])
        self.assertEqual(body["preconditions"]["missing_deps"], ["multimon-ng"])

    def test_the_shape_does_not_change_when_the_watch_is_down(self):
        # Contract §5: a key in the schema is in every response. A field that
        # is sometimes absent and sometimes null reads as two different worlds.
        with mock.patch.object(nwr_routes.listener, "preconditions", return_value=PRE_READY):
            with mock.patch("urllib.request.urlopen",
                            return_value=_FakeUpstream(json.dumps(WATCH_UP).encode())):
                up = json.loads(self.c.get("/api/nwr/status").data)
            with mock.patch("urllib.request.urlopen",
                            side_effect=OSError("no route to host")):
                down = json.loads(self.c.get("/api/nwr/status").data)
        self.assertEqual(sorted(up["watch"]), sorted(down["watch"]))
        self.assertIsNone(down["watch"]["phase"])
        self.assertEqual(down["watch"]["alerts_seen"], 0)

    def test_the_watch_state_comes_from_the_daemon(self):
        with mock.patch.object(nwr_routes.listener, "preconditions", return_value=PRE_READY), \
             mock.patch("urllib.request.urlopen",
                        return_value=_FakeUpstream(json.dumps(WATCH_UP).encode())):
            body = json.loads(self.c.get("/api/nwr/status").data)
        w = body["watch"]
        self.assertTrue(w["reachable"])
        self.assertIsNone(w["detail"])
        self.assertTrue(w["listening"])
        self.assertEqual(w["phase"], "listening")
        self.assertEqual(w["channel"], "WX3")
        self.assertEqual(w["alerts_seen"], 4)
        self.assertEqual(w["last_decode"]["event"], "TOR")

    def test_a_retrying_watch_reports_why_and_when(self):
        # The two fields that keep a red card from looking wedged.
        state = dict(WATCH_UP, phase="retrying", listening=False,
                     channel=None, channel_hz=None, retry_in_s=240,
                     last_error="another service is already using the RTL-SDR dongle")
        with mock.patch.object(nwr_routes.listener, "preconditions", return_value=PRE_READY), \
             mock.patch("urllib.request.urlopen",
                        return_value=_FakeUpstream(json.dumps(state).encode())):
            body = json.loads(self.c.get("/api/nwr/status").data)
        self.assertTrue(body["ok"])
        self.assertEqual(body["watch"]["phase"], "retrying")
        self.assertEqual(body["watch"]["retry_in_s"], 240)
        self.assertIn("already using", body["watch"]["last_error"])

    def test_a_daemon_serving_nonsense_does_not_take_the_page_down(self):
        with mock.patch.object(nwr_routes.listener, "preconditions", return_value=PRE_READY), \
             mock.patch("urllib.request.urlopen",
                        return_value=_FakeUpstream(b"<html>not json</html>")):
            r = self.c.get("/api/nwr/status")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        self.assertTrue(body["ok"])
        self.assertFalse(body["watch"]["reachable"])


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


class NwrStreamRelayTest(unittest.TestCase):
    """The relay. Flask spawns nothing and subscribes to nothing here — the
    encoder and the subscriber queue both live in the daemon, which is what
    makes the v1 audio deadlock impossible in this process by construction.
    tests/test_nwr_daemon.py's DaemonStreamTest is where that regression is
    now guarded, against a real withhold-until-threshold subprocess."""

    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.c = csrf_client(app_module.app)

    def test_a_watch_that_is_not_reachable_is_a_conformant_503(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("Connection refused")):
            r = self.c.get("/api/nwr/listen/stream")
        self.assertEqual(r.status_code, 503)
        body = json.loads(r.data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "NWR_WATCH_UNAVAILABLE")
        self.assertIn("Connection refused", body["error"])

    def test_a_watch_that_is_not_running_answers_409_with_its_own_reason(self):
        # urlopen RAISES on a 409 rather than returning one, so a route that
        # only tested `upstream.status` would report "not reachable" for a
        # daemon that is up and simply has no capture.
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(409, {"error": "the watch is not running"})):
            r = self.c.get("/api/nwr/listen/stream")
        self.assertEqual(r.status_code, 409)
        body = json.loads(r.data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "NWR_NOT_LISTENING")
        self.assertEqual(body["error"], "the watch is not running")

    def test_a_refused_stream_forwards_the_daemons_reason(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(503, {"error": "too many audio streams"})):
            r = self.c.get("/api/nwr/listen/stream")
        self.assertEqual(r.status_code, 503)
        body = json.loads(r.data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "NWR_STREAM_UNAVAILABLE")
        self.assertEqual(body["error"], "too many audio streams")

    def test_audio_is_relayed_byte_for_byte_with_the_daemons_mime(self):
        up = _FakeUpstream(b"\xff\xfb" + b"x" * 20000,
                           headers={"Content-Type": "audio/mpeg"})
        with mock.patch("urllib.request.urlopen", return_value=up):
            r = self.c.get("/api/nwr/listen/stream")
            data = r.get_data()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "audio/mpeg")
        self.assertEqual(data, b"\xff\xfb" + b"x" * 20000)
        self.assertTrue(up.closed, "the upstream connection was left open")

    def test_a_response_that_is_never_iterated_closes_the_upstream(self):
        # The relay's own finally: only runs once the generator has STARTED,
        # and Werkzeug substitutes an empty body for a HEAD -- so this path
        # builds the response and never touches it. Without call_on_close()
        # the daemon keeps writing into a socket nobody reads: an ffmpeg, a
        # subscriber queue and one of its three stream slots, held until
        # refcounting happens to finalise the connection.
        up = _FakeUpstream(b"z" * 50000)
        with mock.patch("urllib.request.urlopen", return_value=up):
            r = self.c.head("/api/nwr/listen/stream")
            r.close()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(up.reads, 0, "the body was iterated; this is not the HEAD path")
        self.assertTrue(up.closed,
                        "a response that was never iterated left the daemon streaming")

    def test_a_stream_that_goes_silent_ends_instead_of_raising(self):
        # A TimeoutError out of a WSGI iterator truncates the audio AND leaves
        # a traceback in the journal; a clean end-of-stream says the same thing
        # without asking the operator to interpret one.
        up = _StallingUpstream(b"m" * 4096)
        with mock.patch("urllib.request.urlopen", return_value=up):
            r = self.c.get("/api/nwr/listen/stream")
            data = r.get_data()
        self.assertEqual(data, b"m" * 4096)
        self.assertTrue(up.closed, "the upstream connection was left open")

    def test_a_client_that_hangs_up_closes_the_upstream(self):
        # An abandoned relay would leave a subscriber — and its ffmpeg — alive
        # on the daemon for as long as this process runs.
        up = _FakeUpstream(b"y" * 100000)
        with mock.patch("urllib.request.urlopen", return_value=up):
            r = self.c.get("/api/nwr/listen/stream")
            gen = r.response
            next(iter(gen))
            r.close()
        self.assertTrue(up.closed, "the upstream connection was left open")


class NwrFlaskWriteTripwireTest(unittest.TestCase):
    """A TRIPWIRE over routes.py's source text, not proof of anything.

    The daemon is the only writer of the alert store (see its module docstring:
    alerts.record() takes a module lock, which means nothing across processes,
    and two read-modify-write callers lose updates under exactly the conditions
    that matter — several counties, several messages, close together). Proving
    Flask cannot reach a write would mean proving a negative about every call
    graph; what these do instead is fail the day someone types the call, which
    is far earlier than the day two alerts arrive together. An indirection
    would walk straight past them, and that is the accepted limit."""

    def setUp(self):
        with open(nwr_routes.__file__, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_the_source_never_names_alerts_record(self):
        self.assertNotIn("alerts.record", self.src,
                         "Flask must never write the alert store — the daemon is "
                         "its single writer")

    def test_the_source_neither_speaks_nor_decides_to(self):
        for gone in ("announce.speak", "bell.should_speak"):
            self.assertNotIn(gone, self.src,
                             f"{gone} belongs to the daemon: an alert must be "
                             "spoken whether or not a browser is open")

    def test_the_source_names_no_encoder_and_no_subscriber_queue(self):
        # What actually makes the v1 deadlock impossible here is that neither
        # exists in this process; this only notices one being reintroduced.
        for gone in ("subprocess", "stream_encoder", "listener.subscribe",
                     "listener.start", "listener.stop"):
            self.assertNotIn(gone, self.src,
                             f"{gone} is capture, and capture lives in the daemon")


if __name__ == "__main__":
    unittest.main()
