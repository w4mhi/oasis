import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import refresh as R
from common import freshness as F


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "configuration"), exist_ok=True)

    def _station(self, **kw):
        p = os.path.join(self.d, "configuration", "station.json")
        with open(p, "w") as fh:
            json.dump(kw, fh)


class TestState(_Tmp):
    def test_missing_state_file_is_empty_dict(self):
        self.assertEqual(R.load_state(self.d), {})

    def test_round_trip(self):
        R.save_state(self.d, {"tle": {"consecutive_failures": 2}})
        self.assertEqual(R.load_state(self.d)["tle"]["consecutive_failures"], 2)

    def test_corrupt_state_file_does_not_raise(self):
        # A truncated write (power loss mid-save) must degrade to "no state",
        # never take the server down at import time.
        with open(os.path.join(self.d, "configuration",
                               "refresh-state.json"), "w") as fh:
            fh.write("{not json")
        self.assertEqual(R.load_state(self.d), {})

    def test_non_dict_state_file_degrades(self):
        with open(os.path.join(self.d, "configuration",
                               "refresh-state.json"), "w") as fh:
            fh.write("[1, 2, 3]")
        self.assertEqual(R.load_state(self.d), {})


class TestLock(_Tmp):
    def test_second_acquire_skips_rather_than_queues(self):
        with R.pass_lock(self.d) as got_first:
            self.assertTrue(got_first)
            with R.pass_lock(self.d) as got_second:
                self.assertFalse(got_second)

    def test_lock_released_after_block(self):
        with R.pass_lock(self.d):
            pass
        with R.pass_lock(self.d) as got:
            self.assertTrue(got)

    def test_lock_released_even_when_body_raises(self):
        with self.assertRaises(RuntimeError):
            with R.pass_lock(self.d):
                raise RuntimeError("boom")
        with R.pass_lock(self.d) as got:
            self.assertTrue(got)

    def test_stale_lock_is_reclaimed(self):
        path = os.path.join(self.d, "configuration", ".refresh.lock")
        with open(path, "w") as fh:
            fh.write("99999")
        os.utime(path, (0, 0))          # ancient -> a hard-killed pass
        with R.pass_lock(self.d) as got:
            self.assertTrue(got)


class TestConfig(_Tmp):
    def test_max_age_default_when_unset(self):
        self._station(callsign="W4MHI")
        cfg = R.station_config(self.d)
        src = R._by_id("tle")
        self.assertEqual(R.max_age_for(cfg, src), src.max_age_days)

    def test_max_age_override(self):
        self._station(max_age_days={"tle": 11})
        cfg = R.station_config(self.d)
        self.assertEqual(R.max_age_for(cfg, R._by_id("tle")), 11)

    def test_garbage_override_falls_back_to_default(self):
        self._station(max_age_days={"tle": "soon"})
        cfg = R.station_config(self.d)
        src = R._by_id("tle")
        self.assertEqual(R.max_age_for(cfg, src), src.max_age_days)

    def test_missing_station_json_is_empty_config(self):
        self.assertEqual(R.station_config(self.d), {})

    # No shipped source carries a credential today, but credential_for is live
    # machinery for any future one — exercised here with a synthetic source.
    def _credentialed(self):
        return R.Source(id="c", label="c", max_age_days=1.0, tier="small",
                        credential="some_token", probe=lambda root: None,
                        fetch=lambda root, cfg: True, attribution="")

    def test_credential_lookup(self):
        self._station(some_token="abc")
        cfg = R.station_config(self.d)
        self.assertEqual(R.credential_for(cfg, self._credentialed()), "abc")

    def test_blank_credential_reads_as_absent(self):
        # An empty string in a config template must not look configured.
        self._station(some_token="   ")
        cfg = R.station_config(self.d)
        self.assertIsNone(R.credential_for(cfg, self._credentialed()))

    def test_sources_without_credentials_return_none(self):
        self.assertIsNone(R.credential_for({}, R._by_id("tle")))


class TestRegistry(unittest.TestCase):
    def test_v1_sources_present(self):
        ids = {s.id for s in R.REGISTRY}
        self.assertEqual(ids, {"tle", "satnogs", "fcc", "repeaterbook"})

    def test_tiers(self):
        by = {s.id: s for s in R.REGISTRY}
        self.assertEqual(by["tle"].tier, "small")
        self.assertEqual(by["satnogs"].tier, "small")
        self.assertEqual(by["fcc"].tier, "large")
        self.assertEqual(by["repeaterbook"].tier, "small")

    def test_no_source_needs_a_credential(self):
        # OASIS holds no third-party API credential. RepeaterBook is
        # report-only precisely so it does not need one.
        for s in R.REGISTRY:
            self.assertIsNone(s.credential, s.id)

    def test_repeaterbook_is_report_only(self):
        # fetch=None: OASIS reports the CSV's age but never downloads it.
        self.assertIsNone(R._by_id("repeaterbook").fetch)
        self.assertIsNotNone(R._by_id("repeaterbook").probe)

    def test_every_other_source_is_fetchable(self):
        for s in R.REGISTRY:
            if s.id != "repeaterbook":
                self.assertIsNotNone(s.fetch, s.id)

    def test_repeaterbook_carries_required_attribution(self):
        # Required by RepeaterBook's terms, not decoration.
        by = {s.id: s for s in R.REGISTRY}
        self.assertIn("RepeaterBook.com", by["repeaterbook"].attribution)

    def test_every_source_has_an_attribution_field(self):
        for s in R.REGISTRY:
            self.assertIsInstance(s.attribution, str)


class TestRunPass(_Tmp):
    def _stub(self, sid, age, calls, ok=True, tier="small", credential=None):
        return R.Source(id=sid, label=sid, max_age_days=3.0, tier=tier,
                        credential=credential,
                        probe=lambda root: None if age is None else 0.0,
                        fetch=lambda root, cfg: (calls.append(sid), ok)[1],
                        attribution="")

    def test_offline_pass_is_ok_with_reasons(self):
        calls = []
        src = self._stub("x", age=None, calls=calls, ok=False)
        out = R.run_pass(self.d, now=0.0, metered=False, registry=[src])
        # ok reports that the pass RAN, not that data is current.
        self.assertTrue(out["ok"])
        self.assertEqual(out["sources"][0]["state"], F.MISSING)
        self.assertFalse(out["sources"][0]["fetched"])

    def test_fresh_source_not_fetched(self):
        calls = []
        src = self._stub("x", age=0.0, calls=calls)
        R.run_pass(self.d, now=0.0, metered=False, registry=[src])
        self.assertEqual(calls, [])

    def test_dry_run_never_fetches(self):
        calls = []
        src = self._stub("x", age=None, calls=calls)
        R.run_pass(self.d, now=0.0, metered=False, registry=[src],
                   dry_run=True)
        self.assertEqual(calls, [])

    def test_dry_run_writes_no_state(self):
        src = self._stub("x", age=None, calls=[])
        R.run_pass(self.d, now=0.0, metered=False, registry=[src],
                   dry_run=True)
        self.assertFalse(os.path.exists(
            os.path.join(self.d, "configuration", "refresh-state.json")))

    def test_only_filters_sources(self):
        calls = []
        a = self._stub("a", age=None, calls=calls)
        b = self._stub("b", age=None, calls=calls)
        out = R.run_pass(self.d, now=0.0, metered=False, registry=[a, b],
                         only=["b"])
        self.assertEqual(calls, ["b"])
        self.assertEqual([r["id"] for r in out["sources"]], ["b"])

    def test_deferred_large_source_is_not_fetched(self):
        calls = []
        src = self._stub("x", age=None, calls=calls, tier="large")
        out = R.run_pass(self.d, now=0.0, metered=True, registry=[src])
        self.assertEqual(out["sources"][0]["state"], F.DEFERRED)
        self.assertEqual(calls, [])

    def test_unconfigured_source_is_not_fetched(self):
        calls = []
        src = self._stub("x", age=None, calls=calls,
                         credential="some_token")
        out = R.run_pass(self.d, now=0.0, metered=False, registry=[src])
        self.assertEqual(out["sources"][0]["state"], F.UNCONFIGURED)
        self.assertEqual(calls, [])

    def test_failure_increments_backoff_counter(self):
        calls = []
        src = self._stub("x", age=None, calls=calls, ok=False)
        R.run_pass(self.d, now=0.0, metered=False, registry=[src])
        self.assertEqual(R.load_state(self.d)["x"]["consecutive_failures"], 1)

    def test_raising_fetch_is_recorded_not_propagated(self):
        def _boom(root, cfg):
            raise OSError("network unreachable")
        src = R.Source(id="x", label="x", max_age_days=3.0, tier="small",
                       credential=None, probe=lambda root: None,
                       fetch=_boom, attribution="")
        out = R.run_pass(self.d, now=0.0, metered=False, registry=[src])
        self.assertTrue(out["ok"])
        self.assertIn("network unreachable", out["sources"][0]["error"])

    def test_raising_probe_is_treated_as_missing(self):
        def _boom(root):
            raise OSError("permission denied")
        src = R.Source(id="x", label="x", max_age_days=3.0, tier="small",
                       credential=None, probe=_boom,
                       fetch=lambda root, cfg: True, attribution="")
        out = R.run_pass(self.d, now=0.0, metered=False, registry=[src],
                         dry_run=True)
        self.assertEqual(out["sources"][0]["state"], F.MISSING)

    def test_success_resets_backoff_counter(self):
        R.save_state(self.d, {"x": {"consecutive_failures": 5}})
        calls = []
        src = self._stub("x", age=None, calls=calls, ok=True)
        R.run_pass(self.d, now=0.0, metered=False, registry=[src])
        self.assertEqual(R.load_state(self.d)["x"]["consecutive_failures"], 0)

    # Back-off timestamps below are deliberately non-zero: 0.0 means "never
    # attempted", and in production last_attempt is always a real epoch.
    def test_backoff_suppresses_retry(self):
        R.save_state(self.d, {"x": {"consecutive_failures": 3,
                                    "last_attempt": 100.0}})
        calls = []
        src = self._stub("x", age=None, calls=calls)
        # 3 failures -> 7200s back-off; 100s later is still inside it.
        R.run_pass(self.d, now=200.0, metered=False, registry=[src])
        self.assertEqual(calls, [])

    def test_failure_counter_without_timestamp_still_retries(self):
        # Nothing to back off FROM. Without this the counter would suppress the
        # very retry that clears it.
        R.save_state(self.d, {"x": {"consecutive_failures": 4}})
        calls = []
        src = self._stub("x", age=None, calls=calls)
        R.run_pass(self.d, now=200.0, metered=False, registry=[src])
        self.assertEqual(calls, ["x"])

    def test_retry_resumes_after_backoff_expires(self):
        R.save_state(self.d, {"x": {"consecutive_failures": 1,
                                    "last_attempt": 100.0}})
        calls = []
        src = self._stub("x", age=None, calls=calls)
        R.run_pass(self.d, now=100.0 + 1801, metered=False, registry=[src])
        self.assertEqual(calls, ["x"])

    def test_force_ignores_backoff_and_freshness(self):
        R.save_state(self.d, {"x": {"consecutive_failures": 9,
                                    "last_attempt": 100.0}})
        calls = []
        src = self._stub("x", age=0.0, calls=calls)
        R.run_pass(self.d, now=101.0, metered=False, registry=[src],
                   force=True)
        self.assertEqual(calls, ["x"])

    def test_row_carries_fields_the_ui_needs(self):
        src = self._stub("x", age=0.0, calls=[])
        row = R.run_pass(self.d, now=0.0, metered=False, registry=[src],
                         dry_run=True)["sources"][0]
        for key in ("id", "label", "state", "tier", "age_days",
                    "max_age_days", "attribution", "fetched", "error",
                    "last_success", "backoff_active"):
            self.assertIn(key, row)


class TestRosterAdapter(_Tmp):
    def test_probe_tle_returns_none_when_cache_empty(self):
        self.assertIsNone(R.probe_tle(self.d))

    def test_probe_tle_returns_newest_mtime(self):
        cache = os.path.join(self.d, "configuration", "tle-cache")
        os.makedirs(cache, exist_ok=True)
        for name, stamp in (("amateur.txt", 5000), ("weather.txt", 3000)):
            p = os.path.join(cache, name)
            with open(p, "w") as fh:
                fh.write("ISS\n1 x\n2 y\n")
            os.utime(p, (stamp, stamp))
        self.assertEqual(R.probe_tle(self.d), 5000)

    def test_probe_satnogs_uses_satellites_json(self):
        p = os.path.join(self.d, "configuration", "satellites.json")
        with open(p, "w") as fh:
            fh.write("{}")
        os.utime(p, (7000, 7000))
        self.assertEqual(R.probe_satnogs(self.d), 7000)

    def test_probe_satnogs_none_when_absent(self):
        self.assertIsNone(R.probe_satnogs(self.d))

    def test_fetch_roster_returns_false_on_script_failure(self):
        from unittest import mock
        with mock.patch.object(R.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stderr="boom")
            self.assertFalse(R.fetch_roster(self.d, {}))

    def test_fetch_roster_returns_true_on_success(self):
        from unittest import mock
        with mock.patch.object(R.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            self.assertTrue(R.fetch_roster(self.d, {}))

    def test_fetch_roster_timeout_is_a_failure_not_a_crash(self):
        from unittest import mock
        with mock.patch.object(R.subprocess, "run",
                               side_effect=R.subprocess.TimeoutExpired(
                                   "build-roster.py", 300)):
            self.assertFalse(R.fetch_roster(self.d, {}))

    def test_registry_wires_both_satellite_sources_to_one_script(self):
        # build-roster.py refreshes CelesTrak TLEs AND the SatNOGS roster in one
        # pass, so both sources share a fetch.
        self.assertIs(R._by_id("tle").fetch, R._by_id("satnogs").fetch)


class TestMetered(unittest.TestCase):
    def _nm(self, out, rc=0):
        from unittest import mock
        return mock.patch.object(
            R.subprocess, "run",
            return_value=mock.Mock(returncode=rc, stdout=out, stderr=""))

    # Fixtures below are the LITERAL output of `nmcli -t -f METERED general`,
    # captured from a Pi on 2026-08-16. The previous fixtures invented a
    # colon-prefixed format that nmcli never emits for this subcommand, which
    # is how a malformed command passed its own tests.
    def test_definitely_unmetered_is_false(self):
        with self._nm("no\n"):
            self.assertFalse(R.is_metered())

    def test_guess_no_is_unmetered(self):
        # The real answer on ordinary home Wi-Fi.
        with self._nm("no (guessed)\n"):
            self.assertFalse(R.is_metered())

    def test_definitely_metered_is_true(self):
        with self._nm("yes\n"):
            self.assertTrue(R.is_metered())

    def test_guess_yes_is_metered(self):
        with self._nm("yes (guessed)\n"):
            self.assertTrue(R.is_metered())

    def test_unknown_fails_closed(self):
        with self._nm("unknown\n"):
            self.assertTrue(R.is_metered())

    def test_the_command_asks_for_the_right_field(self):
        # `-f GENERAL.METERED general` is an invalid-field error; that field
        # name belongs to `nmcli device show`. The error made is_metered() fail
        # closed forever, so the gate never actually measured anything.
        from unittest import mock
        with mock.patch.object(R.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="no\n", stderr="")
            R.is_metered()
            argv = run.call_args[0][0]
        self.assertIn("METERED", argv)
        self.assertNotIn("GENERAL.METERED", argv)

    def test_empty_output_fails_closed(self):
        with self._nm("\n"):
            self.assertTrue(R.is_metered())

    def test_nmcli_absent_fails_closed(self):
        # No nmcli at all on macOS/Windows portable mode.
        from unittest import mock
        with mock.patch.object(R.subprocess, "run",
                               side_effect=FileNotFoundError):
            self.assertTrue(R.is_metered())

    def test_nmcli_error_fails_closed(self):
        with self._nm("", rc=1):
            self.assertTrue(R.is_metered())

    def test_nmcli_timeout_fails_closed(self):
        from unittest import mock
        with mock.patch.object(
                R.subprocess, "run",
                side_effect=R.subprocess.TimeoutExpired("nmcli", 5)):
            self.assertTrue(R.is_metered())


class TestFccAdapter(_Tmp):
    def test_refuses_when_disk_too_small(self):
        from unittest import mock
        with mock.patch.object(R, "free_bytes", return_value=1024):
            with mock.patch.object(R.subprocess, "run") as run:
                self.assertFalse(R.fetch_fcc(self.d, {}))
                run.assert_not_called()

    def test_runs_installer_when_disk_ok(self):
        from unittest import mock
        with mock.patch.object(R, "free_bytes",
                               return_value=R.FCC_REQUIRED_BYTES * 2):
            with mock.patch.object(R.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stderr="")
                self.assertTrue(R.fetch_fcc(self.d, {}))

    def test_free_bytes_walks_up_to_an_existing_parent(self):
        deep = os.path.join(self.d, "does", "not", "exist", "yet")
        self.assertGreater(R.free_bytes(deep), 0)

    def test_probe_fcc_none_when_absent(self):
        self.assertIsNone(R.probe_fcc(self.d))

    def test_probe_fcc_reads_en_dat(self):
        d = os.path.join(self.d, "services", "fcc_database", "data")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "EN.dat")
        with open(p, "w") as fh:
            fh.write("x")
        os.utime(p, (4242, 4242))
        self.assertEqual(R.probe_fcc(self.d), 4242)


class TestReportOnlySource(_Tmp):
    def _report_only(self, age):
        return R.Source(id="ro", label="ro", max_age_days=3.0, tier="small",
                        credential=None,
                        probe=lambda root: None if age is None else 0.0,
                        fetch=None, attribution="")

    def test_never_attempted_even_when_stale(self):
        out = R.run_pass(self.d, now=0.0, metered=False,
                         registry=[self._report_only(None)])
        self.assertTrue(out["ok"])
        self.assertFalse(out["sources"][0]["fetched"])

    def test_force_does_not_crash_on_a_none_fetch(self):
        # None is not callable; --force must not reach it.
        out = R.run_pass(self.d, now=0.0, metered=False, force=True,
                         registry=[self._report_only(None)])
        self.assertFalse(out["sources"][0]["fetched"])

    def test_row_is_flagged_manual_for_the_ui(self):
        out = R.run_pass(self.d, now=0.0, metered=False, dry_run=True,
                         registry=[self._report_only(None)])
        self.assertTrue(out["sources"][0]["manual"])

    def test_fetchable_sources_are_not_flagged_manual(self):
        src = R.Source(id="x", label="x", max_age_days=3.0, tier="small",
                       credential=None, probe=lambda root: 0.0,
                       fetch=lambda root, cfg: True, attribution="")
        out = R.run_pass(self.d, now=0.0, metered=False, dry_run=True,
                         registry=[src])
        self.assertFalse(out["sources"][0]["manual"])

    def test_probe_reads_the_csv_mtime(self):
        folder = os.path.join(self.d, "static", "repeaterbook")
        os.makedirs(folder, exist_ok=True)
        p = os.path.join(folder, "repeaterbook.csv")
        with open(p, "w") as fh:
            fh.write("Location,Name\n")
        os.utime(p, (8888, 8888))
        self.assertEqual(R.probe_repeaterbook(self.d), 8888)

    def test_probe_none_when_no_csv(self):
        self.assertIsNone(R.probe_repeaterbook(self.d))


if __name__ == "__main__":
    unittest.main()
