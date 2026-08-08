import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common import setup_registry as R


class PrivilegedAllowlistSyncTest(unittest.TestCase):
    """The root installer worker (scripts/oasis_installer_worker.py) and the web
    enqueue guard (server/routes/setup.py) both gate on the hardcoded
    PRIVILEGED_FEATURES allowlist. It is kept explicit (not inferred) for
    auditability — but it MUST stay in lockstep with the FeatureSpecs that
    actually declare privileged=True, or a legitimately-privileged feature gets
    rejected with "is not a privileged feature" (as gps-l76x did), or a
    non-privileged key silently gains a root path. This test is the backstop."""

    def test_allowlist_equals_privileged_specs(self):
        reg = R.build_registry("/tmp/oasis-test-repo")
        spec_privileged = {k for k, s in reg.items()
                           if getattr(s, "privileged", False)}
        allowlist = set(R.PRIVILEGED_FEATURES)

        missing = spec_privileged - allowlist   # privileged spec, not allowlisted -> rejected
        extra = allowlist - spec_privileged      # allowlisted, but no privileged spec -> stale
        self.assertEqual(
            missing, set(),
            f"FeatureSpec(privileged=True) keys missing from PRIVILEGED_FEATURES "
            f"(they will be rejected as 'not a privileged feature'): {sorted(missing)}")
        self.assertEqual(
            extra, set(),
            f"PRIVILEGED_FEATURES keys with no privileged FeatureSpec "
            f"(stale allowlist entries): {sorted(extra)}")

    def test_gps_l76x_is_privileged(self):
        # Regression: gps-l76x (UART GPS HAT) needs root (enables UART, writes
        # /boot config, repoints gpsd) and is installed via the privileged worker.
        self.assertIn("gps-l76x", R.PRIVILEGED_FEATURES)
        reg = R.build_registry("/tmp/oasis-test-repo")
        self.assertTrue(reg["gps-l76x"].privileged)


class DrawsFeaturesTest(unittest.TestCase):
    """The two DRAWS HAT features (P2 wiring). Both write /boot config and
    touch system services, so both go through the privileged worker."""

    def setUp(self):
        self.reg = R.build_registry("/tmp/oasis-test-repo")

    def test_both_features_are_registered(self):
        self.assertIn("draws-gps", self.reg)
        self.assertIn("draws-audio", self.reg)

    def test_both_are_privileged_and_allowlisted(self):
        for key in ("draws-gps", "draws-audio"):
            self.assertTrue(self.reg[key].privileged, key)
            self.assertIn(key, R.PRIVILEGED_FEATURES, key)

    def test_neither_auto_enables_a_service(self):
        """Config-only hardware features, like gps/gps-l76x/dra-pi — there is no
        unit to enable, and the exit-10 reboot convention drives the rest."""
        for key in ("draws-gps", "draws-audio"):
            self.assertEqual(self.reg[key].enable_policy, "none", key)

    def test_no_dependencies(self):
        """draws-audio must NOT depend on draws-gps: the shared dtoverlay=draws
        line is written idempotently by whichever installs first, so either can
        be installed alone."""
        for key in ("draws-gps", "draws-audio"):
            self.assertEqual(self.reg[key].dependencies, [], key)


if __name__ == "__main__":
    unittest.main()
