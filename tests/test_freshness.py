import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import freshness as F


class TestAge(unittest.TestCase):
    def test_none_mtime_is_none_age(self):
        self.assertIsNone(F.age_days(None, 1000.0))

    def test_age_in_days(self):
        self.assertAlmostEqual(F.age_days(0.0, 86400.0 * 3), 3.0)

    def test_negative_age_clamps_to_zero(self):
        # A file mtime in the future (bad clock, restored backup, the
        # frozen-epoch boot problem) must not read as "fresh forever" via a
        # negative age.
        self.assertEqual(F.age_days(86400.0 * 5, 0.0), 0.0)


class TestVerdict(unittest.TestCase):
    def _v(self, **kw):
        base = dict(age=1.0, max_age_days=3.0, has_credential=True,
                    needs_credential=False, tier="small", metered=False)
        base.update(kw)
        return F.verdict(**base)

    def test_fresh(self):
        self.assertEqual(self._v(age=1.0), F.FRESH)

    def test_stale_small_tier(self):
        self.assertEqual(self._v(age=9.0), F.STALE)

    def test_exactly_at_threshold_is_stale(self):
        self.assertEqual(self._v(age=3.0), F.STALE)

    def test_missing_beats_stale(self):
        self.assertEqual(self._v(age=None), F.MISSING)

    def test_unconfigured_beats_everything(self):
        # A source needing a token it does not have is OFF, not broken and not
        # current. It must not read as a failure or as success.
        self.assertEqual(
            self._v(age=None, needs_credential=True, has_credential=False),
            F.UNCONFIGURED)
        self.assertEqual(
            self._v(age=99.0, needs_credential=True, has_credential=False),
            F.UNCONFIGURED)

    def test_credentialed_source_with_token_behaves_normally(self):
        self.assertEqual(
            self._v(age=99.0, needs_credential=True, has_credential=True),
            F.STALE)

    def test_large_stale_on_metered_is_deferred(self):
        self.assertEqual(self._v(age=99.0, tier="large", metered=True),
                         F.DEFERRED)

    def test_large_stale_unmetered_is_stale(self):
        self.assertEqual(self._v(age=99.0, tier="large", metered=False),
                         F.STALE)

    def test_large_missing_on_metered_is_deferred(self):
        self.assertEqual(self._v(age=None, tier="large", metered=True),
                         F.DEFERRED)

    def test_small_stale_on_metered_still_stale(self):
        # Kilobyte sources ignore metering entirely.
        self.assertEqual(self._v(age=99.0, tier="small", metered=True),
                         F.STALE)

    def test_fresh_large_on_metered_stays_fresh(self):
        self.assertEqual(self._v(age=1.0, tier="large", metered=True), F.FRESH)


class TestDue(unittest.TestCase):
    def test_due_states(self):
        self.assertTrue(F.is_due(F.STALE))
        self.assertTrue(F.is_due(F.MISSING))

    def test_not_due_states(self):
        # DEFERRED waits for an operator tap, not for the network.
        # UNCONFIGURED has nothing to try.
        for s in (F.FRESH, F.DEFERRED, F.UNCONFIGURED):
            self.assertFalse(F.is_due(s), s)


class TestBackoff(unittest.TestCase):
    def test_no_failures_no_backoff(self):
        self.assertEqual(F.backoff_seconds(0), 0)

    def test_negative_treated_as_none(self):
        self.assertEqual(F.backoff_seconds(-1), 0)

    def test_exponential(self):
        self.assertEqual(F.backoff_seconds(1), 1800)
        self.assertEqual(F.backoff_seconds(2), 3600)
        self.assertEqual(F.backoff_seconds(3), 7200)

    def test_capped_at_one_day(self):
        self.assertEqual(F.backoff_seconds(99), 86400)


if __name__ == "__main__":
    unittest.main()
