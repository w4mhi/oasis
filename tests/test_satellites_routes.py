import json, os, sys, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

# Probe the optional pass-prediction dep (skyfield/numpy/sgp4). The server boots
# without it (routes.py lazy-imports predict), so the route tests that exercise
# prediction skip cleanly when it's absent — e.g. the minimal CI server-setup job.
# CachePlanTest below is predict-free and always runs.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
    import predict as _predict_probe  # noqa: F401,E402
    _HAS_PREDICT = True
except Exception:  # noqa: BLE001
    _HAS_PREDICT = False


@unittest.skipUnless(_HAS_PREDICT, "skyfield/predict not installed")
class RoutesTest(unittest.TestCase):
    def setUp(self):
        # Dotted import (not bare `import routes`) — the repo already has a
        # top-level `routes` package (server/routes/), so a bare import here
        # would silently resolve to whichever one another test imported first
        # under unittest discover. Matches the existing services.map pattern
        # in tests/test_aprs_server_routes.py.
        from services.satellites import routes  # services/satellites/routes.py
        self.routes = routes
        # routes.py imports `predict` (skyfield/numpy) lazily inside each
        # function body rather than at module top, so the server boots even
        # if skyfield/numpy is missing. `services/satellites` was put on
        # sys.path as a side effect of importing `routes` above, so this
        # bare import resolves to the exact same cached module object that
        # routes.py's local `import predict` statements will pick up.
        import predict
        self.predict = predict
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        from oasis_testclient import bare_client, csrf_client
        self.client = csrf_client(app)
        self.bare = bare_client(app)      # no CSRF header — proves the guard

        # Redirect config + cache to a temp dir seeded from the fixture.
        self._tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self._tmp, "configuration", "tle-cache"))
        fx = os.path.join(_HERE, "fixtures", "tle-sample.txt")
        open(os.path.join(self._tmp, "configuration", "tle-cache", "stations.txt"), "w").write(open(fx).read())
        json.dump({"lat": 47.5495, "lon": -122.0298},
                  open(os.path.join(self._tmp, "configuration", "station.json"), "w"))
        import shutil
        shutil.copy(os.path.join(_HERE, "fixtures", "satellites-sample.json"),
                    os.path.join(self._tmp, "configuration", "satellites.json"))
        self.routes.SUITE_ROOT = self._tmp

    def test_api_satellites_shape(self):
        r = self.client.get("/api/satellites")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("satellites", data)
        self.assertIn("tle_age_days", data)
        self.assertIn("station", data)
        # Each roster entry carries its TLE lines so the client can propagate
        # live look-angles for the workability pill (l1/l2 present, None if the
        # satellite is absent from the cache). ISS is in the fixture.
        for s in data["satellites"]:
            self.assertIn("l1", s)
            self.assertIn("l2", s)
        iss = [s for s in data["satellites"] if s["norad"] == 25544][0]
        self.assertTrue(iss["l1"].startswith("1 25544"))
        self.assertTrue(iss["l2"].startswith("2 25544"))
        # Each downlink is tagged with v1 support + blurb for the roster buttons.
        for dl in iss["downlinks"]:
            self.assertIn("supported", dl)
            self.assertIn("blurb", dl)
        # ISS has FM voice + APRS — both supported in v1.
        self.assertTrue(all(dl["supported"] for dl in iss["downlinks"]))

    def test_passes_endpoint(self):
        r = self.client.get("/api/satellites/passes?window=24")
        self.assertEqual(r.status_code, 200)
        self.assertIn("passes", r.get_json())

    def test_select_toggles(self):
        r = self.client.post("/api/satellites/select", json={"norad": 25544, "selected": False})
        self.assertEqual(r.status_code, 200)
        # The response is the monitored set, not the whole roster echoed back:
        # a single checkbox used to return ~150 birds with their TLE lines.
        self.assertNotIn(25544, r.get_json()["selected"])

    # ── Selection: both request shapes, one write ─────────────────────────────
    def test_select_bulk_applies_the_whole_set(self):
        r = self.client.post("/api/satellites/select",
                             json={"selections": {"25544": True}})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn(25544, d["selected"])
        self.assertEqual(d["applied"], 1)

    def test_select_bulk_is_a_single_write(self):
        # The whole point: a burst of N single-toggle requests raced itself and
        # lost most of them. One request must mean one load-modify-save.
        import roster as roster_mod

        calls = []
        original = roster_mod.save
        roster_mod.save = lambda p, d: calls.append(p) or original(p, d)
        try:
            self.client.post("/api/satellites/select",
                             json={"selections": {"25544": True, "43017": False}})
        finally:
            roster_mod.save = original
        self.assertEqual(len(calls), 1)

    def test_select_single_shape_still_works(self):
        # A cached page from before the bulk endpoint must keep working.
        r = self.client.post("/api/satellites/select",
                             json={"norad": 25544, "selected": True})
        self.assertEqual(r.status_code, 200)
        self.assertIn(25544, r.get_json()["selected"])

    def test_select_rejects_a_malformed_body(self):
        for body in ({}, {"norad": "nope", "selected": True}, {"selections": [1, 2]}):
            r = self.client.post("/api/satellites/select", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn("error", r.get_json())

    def test_select_cannot_add_satellites_to_the_roster(self):
        before = len(self.client.get("/api/satellites").get_json()["satellites"])
        self.client.post("/api/satellites/select", json={"selections": {"999999": True}})
        after = self.client.get("/api/satellites").get_json()["satellites"]
        self.assertEqual(len(after), before)
        self.assertNotIn(999999, [s["norad"] for s in after])

    def test_bells_toggle_and_echo_the_armed_set(self):
        r = self.client.post("/api/satellites/bells", json={"norad": 25544, "bell": True})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertIn(25544, d["bells"])
        self.assertEqual(d["applied"], 1)

    def test_bells_bulk_is_a_single_write(self):
        import roster as roster_mod

        calls = []
        original = roster_mod.save
        roster_mod.save = lambda p, d: calls.append(p) or original(p, d)
        try:
            self.client.post("/api/satellites/bells",
                             json={"bells": {"25544": True, "43017": True}})
        finally:
            roster_mod.save = original
        self.assertEqual(len(calls), 1)

    def test_bells_reach_the_roster_the_kiosk_reads(self):
        """The whole point of moving the bell off localStorage: a bell armed
        from one browser has to be visible to the kiosk, which only ever sees
        GET /api/satellites."""
        self.client.post("/api/satellites/bells", json={"bells": {"25544": True}})
        sats = self.client.get("/api/satellites").get_json()["satellites"]
        iss = next(s for s in sats if s["norad"] == 25544)
        self.assertTrue(iss["bell"])

    def test_every_satellite_reports_the_full_operator_key_set(self):
        """Contract §5: a roster written before bells existed has no `bell` key,
        and a consumer must not have to tell "off" apart from "unsupported"."""
        import roster as roster_mod
        sats = self.client.get("/api/satellites").get_json()["satellites"]
        self.assertTrue(sats)
        for s in sats:
            for f in roster_mod.OPERATOR_FIELDS:
                self.assertIn(f, s)

    def test_bells_do_not_disturb_the_monitored_set(self):
        self.client.post("/api/satellites/select", json={"selections": {"25544": True}})
        self.client.post("/api/satellites/bells", json={"bells": {"25544": False}})
        sats = self.client.get("/api/satellites").get_json()["satellites"]
        iss = next(s for s in sats if s["norad"] == 25544)
        self.assertTrue(iss["selected"])
        self.assertFalse(iss["bell"])

    def test_bells_reject_a_malformed_body(self):
        for body in ({}, {"norad": "nope", "bell": True}, {"bells": [1, 2]}):
            r = self.client.post("/api/satellites/bells", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.get_json()["code"], "INVALID_BELLS")

    def test_bells_cannot_add_satellites_to_the_roster(self):
        before = len(self.client.get("/api/satellites").get_json()["satellites"])
        self.client.post("/api/satellites/bells", json={"bells": {"999999": True}})
        after = self.client.get("/api/satellites").get_json()["satellites"]
        self.assertEqual(len(after), before)

    def test_bells_without_csrf_header_are_refused(self):
        # Read FIRST, so the one-shot "monitoring arms the bell" backfill has
        # already settled: the fixture is a pre-migration roster (ISS monitored,
        # no bell key), so asserting a bare `False` afterwards would be measuring
        # the default, not the CSRF guard. The refused write then tries to CHANGE
        # the settled state, which is the only version of this that can fail for
        # the reason the test is named after.
        def bell():
            sats = self.client.get("/api/satellites").get_json()["satellites"]
            return next(s for s in sats if s["norad"] == 25544)["bell"]
        self.assertTrue(bell())
        r = self.bare.post("/api/satellites/bells", json={"norad": 25544, "bell": False})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(bell())

    def test_listen_status_shape(self):
        r = self.client.get("/api/satellites/listen/status")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        for k in ("recording", "missing_deps", "dongle_present", "busy", "holder"):
            self.assertIn(k, d)

    def test_listen_degrades_without_hardware(self):
        # No rtl_fm/sox/dongle in CI -> a clean 4xx with an error, never a 500.
        r = self.client.post("/api/satellites/listen", json={"norad": 25544})
        self.assertIn(r.status_code, (400, 409))
        self.assertIn("error", r.get_json())

    def test_listen_rejects_freq_not_a_downlink(self):
        # An override that matches no downlink is a 400 before any hardware check
        # would matter (the precondition order: deps first). On a dev box with no
        # rtl_fm the deps check fires first — accept either as a clean 4xx.
        r = self.client.post("/api/satellites/listen",
                             json={"norad": 25544, "freq_mhz": 999.0})
        self.assertIn(r.status_code, (400, 409))
        self.assertIn("error", r.get_json())

    # ── CSRF ──────────────────────────────────────────────────────────────────
    # These routes parse with get_json(force=True), so a text/plain body needs no
    # CORS preflight: before the guard, any page the operator visited could seize
    # the RTL-SDR and start a capture, or rewrite the shared roster. Built with a
    # PLAIN client so the suite-wide header default can't mask a missing guard.
    def test_listen_without_csrf_header_is_forbidden_and_starts_nothing(self):
        import listen

        started = []
        original = listen.start
        listen.start = lambda *a, **k: started.append(a) or {}
        try:
            r = self.bare.post("/api/satellites/listen", json={"norad": 25544})
        finally:
            listen.start = original
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json(), {"ok": False, "error": "forbidden"})
        self.assertEqual(started, [], "capture started despite a rejected request")

    def test_listen_rejects_a_simple_cross_origin_post(self):
        # The exact shape that dodges a preflight: text/plain body, JSON content.
        r = self.bare.post("/api/satellites/listen",
                           data='{"norad": 25544}', content_type="text/plain")
        self.assertEqual(r.status_code, 403)

    def test_select_without_csrf_header_leaves_the_roster_untouched(self):
        before = self.client.get("/api/satellites").get_json()["satellites"]
        r = self.bare.post("/api/satellites/select",
                           data='{"norad": 25544, "selected": false}',
                           content_type="text/plain")
        self.assertEqual(r.status_code, 403)
        after = self.client.get("/api/satellites").get_json()["satellites"]
        self.assertEqual([s["selected"] for s in before], [s["selected"] for s in after])

    def test_refresh_and_listen_stop_are_guarded(self):
        self.assertEqual(self.bare.post("/api/satellites/refresh").status_code, 403)
        self.assertEqual(self.bare.post("/api/satellites/listen/stop").status_code, 403)

    def test_reads_stay_open_without_the_header(self):
        # The guard must not touch GETs — the dashboard polls these unauthenticated.
        for path in ("/api/satellites", "/api/satellites/listen/status"):
            self.assertEqual(self.bare.get(path).status_code, 200, path)

    # ── Disk budget ───────────────────────────────────────────────────────────
    # A dev box has no rtl_fm and no dongle, so _prep_capture rejects long before
    # the disk budget would matter. Stub it out to reach the code under test.
    def _allow_capture(self):
        entry = {"name": "ISS (ZARYA)"}
        self._orig_prep = self.routes._prep_capture
        # 5-tuple since DRAWS: the last slot is the radio channel, None on an
        # RTL-SDR capture. A 4-tuple stub makes the real view raise ValueError
        # into its generic handler, turning every refusal into a 500.
        self.routes._prep_capture = (
            lambda norad, freq_mhz=None: (entry, 145_800_000, None, "fm", None))
        self.addCleanup(lambda: setattr(self.routes, "_prep_capture", self._orig_prep))

    def test_listen_refuses_when_the_card_is_full(self):
        import listen

        self._allow_capture()
        started = []
        orig_start, orig_check = listen.start, listen.check_free_space
        listen.start = lambda *a, **k: started.append(a) or {}
        listen.check_free_space = lambda *a, **k: (False, "not enough disk space to record: 12 MB free")
        try:
            r = self.client.post("/api/satellites/listen", json={"norad": 25544})
        finally:
            listen.start, listen.check_free_space = orig_start, orig_check
        self.assertEqual(r.status_code, 507)
        self.assertIn("disk space", r.get_json()["error"])
        self.assertEqual(started, [], "capture started with no room for it")

    def test_listen_prunes_before_starting(self):
        import listen

        self._allow_capture()
        calls = []
        orig_prune, orig_start = listen.prune_recordings, listen.start
        listen.prune_recordings = lambda d, **k: calls.append(d) or {
            "deleted": [], "bytes_freed": 0, "total_bytes": 0}
        listen.start = lambda *a, **k: {"recording": True}
        try:
            self.client.post("/api/satellites/listen", json={"norad": 25544})
        finally:
            listen.prune_recordings, listen.start = orig_prune, orig_start
        self.assertEqual(calls, [listen.recordings_dir(self.routes.SUITE_ROOT)])

    def test_stop_prunes_after_the_file_is_closed(self):
        import listen

        calls = []
        orig_prune, orig_stop = listen.prune_recordings, listen.stop
        listen.prune_recordings = lambda d, **k: calls.append(d) or {
            "deleted": [], "bytes_freed": 0, "total_bytes": 0}
        listen.stop = lambda: {"recording": False}
        try:
            r = self.client.post("/api/satellites/listen/stop")
        finally:
            listen.prune_recordings, listen.stop = orig_prune, orig_stop
        self.assertEqual(r.status_code, 200)
        self.assertEqual(calls, [listen.recordings_dir(self.routes.SUITE_ROOT)])

    def test_passes_cache_reused(self):
        calls = []
        orig = self.predict.compute_passes
        def counting(*a, **k):
            calls.append(1)
            return orig(*a, **k)
        self.predict.compute_passes = counting
        try:
            self.client.get("/api/satellites/passes?window=24")
            n1 = len(calls)
            self.assertGreater(n1, 0)                 # first request computed
            self.client.get("/api/satellites/passes?window=24")
            self.assertEqual(len(calls), n1)          # identical 2nd request hit the cache
        finally:
            self.predict.compute_passes = orig

    def test_passes_over_budget_returns_200_and_resumes(self):
        # A roster too large to finish in one budget window must NOT 500 (the old
        # behaviour: worker timeout → cache never written → stuck at 500 forever).
        # It returns 200 with partial data and persists progress; a later request
        # resumes and completes.
        orig_budget = self.routes._PASSES_BUDGET_S
        try:
            self.routes._PASSES_BUDGET_S = -1        # force "out of budget" at once
            r = self.client.get("/api/satellites/passes?window=24")
            self.assertEqual(r.status_code, 200)     # partial, never 500
            self.assertIn("passes", r.get_json())
            self.routes._PASSES_BUDGET_S = orig_budget   # normal budget → resume
            r2 = self.client.get("/api/satellites/passes?window=24")
            self.assertEqual(r2.status_code, 200)
            self.assertIn("25544", r2.get_json()["passes"])   # ISS computed on resume
        finally:
            self.routes._PASSES_BUDGET_S = orig_budget

class CachePlanTest(unittest.TestCase):
    """_cache_plan(cached, now_ts, ttl) -> (action, base_ts). Pure decision for
    how to use an on-disk passes cache. Regression guard for the frozen-cache bug:
    passes are ABSOLUTE times computed at `computed_at`; once the entry is older
    than the TTL its whole window has elapsed, so it must be RECOMPUTED from now —
    never served (the old code reused a stale-but-complete cache forever, so every
    pass eventually slid into the past and the roster went dark)."""

    def setUp(self):
        from services.satellites import routes
        self.routes = routes
        self.TTL = routes._CACHE_TTL_S

    def test_missing_cache_is_fresh_compute(self):
        self.assertEqual(self.routes._cache_plan({}, 1000.0, self.TTL),
                         ("fresh", 1000.0))

    def test_fresh_complete_is_served(self):
        c = {"computed_at": 1000.0, "complete": True}
        self.assertEqual(self.routes._cache_plan(c, 1000.0 + self.TTL - 1, self.TTL),
                         ("serve", 1000.0))

    def test_stale_complete_is_recomputed_not_served(self):
        # THE BUG: a complete cache older than the TTL describes an elapsed window.
        c = {"computed_at": 1000.0, "complete": True}
        now = 1000.0 + self.TTL + 1
        self.assertEqual(self.routes._cache_plan(c, now, self.TTL), ("fresh", now))

    def test_fresh_incomplete_resumes_from_original_start(self):
        c = {"computed_at": 1000.0, "complete": False}
        self.assertEqual(self.routes._cache_plan(c, 1000.0 + 5, self.TTL),
                         ("resume", 1000.0))

    def test_stale_incomplete_is_recomputed(self):
        c = {"computed_at": 1000.0, "complete": False}
        now = 1000.0 + self.TTL + 1
        self.assertEqual(self.routes._cache_plan(c, now, self.TTL), ("fresh", now))

    def test_legacy_cache_without_computed_at_is_recomputed(self):
        # A pre-fix cache file (no computed_at) can't be trusted to be current.
        c = {"passes": {"25544": []}}
        self.assertEqual(self.routes._cache_plan(c, 1000.0, self.TTL),
                         ("fresh", 1000.0))


class PassesStalenessTest(RoutesTest):
    """End-to-end guard: a stale complete cache on disk must trigger a real
    recompute (compute_passes called again) rather than serving elapsed passes."""

    def test_stale_cache_triggers_recompute(self):
        # Prime the cache with a normal request, then backdate its computed_at
        # past the TTL and confirm the next request recomputes instead of serving.
        self.client.get("/api/satellites/passes?window=24")
        # Locate the single cache file and age its computed_at beyond the TTL.
        cache_root = os.path.join(self._tmp, "configuration", "tle-cache", "_passes")
        files = [os.path.join(cache_root, f) for f in os.listdir(cache_root)]
        self.assertTrue(files)
        cf = files[0]
        data = json.load(open(cf))
        data["computed_at"] = data["computed_at"] - self.routes._CACHE_TTL_S - 60
        json.dump(data, open(cf, "w"))

        calls = []
        orig = self.predict.compute_passes
        def counting(*a, **k):
            calls.append(1)
            return orig(*a, **k)
        self.predict.compute_passes = counting
        try:
            r = self.client.get("/api/satellites/passes?window=24")
            self.assertEqual(r.status_code, 200)
            self.assertGreater(len(calls), 0)   # stale window → recomputed, not served
        finally:
            self.predict.compute_passes = orig


if __name__ == "__main__":
    unittest.main()
