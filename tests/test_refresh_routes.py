import contextlib, json, os, sys, unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "server"))

try:
    from flask import Flask
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False

# Mutating /api routes require this header (common/web_guard.py).
_HDR = {"X-OASIS-Request": "1"}


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class TestRefreshRoutes(unittest.TestCase):
    def setUp(self):
        from routes import refresh as RR
        self.RR = RR
        app = Flask(__name__)
        app.register_blueprint(RR.bp)
        self.c = app.test_client()

    def test_status_shape(self):
        r = self.c.get("/api/refresh/status")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.data)
        # ok means the request succeeded, NOT that the data is current.
        self.assertTrue(body["ok"])
        self.assertIsInstance(body["sources"], list)
        self.assertIn("metered", body)

    def test_status_rows_carry_ui_fields(self):
        body = json.loads(self.c.get("/api/refresh/status").data)
        for row in body["sources"]:
            for key in ("id", "label", "state", "tier", "age_days",
                        "max_age_days", "attribution", "last_success"):
                self.assertIn(key, row)

    def test_status_never_fetches(self):
        with mock.patch.object(self.RR.R, "run_pass") as rp:
            rp.return_value = {"ok": True, "sources": [], "metered": True,
                               "checked_at": 0}
            self.c.get("/api/refresh/status")
            self.assertTrue(rp.call_args.kwargs["dry_run"])

    def test_run_requires_post(self):
        self.assertEqual(self.c.get("/api/refresh/run").status_code, 405)

    def test_run_without_csrf_header_is_refused(self):
        # Mutating /api routes must carry X-OASIS-Request (common/web_guard.py).
        self.assertNotEqual(
            self.c.post("/api/refresh/run", json={}).status_code, 200)

    def test_run_accepts_source_filter(self):
        with mock.patch.object(self.RR.R, "run_pass") as rp:
            rp.return_value = {"ok": True, "sources": [], "metered": False,
                               "checked_at": 0}
            self.c.post("/api/refresh/run", json={"source": "fcc"},
                        headers=_HDR)
            self.assertEqual(rp.call_args.kwargs["only"], ["fcc"])

    def test_run_passes_force_through(self):
        with mock.patch.object(self.RR.R, "run_pass") as rp:
            rp.return_value = {"ok": True, "sources": [], "metered": False,
                               "checked_at": 0}
            self.c.post("/api/refresh/run", json={"force": True},
                        headers=_HDR)
            self.assertTrue(rp.call_args.kwargs["force"])

    def test_run_with_no_body_does_not_500(self):
        with mock.patch.object(self.RR.R, "run_pass") as rp:
            rp.return_value = {"ok": True, "sources": [], "metered": False,
                               "checked_at": 0}
            self.assertEqual(
                self.c.post("/api/refresh/run", headers=_HDR).status_code, 200)

    def test_run_reports_busy_when_locked(self):
        @contextlib.contextmanager
        def _held(_root):
            yield False

        with mock.patch.object(self.RR.R, "pass_lock", _held):
            body = json.loads(
                self.c.post("/api/refresh/run", json={}, headers=_HDR).data)
            self.assertTrue(body["ok"])       # the request succeeded
            self.assertTrue(body["busy"])     # the news is "already running"


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class TestGuardianYield(unittest.TestCase):
    def setUp(self):
        from routes import refresh as RR
        self.RR = RR

    def test_disabled_guardian_never_blocks(self):
        fake = mock.Mock(_load_guardian_config=lambda: {"enabled": False},
                         _GUARD_STATS={})
        with mock.patch.dict(sys.modules, {"routes.hardware": fake}):
            self.assertFalse(self.RR.guardian_busy())

    def test_cool_box_does_not_block(self):
        fake = mock.Mock(
            _load_guardian_config=lambda: {
                "enabled": True,
                "thresholds": {"temp_c": 80, "cpu_pct": 95, "mem_pct": 92}},
            _GUARD_STATS={"temp_c": 45.0, "cpu_pct": 10.0, "mem_pct": 30.0})
        with mock.patch.dict(sys.modules, {"routes.hardware": fake}):
            self.assertFalse(self.RR.guardian_busy())

    def test_hot_box_blocks_before_the_guardian_would_fire(self):
        # 70 C is under the 80 C trip point but over 85% of it, so the
        # refresher yields rather than being the thing that trips STOP ALL.
        fake = mock.Mock(
            _load_guardian_config=lambda: {
                "enabled": True,
                "thresholds": {"temp_c": 80, "cpu_pct": 95, "mem_pct": 92}},
            _GUARD_STATS={"temp_c": 70.0, "cpu_pct": 10.0, "mem_pct": 30.0})
        with mock.patch.dict(sys.modules, {"routes.hardware": fake}):
            self.assertTrue(self.RR.guardian_busy())

    def test_unreadable_guardian_does_not_block_forever(self):
        fake = mock.Mock()
        fake._load_guardian_config.side_effect = RuntimeError("nope")
        with mock.patch.dict(sys.modules, {"routes.hardware": fake}):
            self.assertFalse(self.RR.guardian_busy())


if __name__ == "__main__":
    unittest.main()
