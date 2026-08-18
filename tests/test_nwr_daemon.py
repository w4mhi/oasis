import io
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

# The sweep pi5draws took with dongle 00000031 against a live NWS transmitter,
# through scan.run(). WX7 is the transmitter; the other six are the band's own
# noise, and they sit at -5.7 dBm, not at anything like a textbook floor.
MEASURED_SWEEP = {162400000: -5.71, 162425000: -5.56, 162450000: -5.64,
                  162475000: -5.75, 162500000: -5.72, 162525000: -5.68,
                  162550000: -1.95}


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

    def test_the_measured_live_sweep_is_not_weak(self):
        # The sweep pi5draws actually took against a live NWS transmitter. Its
        # EMPTY channels read -5.7 dBm, so the absolute floor this replaced
        # (-50 dBm) could never fire and the amber state was unreachable. See
        # scan.channel_margin().
        def fake_scan(**kw):
            return {"ok": True, "powers": MEASURED_SWEEP,
                    "best_hz": 162550000, "best_dbm": -1.95, "error": None}
        hz, result = daemon.choose_channel(_ROOT, {}, "0001", scan_fn=fake_scan)
        self.assertEqual(hz, 162550000)
        self.assertFalse(result["weak"])
        self.assertAlmostEqual(result["margin_db"], 3.75, places=2)

    def test_a_weak_best_still_starts(self):
        # Refusing to start would leave nothing running, and silence that means
        # "no transmitter" would look identical to silence that means "broken".
        # A flat band -- no antenna -- is what weak now means.
        flat = {hz: -5.7 for hz in MEASURED_SWEEP}

        def fake_scan(**kw):
            return {"ok": True, "powers": flat,
                    "best_hz": 162400000, "best_dbm": -5.7, "error": None}
        hz, result = daemon.choose_channel(_ROOT, {}, "0001", scan_fn=fake_scan)
        self.assertEqual(hz, 162400000)
        self.assertTrue(result["weak"])
        self.assertEqual(result["margin_db"], 0.0)

    def test_a_sweep_that_cannot_be_read_is_reported_not_guessed(self):
        # One channel read, a best that is not in `powers`, no powers at all:
        # each is "did not measure", which is not "no signal". None of them may
        # raise into supervise(), and none may claim the band is weak.
        for powers, best in (({162550000: -1.95}, 162550000),
                             ({}, 162550000),
                             (None, 162550000),
                             (MEASURED_SWEEP, 162475001)):
            with self.subTest(powers=powers, best=best):
                def fake_scan(**kw):
                    return {"ok": True, "powers": powers, "best_hz": best,
                            "best_dbm": -1.95, "error": None}
                hz, result = daemon.choose_channel(_ROOT, {}, "0001",
                                                   scan_fn=fake_scan)
                self.assertEqual(hz, best)
                self.assertFalse(result["weak"])
                self.assertIsNone(result["margin_db"])

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

    def test_with_no_sweep_behind_it_the_configured_channel_applies(self):
        self.assertEqual(daemon.fallback_channel({"pinned_channel": 162400000}),
                         162400000)
        self.assertEqual(daemon.fallback_channel({"channel_hz": 162475000}),
                         162475000)
        self.assertEqual(daemon.fallback_channel({}),
                         daemon.settings.DEFAULTS["channel_hz"])
        self.assertEqual(daemon.retry_channel({"channel_hz": 162475000}, None),
                         162475000)

    def test_a_skipped_sweep_reuses_the_channel_the_sweep_chose(self):
        self.assertEqual(
            daemon.retry_channel({"channel_hz": 162550000}, 162450000), 162450000)

    def test_a_pin_still_wins_over_the_last_sweep(self):
        # A pin can be set WHILE the watch is retrying; this branch is the
        # only place that would ever see it.
        self.assertEqual(
            daemon.retry_channel({"pinned_channel": 162400000}, 162450000),
            162400000)


class _FakeStop:
    """A stop_event whose wait() advances a simulated clock instead of
    spending the wall clock, and which sets itself once `window` seconds of
    simulated time have gone by.

    The clock ONLY moves in wait(). A supervisor that backed off with
    time.sleep() instead would therefore never reach the end of the window --
    it would spin forever and WEDGE the suite rather than fail it, taking the
    other 2400-odd tests with it. So is_set(), which every pass calls, counts
    passes and fails the test itself once they can no longer be explained by
    the window. That turns the wedge into a red test with a diagnosis.
    """

    EPOCH = 1_700_000_000.0

    def __init__(self, window):
        self.now = self.EPOCH
        self.window = window
        self.waits = []
        self.passes = 0
        # An honest pass waits at least one tick, so a `window`-second window
        # cannot take more than window/tick passes -- 720 for an hour at the
        # 5 s tick, and far fewer once the back-off curve applies. Scaled to
        # the window rather than a flat number so a longer one stays valid.
        self.max_passes = int(window) + 1000

    def is_set(self):
        self.passes += 1
        if self.passes > self.max_passes:
            raise AssertionError(
                "supervise() made {} passes without the simulated clock "
                "advancing: the loop is not waiting on stop_event".format(
                    self.passes))
        return (self.now - self.EPOCH) >= self.window

    def wait(self, delay):
        # Deliberately not is_set(): only the while-condition counts passes,
        # so the guard can never raise from inside supervise()'s own
        # try/except, which would swallow it.
        self.waits.append(delay)
        self.now += max(float(delay), 0.001)
        return (self.now - self.EPOCH) >= self.window


class SuperviseRetryCadenceTest(unittest.TestCase):
    """The hot-spin found by a live smoke test on 192.168.1.46: with all
    dongles assigned elsewhere, supervise() respawned rtl_power + rtl_fm +
    multimon-ng every ~4.8 s indefinitely -- 9 attempts in 43 s, roughly
    17,000 journal lines a day, on a box where the condition lasts for days.

    Nothing asserted on retry CADENCE, which is precisely why ten passing
    unit tests missed it.
    """

    HELD = "another service is already using the RTL-SDR dongle"
    # A dongle IS assigned to nwr here -- _device_serial() returns "0001",
    # not None -- and it is simply held by someone else. That is a distinct
    # condition from "nothing assigned to nwr", which NoDongleAssignedTest
    # below covers and which must never reach choose_channel()/listener.start()
    # at all.
    # WX3, and NOT settings.DEFAULTS["channel_hz"] (WX7). A fixture that
    # sweeps to the default cannot tell "reused the sweep's answer" apart from
    # "fell back to the configured channel" -- which is exactly how the retry
    # path came to throw the scan's answer away unnoticed.
    SWEPT_HZ = 162450000

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
            return self.SWEPT_HZ, {"ok": True, "best_hz": self.SWEPT_HZ,
                                   "best_dbm": -20.0, "weak": False}

        with mock.patch.object(daemon.settings, "load",
                               return_value=dict(daemon.settings.DEFAULTS)), \
             mock.patch.object(daemon, "_device_serial", return_value="0001"), \
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

    def test_a_skipped_sweep_keeps_the_channel_the_sweep_chose(self):
        # The sweep picks WX3 and the dongle is then lost -- to a manual
        # Listen, or an ADS-B restart. Retrying on the CONFIGURED channel
        # instead would land the watch on WX7 when the dongle comes back, and
        # a healthy last scan leaves next_rescan at 0, so nothing would ever
        # re-derive it: LISTENING, nothing decoded, no transmitter there.
        _stop, attempts, _sweeps = self._run(
            600, {"ok": False, "error": self.HELD})
        self.assertGreater(len(attempts), 1)
        self.assertEqual(set(attempts), {self.SWEPT_HZ},
                         "every retry must aim at the channel the sweep chose")
        self.assertNotEqual(self.SWEPT_HZ, daemon.settings.DEFAULTS["channel_hz"])

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

    def test_a_weak_sweep_starts_the_watch_and_arms_the_rescan(self):
        # The whole point of measuring the margin: a flat band -- an antenna
        # that fell off -- must still put a capture on the air AND arm the
        # back-off that will pick the antenna up again when it is refitted.
        # With the old absolute floor neither half of this could ever happen,
        # because `weak` was unreachable.
        flat = {hz: -5.7 for hz in MEASURED_SWEEP}
        stop = _FakeStop(30)
        attempts = []

        def fake_scan(**kw):
            return {"ok": True, "powers": flat, "best_hz": 162400000,
                    "best_dbm": -5.7, "error": None}

        def fake_start(hz, **kw):
            attempts.append(hz)
            return {"ok": True, "error": None}

        with mock.patch.object(daemon.settings, "load",
                               return_value=dict(daemon.settings.DEFAULTS)), \
             mock.patch.object(daemon, "_device_serial", return_value="0001"), \
             mock.patch.object(daemon.listener, "is_listening",
                               return_value=False), \
             mock.patch.object(daemon.scan, "run", side_effect=fake_scan), \
             mock.patch.object(daemon.listener, "start", side_effect=fake_start):
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)

        self.assertEqual(set(attempts), {162400000},
                         "a weak band must still put a capture on the air")
        with daemon._lock:
            self.assertTrue(daemon._state["scan_weak"])
            self.assertEqual(daemon._state["phase"], "listening")
            weak_count = daemon._state["consecutive_weak"]
            self.assertGreater(weak_count, 0)
            self.assertGreaterEqual(daemon.rescan_delay(weak_count),
                                    daemon.RESCAN_START_S)
            self.assertGreater(daemon._state["next_rescan"], _FakeStop.EPOCH,
                               "a weak scan must leave a rescan due")

    def test_a_broken_settings_file_does_not_end_the_watch(self):
        # A hand-edited "watch_fips": 12345 makes settings.load() do
        # list(12345) -> TypeError. That used to kill this thread outright:
        # the HTTP server kept answering /status, phase froze at its last
        # value, and the only evidence was one threading.excepthook traceback.
        stop = _FakeStop(600)
        loads = []
        attempts = []

        def flaky_load(repo_root):
            loads.append(1)
            if len(loads) <= 3:
                raise TypeError("'int' object is not iterable")
            return dict(daemon.settings.DEFAULTS)

        def fake_choose(repo_root, cfg, serial, **kw):
            return self.SWEPT_HZ, {"ok": True, "best_hz": self.SWEPT_HZ,
                                   "best_dbm": -20.0, "weak": False}

        def fake_start(hz, **kw):
            attempts.append(hz)
            return {"ok": False, "error": self.HELD}

        with mock.patch.object(daemon.settings, "load", side_effect=flaky_load), \
             mock.patch.object(daemon, "_device_serial", return_value="0001"), \
             mock.patch.object(daemon.listener, "is_listening", return_value=False), \
             mock.patch.object(daemon, "choose_channel", side_effect=fake_choose), \
             mock.patch.object(daemon.listener, "start", side_effect=fake_start), \
             self.assertLogs(daemon.log.name, level="ERROR") as caught:
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)

        self.assertEqual(len(caught.records), 3, "each failure must be logged")
        self.assertTrue(attempts,
                        "the watch never recovered once the file was readable")

    def test_a_loop_failure_says_so_instead_of_freezing(self):
        stop = _FakeStop(4)
        with mock.patch.object(daemon.settings, "load",
                               side_effect=ValueError("not a channel: '162.550'")), \
             mock.patch.object(daemon.listener, "is_listening", return_value=False), \
             self.assertLogs(daemon.log.name, level="ERROR"):
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)
        with daemon._lock:
            self.assertEqual(daemon._state["phase"], "retrying")
            self.assertIn("162.550", daemon._state["last_error"])
            self.assertGreater(daemon._state["next_retry"], 0)


class NoDongleAssignedTest(unittest.TestCase):
    """listener.rtl_command() falls back to device index 0 when device_serial
    is falsy -- on a multi-dongle Pi that is very often another service's
    radio. Nothing assigned to nwr must never reach a sweep (rtl_power) or a
    listen (rtl_fm); is_claimed() only guards a dongle that is BUSY, not one
    that was never handed to us."""

    def setUp(self):
        before = dict(daemon._state)
        self.addCleanup(lambda: daemon._state.update(before))
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_no_sweep_and_no_listen_are_attempted(self):
        stop = _FakeStop(600)
        with mock.patch.object(daemon.settings, "load",
                               return_value=dict(daemon.settings.DEFAULTS)), \
             mock.patch.object(daemon, "_device_serial", return_value=None), \
             mock.patch.object(daemon.listener, "is_listening", return_value=False), \
             mock.patch.object(daemon, "choose_channel") as choose, \
             mock.patch.object(daemon.listener, "start") as start:
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)
        choose.assert_not_called()
        start.assert_not_called()

    def test_status_reports_the_reason(self):
        stop = _FakeStop(60)
        with mock.patch.object(daemon.settings, "load",
                               return_value=dict(daemon.settings.DEFAULTS)), \
             mock.patch.object(daemon, "_device_serial", return_value=None), \
             mock.patch.object(daemon.listener, "is_listening", return_value=False):
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)
        with daemon._lock:
            self.assertEqual(daemon._state["phase"], "retrying")
            self.assertEqual(daemon._state["last_error"], "no dongle assigned")
            self.assertIsNone(daemon._state["channel_hz"])
            self.assertGreater(daemon._state["retry_failures"], 0)
            self.assertGreater(daemon._state["next_retry"], 0)

    def test_it_backs_off_on_the_same_curve_as_a_failed_start(self):
        stop = _FakeStop(3600)
        with mock.patch.object(daemon.settings, "load",
                               return_value=dict(daemon.settings.DEFAULTS)), \
             mock.patch.object(daemon, "_device_serial", return_value=None), \
             mock.patch.object(daemon.listener, "is_listening", return_value=False):
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)
        self.assertEqual(max(stop.waits), daemon.RETRY_MAX_S)

    def test_sweep_is_pointless_matches_the_literal_reason(self):
        # "no dongle assigned" is supervise()'s own last_error text, back in
        # DONGLE_UNAVAILABLE so a retry that follows one does not re-sweep.
        self.assertTrue(daemon.sweep_is_pointless("no dongle assigned"))

    def test_assignment_recovers_without_a_restart(self):
        # A dongle handed to nwr mid-retry must be picked up on the very next
        # pass -- _device_serial() is read fresh every time, per its own
        # docstring.
        stop = _FakeStop(30)
        serials = iter([None, None, "0001"])
        attempts = []

        def fake_choose(repo_root, cfg, serial, **kw):
            return 162400000, {"ok": True, "best_hz": 162400000,
                               "best_dbm": -20.0, "weak": False}

        def fake_start(hz, **kw):
            attempts.append(hz)
            return {"ok": True, "error": None}

        with mock.patch.object(daemon.settings, "load",
                               return_value=dict(daemon.settings.DEFAULTS)), \
             mock.patch.object(daemon, "_device_serial",
                               side_effect=lambda repo_root: next(serials, "0001")), \
             mock.patch.object(daemon.listener, "is_listening", return_value=False), \
             mock.patch.object(daemon, "choose_channel", side_effect=fake_choose), \
             mock.patch.object(daemon.listener, "start", side_effect=fake_start):
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)
        self.assertTrue(attempts, "the watch never started once a dongle arrived")


class _FakeRadio:
    """listener.start/stop/is_listening backed by a flag instead of a dongle.

    stop() is synchronous here for the same reason it is in the real listener:
    it does not return until the capture is gone, so the supervisor's next pass
    cannot race a still-dying one.
    """

    def __init__(self, results=None, on_start=None):
        self.listening = False
        self.starts = []
        self.stops = 0
        self.results = list(results or [])
        self.on_start = on_start

    def start(self, hz, **kw):
        if self.on_start:
            self.on_start(len(self.starts))
        self.starts.append(hz)
        res = self.results.pop(0) if self.results else {"ok": True, "error": None}
        self.listening = bool(res.get("ok"))
        return dict(res)

    def stop(self):
        self.stops += 1
        self.listening = False
        return {"ok": True}

    def is_listening(self):
        return self.listening


class _StopWithHook(_FakeStop):
    """_FakeStop that runs `fn` once, after the Nth pass has finished waiting.

    A retune request has to arrive at a KNOWN point in the cycle -- after a
    capture is fully recorded in _state, not while listener.start() is still
    inside it -- or the test proves something other than what it claims.
    """

    def __init__(self, window, after_waits, fn):
        super().__init__(window)
        self.after_waits = after_waits
        self.fn = fn

    def wait(self, delay):
        done = super().wait(delay)
        if len(self.waits) == self.after_waits:
            self.fn()
        return done


class RetunePlanTest(unittest.TestCase):
    """The decision, without a radio in the room."""

    def test_a_pin_that_differs_from_what_is_tuned_retunes(self):
        go, detail = daemon.retune_plan({"pinned_channel": 162400000},
                                        162450000, None)
        self.assertTrue(go)
        self.assertIn("WX1", detail)

    def test_re_selecting_the_channel_already_tuned_is_a_no_op(self):
        # An operator re-picking the current channel is a normal click, and a
        # gap in the watch bought to land on the frequency we are already on
        # is a gap bought for nothing.
        go, detail = daemon.retune_plan({"pinned_channel": 162450000},
                                        162450000, 162450000)
        self.assertFalse(go)
        self.assertIn("already", detail)

    def test_clearing_a_pin_that_was_in_force_rescans(self):
        go, _detail = daemon.retune_plan({"pinned_channel": None},
                                         162400000, 162400000)
        self.assertTrue(go)

    def test_clearing_a_pin_that_never_applied_changes_nothing(self):
        # The sweep chose this channel, so "Auto" is already what is happening
        # and re-deriving the same answer costs six seconds of rtl_power.
        go, _detail = daemon.retune_plan({}, 162450000, None)
        self.assertFalse(go)

    def test_a_pin_with_nothing_tuned_still_asks(self):
        go, _detail = daemon.retune_plan({"pinned_channel": 162400000},
                                         None, None)
        self.assertTrue(go)


class RequestRetuneTest(unittest.TestCase):
    def setUp(self):
        before = dict(daemon._state)
        self.addCleanup(lambda: daemon._state.update(before))
        with daemon._lock:
            daemon._state.update({"retune_seq": 0, "retune_ack": 0,
                                  "channel_hz": 162450000, "pinned_hz": None})

    def _ask(self, cfg):
        with mock.patch.object(daemon.settings, "load", return_value=cfg):
            return daemon.request_retune(_ROOT)

    def test_a_changed_pin_is_accepted_and_left_pending(self):
        out = self._ask({"pinned_channel": 162400000})
        self.assertTrue(out["ok"])
        self.assertTrue(out["retuning"])
        self.assertTrue(out["pending"])
        self.assertEqual(daemon._state["retune_seq"], 1)
        self.assertTrue(daemon.status()["retune_pending"])

    def test_an_unchanged_pin_moves_nothing(self):
        out = self._ask({"pinned_channel": 162450000})
        self.assertTrue(out["ok"], "the request succeeded; the news is 'nothing to do'")
        self.assertFalse(out["retuning"])
        self.assertEqual(daemon._state["retune_seq"], 0)
        self.assertFalse(daemon.status()["retune_pending"])

    def test_two_clicks_do_not_lose_the_second(self):
        # The reason the request is a sequence and not a flag.
        self._ask({"pinned_channel": 162400000})
        self._ask({"pinned_channel": 162475000})
        self.assertEqual(daemon._state["retune_seq"], 2)

    def test_it_touches_no_radio(self):
        with mock.patch.object(daemon.listener, "start",
                               side_effect=AssertionError("must not start a capture")), \
             mock.patch.object(daemon.listener, "stop",
                               side_effect=AssertionError("must not stop a capture")):
            self._ask({"pinned_channel": 162400000})


class RetuneSuperviseTest(unittest.TestCase):
    """The supervisor's half: an accepted request has to become a real gap and
    then a real capture on the new channel, and must never be able to leave the
    watch sitting idle."""

    SWEPT_HZ = 162450000        # WX3, what the fake sweep picks
    PINNED_HZ = 162400000       # WX1, what the operator pins

    def setUp(self):
        before = dict(daemon._state)
        self.addCleanup(lambda: daemon._state.update(before))
        with daemon._lock:
            daemon._state.update({"retune_seq": 0, "retune_ack": 0,
                                  "channel_hz": None, "pinned_hz": None,
                                  "next_rescan": 0, "consecutive_weak": 0})
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _run(self, cfg, hook, window=60, after_waits=1, results=None,
             on_start=None):
        """Drive the real supervise() against a fake radio and a fake sweep.

        choose_channel() is NOT stubbed: whether a pinned channel skips the
        band sweep is part of what a retune has to get right.
        """
        radio = _FakeRadio(results, on_start=on_start)
        sweeps = []
        phases = []

        def fake_scan(**kw):
            sweeps.append(1)
            return {"ok": True, "powers": {self.SWEPT_HZ: -20.0},
                    "best_hz": self.SWEPT_HZ, "best_dbm": -20.0, "error": None}

        stop = _StopWithHook(window, after_waits, lambda: hook(radio))
        real_wait = stop.wait

        def wait_and_record(delay):
            phases.append(daemon._state["phase"])
            return real_wait(delay)

        stop.wait = wait_and_record

        with mock.patch.object(daemon.settings, "load", side_effect=lambda root: dict(cfg)), \
             mock.patch.object(daemon, "_device_serial", return_value="0001"), \
             mock.patch.object(daemon.scan, "run", side_effect=fake_scan), \
             mock.patch.object(daemon.listener, "is_listening",
                               side_effect=radio.is_listening), \
             mock.patch.object(daemon.listener, "start", side_effect=radio.start), \
             mock.patch.object(daemon.listener, "stop", side_effect=radio.stop):
            daemon.supervise(_ROOT, stop_event=stop, now=lambda: stop.now)
        return radio, sweeps, phases

    def test_a_pin_set_while_listening_actually_retunes(self):
        cfg = dict(daemon.settings.DEFAULTS)

        def pin(_radio):
            cfg["pinned_channel"] = self.PINNED_HZ
            with mock.patch.object(daemon.settings, "load", return_value=dict(cfg)):
                daemon.request_retune(_ROOT)

        radio, sweeps, phases = self._run(cfg, pin, window=40)
        self.assertEqual(radio.starts, [self.SWEPT_HZ, self.PINNED_HZ],
                         "the watch never moved off the channel the sweep chose")
        self.assertEqual(radio.stops, 1, "exactly one gap, and only for the retune")
        self.assertEqual(len(sweeps), 1, "a pinned channel must not sweep the band")
        self.assertEqual(daemon._state["channel_hz"], self.PINNED_HZ)
        self.assertFalse(daemon.status()["retune_pending"],
                         "the request stays pending until the new capture runs")
        self.assertIn("retuning", phases,
                      "the gap must be visible as a retune, not as a dead decoder")

    def test_re_selecting_the_tuned_channel_interrupts_nothing(self):
        cfg = dict(daemon.settings.DEFAULTS)

        def pin_the_same(_radio):
            cfg["pinned_channel"] = self.SWEPT_HZ
            with mock.patch.object(daemon.settings, "load", return_value=dict(cfg)):
                out = daemon.request_retune(_ROOT)
            self.assertFalse(out["retuning"])

        radio, _sweeps, phases = self._run(cfg, pin_the_same, window=40)
        self.assertEqual(radio.starts, [self.SWEPT_HZ])
        self.assertEqual(radio.stops, 0, "a healthy capture was interrupted for nothing")
        self.assertNotIn("retuning", phases)

    def test_choosing_auto_gives_the_channel_back_to_the_scan(self):
        cfg = dict(daemon.settings.DEFAULTS, pinned_channel=self.PINNED_HZ)

        def unpin(_radio):
            cfg["pinned_channel"] = None
            with mock.patch.object(daemon.settings, "load", return_value=dict(cfg)):
                daemon.request_retune(_ROOT)

        radio, sweeps, _phases = self._run(cfg, unpin, window=40)
        self.assertEqual(radio.starts, [self.PINNED_HZ, self.SWEPT_HZ])
        self.assertEqual(len(sweeps), 1, "clearing the pin is what earns the sweep")

    def test_a_retune_whose_capture_fails_still_converges_on_a_running_watch(self):
        # The failure mode that matters: an interruption we asked for must
        # never be able to leave the watch idle. Two failed starts back off on
        # the ordinary retry curve and the third succeeds.
        cfg = dict(daemon.settings.DEFAULTS)
        results = [{"ok": True, "error": None},
                   {"ok": False, "error": "rtl_fm exited immediately"},
                   {"ok": False, "error": "rtl_fm exited immediately"},
                   {"ok": True, "error": None}]

        def pin(_radio):
            cfg["pinned_channel"] = self.PINNED_HZ
            with mock.patch.object(daemon.settings, "load", return_value=dict(cfg)):
                daemon.request_retune(_ROOT)

        radio, _sweeps, _phases = self._run(cfg, pin, window=60, results=results)
        self.assertTrue(radio.listening, "the watch was left with no capture")
        self.assertEqual(radio.starts[1:], [self.PINNED_HZ] * 3,
                         "every retry must keep aiming at the pinned channel")
        self.assertEqual(radio.stops, 1, "a failed start must not cost another gap")
        self.assertEqual(daemon._state["phase"], "listening")
        self.assertFalse(daemon.status()["retune_pending"])

    def test_a_request_that_lands_mid_start_is_not_swallowed(self):
        # The race the sequence counter exists for: the pass has already read
        # its configuration and is inside listener.start(), so the pin it is
        # about to record is the OLD one. A flag cleared on consumption would
        # ack a request the running capture does not satisfy, and the pin would
        # sit stored and inert -- the exact defect this whole path fixes.
        cfg = dict(daemon.settings.DEFAULTS)

        def pin_mid_start(n):
            if n:
                return
            cfg["pinned_channel"] = self.PINNED_HZ
            with mock.patch.object(daemon.settings, "load", return_value=dict(cfg)):
                daemon.request_retune(_ROOT)

        radio, _sweeps, _phases = self._run(cfg, lambda _r: None, window=40,
                                            on_start=pin_mid_start)
        self.assertEqual(radio.starts, [self.SWEPT_HZ, self.PINNED_HZ],
                         "a request that raced the start was lost")
        self.assertEqual(radio.stops, 1)
        self.assertFalse(daemon.status()["retune_pending"])

    def test_a_request_that_lands_while_nothing_is_running_costs_no_gap(self):
        # Mid-retry: there is no capture to interrupt, and the next pass reads
        # the file anyway.
        cfg = dict(daemon.settings.DEFAULTS)
        results = [{"ok": False, "error": "rtl_fm exited immediately"}] * 3

        def pin(_radio):
            cfg["pinned_channel"] = self.PINNED_HZ
            with mock.patch.object(daemon.settings, "load", return_value=dict(cfg)):
                daemon.request_retune(_ROOT)

        radio, _sweeps, _phases = self._run(cfg, pin, window=40, results=results)
        self.assertEqual(radio.stops, 0)
        self.assertEqual(set(radio.starts[1:]), {self.PINNED_HZ})


class _PostHandler(daemon._Handler):
    """_Handler with the socket machinery stubbed out, for do_POST only.

    Same shape as _RecordingHandler below: BaseHTTPRequestHandler.__init__
    parses a request off a real socket and then serves it, which is not what is
    under test here.
    """

    def __init__(self, path, body=b""):
        self.path = path
        self.rfile = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}
        self.close_connection = False
        self.code = None
        self.json_out = None

    def _json(self, code, payload):
        self.code = code
        self.json_out = payload


class RetuneHandlerTest(unittest.TestCase):
    """The daemon's side of the boundary: a caller may ask, never command."""

    def setUp(self):
        before = dict(daemon._state)
        self.addCleanup(lambda: daemon._state.update(before))
        with daemon._lock:
            daemon._state.update({"retune_seq": 0, "retune_ack": 0,
                                  "channel_hz": 162450000, "pinned_hz": None})
        self._root_patch = mock.patch.object(daemon, "REPO_ROOT", _ROOT)
        self._root_patch.start()
        self.addCleanup(self._root_patch.stop)

    def _post(self, path, body=b"", cfg=None):
        cfg = cfg if cfg is not None else {"pinned_channel": 162400000}
        h = _PostHandler(path, body)
        with mock.patch.object(daemon.settings, "load", return_value=cfg), \
             mock.patch.object(daemon.listener, "start",
                               side_effect=AssertionError("the handler must not start a capture")), \
             mock.patch.object(daemon.listener, "stop",
                               side_effect=AssertionError("the handler must not stop a capture")):
            h.do_POST()
        return h

    def test_a_retune_is_accepted_and_answered(self):
        h = self._post("/retune")
        self.assertEqual(h.code, 200)
        self.assertTrue(h.json_out["ok"])
        self.assertTrue(h.json_out["retuning"])
        self.assertEqual(daemon._state["retune_seq"], 1)

    def test_the_request_body_is_read_before_the_answer_is_written(self):
        # HTTP/1.1 keep-alive frames the next request right after this body;
        # bytes left in the socket are parsed as a request line.
        h = self._post("/retune", body=b'{"ignored": true}')
        self.assertEqual(h.rfile.read(), b"", "the request body was left in the socket")
        self.assertEqual(h.code, 200)

    def test_an_unknown_post_path_is_a_conformant_404(self):
        h = self._post("/listen")
        self.assertEqual(h.code, 404)
        self.assertFalse(h.json_out["ok"])
        self.assertEqual(daemon._state["retune_seq"], 0)

    def test_a_configuration_it_cannot_read_answers_instead_of_hanging_up(self):
        h = _PostHandler("/retune")
        with mock.patch.object(daemon.settings, "load",
                               side_effect=TypeError("'int' object is not iterable")), \
             self.assertLogs(daemon.log.name, level="ERROR"):
            h.do_POST()
        self.assertEqual(h.code, 500)
        self.assertFalse(h.json_out["ok"])
        self.assertEqual(h.json_out["code"], "NWR_RETUNE_FAILED")

    def test_without_a_station_root_it_says_so(self):
        self._root_patch.stop()
        h = _PostHandler("/retune")
        with mock.patch.object(daemon, "REPO_ROOT", None):
            h.do_POST()
        self._root_patch.start()
        self.assertEqual(h.code, 503)
        self.assertFalse(h.json_out["ok"])


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


class StreamGainTest(unittest.TestCase):
    """The relay measured 9 dB below OASIS speech on pi5draws because this
    encode chain had no gain stage. The numbers are in
    common/sdr_rx.stream_encoder(); what belongs here is that the daemon's
    stream is the thing that asks for it."""

    def test_the_stream_asks_for_the_measured_gain(self):
        seen = {}

        def fake_encoder(srate, **kw):
            seen["srate"] = srate
            seen.update(kw)
            return (None, None)          # 503, so no encoder is ever spawned

        with mock.patch.object(daemon.listener, "is_listening", return_value=True), \
             mock.patch("common.sdr_rx.stream_encoder", side_effect=fake_encoder):
            handler = _RecordingHandler(_FakeWfile(1 << 20))
            handler._stream()
        self.assertEqual(handler.code, 503)
        self.assertEqual(seen.get("gain_db"), daemon.listener.STREAM_GAIN_DB)
        self.assertEqual(seen.get("srate"), daemon.listener.SAMPLE_RATE)

    def test_the_command_it_gets_back_carries_the_filter(self):
        from common import sdr_rx
        cmd, _ = sdr_rx.stream_encoder(daemon.listener.SAMPLE_RATE,
                                       which=lambda b: "/usr/bin/" + b,
                                       gain_db=daemon.listener.STREAM_GAIN_DB)
        self.assertIn('-af "volume=8dB,alimiter=limit=0.9"', cmd)


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

    def test_the_countdown_reads_the_same_clock_that_wrote_it(self):
        # supervise() writes next_retry with its injected `now`; a status()
        # hardwired to time.time() would disagree with it under any clock but
        # the wall one.
        before = dict(daemon._state)
        self.addCleanup(lambda: daemon._state.update(before))
        with daemon._lock:
            daemon._state["next_retry"] = 5000.0
        self.assertEqual(daemon.status(now=lambda: 4700.0)["retry_in_s"], 300)
        self.assertEqual(daemon.status(now=lambda: 9000.0)["retry_in_s"], 0)


if __name__ == "__main__":
    unittest.main()
