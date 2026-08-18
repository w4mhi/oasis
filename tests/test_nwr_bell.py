import datetime
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import bell  # noqa: E402

TOR = {"event": "TOR", "event_name": "Tornado Warning", "type": "tornado",
       "matched": True}
RWT = {"event": "RWT", "event_name": "Required Weekly Test", "type": None,
       "matched": True}
UNMATCHED = {"event": "TOR", "event_name": "Tornado Warning", "type": "tornado",
             "matched": False}

DAY = datetime.datetime(2026, 8, 17, 14, 0)
NIGHT = datetime.datetime(2026, 8, 17, 23, 30)


def _cfg(**kw):
    base = {"bell": True, "bell_override_until": 0}
    base.update(kw)
    return base


class BellTest(unittest.TestCase):
    def test_off_by_default(self):
        ok, why = bell.should_speak({"bell": False}, TOR, _ROOT, now=DAY)
        self.assertFalse(ok)
        self.assertIn("disabled", why)

    def test_speaks_a_matched_warning_when_enabled(self):
        ok, why = bell.should_speak(_cfg(), TOR, _ROOT, now=DAY)
        self.assertTrue(ok, why)

    def test_never_speaks_an_unmatched_alert(self):
        ok, why = bell.should_speak(_cfg(), UNMATCHED, _ROOT, now=DAY)
        self.assertFalse(ok)
        self.assertIn("watch list", why)

    def test_silent_during_quiet_hours(self):
        ok, why = bell.should_speak(_cfg(), TOR, _ROOT, now=NIGHT)
        self.assertFalse(ok)
        self.assertIn("quiet hours", why)

    def test_override_lets_it_speak_at_night(self):
        until = int(NIGHT.timestamp()) + 3600
        ok, why = bell.should_speak(_cfg(bell_override_until=until), TOR,
                                    _ROOT, now=NIGHT)
        self.assertTrue(ok, why)

    def test_an_expired_override_does_not_linger(self):
        until = int(NIGHT.timestamp()) - 1
        ok, _ = bell.should_speak(_cfg(bell_override_until=until), TOR,
                                  _ROOT, now=NIGHT)
        self.assertFalse(ok)

    def test_routine_codes_are_still_spoken_when_the_bell_is_on(self):
        # The bell IS the gate. There is no per-code severity filter in v2 --
        # turning the bell on means you want to hear the weekly test too, which
        # is the only regular proof the whole path still works.
        ok, why = bell.should_speak(_cfg(), RWT, _ROOT, now=DAY)
        self.assertTrue(ok, why)


class OverrideUntilTest(unittest.TestCase):
    def test_expires_at_the_next_seven(self):
        # 23:30 -> 07:00 tomorrow
        got = bell.override_until(NIGHT, _ROOT)
        self.assertEqual(datetime.datetime.fromtimestamp(got).hour, 7)
        self.assertGreater(got, NIGHT.timestamp())

    def test_from_the_morning_it_is_the_same_day(self):
        early = datetime.datetime(2026, 8, 17, 2, 0)
        got = datetime.datetime.fromtimestamp(bell.override_until(early, _ROOT))
        self.assertEqual((got.day, got.hour), (17, 7))


if __name__ == "__main__":
    unittest.main()
