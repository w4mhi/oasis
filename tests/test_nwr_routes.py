import json
import os
import shutil
import sys
import tempfile
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
from services.nwr.common import settings  # noqa: E402


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
        self.assertTrue(s["speak"])

    def test_save_and_reload(self):
        settings.save(self.root, {"channel_hz": 162400000,
                                  "watch_fips": ["53033"], "speak": False})
        s = settings.load(self.root)
        self.assertEqual(s["channel_hz"], 162400000)
        self.assertEqual(s["watch_fips"], ["53033"])
        self.assertFalse(s["speak"])

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


if __name__ == "__main__":
    unittest.main()
