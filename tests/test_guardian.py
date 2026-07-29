import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import guardian


TH = {"temp_c": 80.0, "cpu_pct": 95.0, "mem_pct": 92.0}


class OverThresholdTest(unittest.TestCase):
    def test_none_when_all_under(self):
        self.assertIsNone(guardian.over_threshold(
            {"temp_c": 60, "cpu_pct": 30, "mem_pct": 50}, TH))

    def test_names_the_exceeded_metric(self):
        self.assertEqual(guardian.over_threshold({"temp_c": 85}, TH), "temp_c")

    def test_ignores_missing_metrics(self):
        # A metric we can't read (None/absent) must never trip the guardian.
        self.assertIsNone(guardian.over_threshold({"temp_c": None}, TH))
        self.assertIsNone(guardian.over_threshold({}, TH))


class EvaluateTest(unittest.TestCase):
    """Pure state machine: (stats, thresholds, state, now) -> (new_state, action).
    action 'fire' == caller must STOP ALL now."""

    def _idle(self):
        return {"mode": "idle", "deadline": None, "reason": None}

    def test_idle_stays_idle_when_under(self):
        s, act = guardian.evaluate({"cpu_pct": 10}, TH, self._idle(), now=1000)
        self.assertEqual(s["mode"], "idle")
        self.assertIsNone(act)

    def test_idle_arms_with_countdown_when_over(self):
        s, act = guardian.evaluate({"temp_c": 85}, TH, self._idle(), now=1000, countdown=30)
        self.assertEqual(s["mode"], "armed")
        self.assertEqual(s["deadline"], 1030)
        self.assertEqual(s["reason"], "temp_c")
        self.assertIsNone(act)   # armed, not fired yet

    def test_armed_recovers_to_idle_when_no_longer_over(self):
        armed = {"mode": "armed", "deadline": 1030, "reason": "temp_c"}
        s, act = guardian.evaluate({"temp_c": 60}, TH, armed, now=1010)
        self.assertEqual(s["mode"], "idle")   # cooled off → auto-disarm
        self.assertIsNone(act)

    def test_armed_still_counting_before_deadline(self):
        armed = {"mode": "armed", "deadline": 1030, "reason": "temp_c"}
        s, act = guardian.evaluate({"temp_c": 85}, TH, armed, now=1020)
        self.assertEqual(s["mode"], "armed")
        self.assertIsNone(act)

    def test_armed_fires_at_deadline(self):
        armed = {"mode": "armed", "deadline": 1030, "reason": "temp_c"}
        s, act = guardian.evaluate({"temp_c": 85}, TH, armed, now=1030)
        self.assertEqual(s["mode"], "tripped")
        self.assertEqual(act, "fire")

    def test_tripped_does_not_refire_while_still_over(self):
        tripped = {"mode": "tripped", "deadline": None, "reason": "temp_c"}
        s, act = guardian.evaluate({"temp_c": 85}, TH, tripped, now=1100)
        self.assertEqual(s["mode"], "tripped")
        self.assertIsNone(act)   # already stopped everything; don't loop

    def test_tripped_recovers_to_idle(self):
        tripped = {"mode": "tripped", "deadline": None, "reason": "temp_c"}
        s, act = guardian.evaluate({"temp_c": 55}, TH, tripped, now=1200)
        self.assertEqual(s["mode"], "idle")
        self.assertIsNone(act)

    def test_cancel_disarms(self):
        armed = {"mode": "armed", "deadline": 1030, "reason": "temp_c"}
        self.assertEqual(guardian.cancel(armed)["mode"], "idle")
