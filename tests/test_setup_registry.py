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


class SpeechFeatureTest(unittest.TestCase):
    """Station-wide speech (Piper neural TTS via common/speech.py) replaces the
    speech-dispatcher-era 'satellites-piper' feature. It is OPT-IN, not a
    requirement: every announcement still speaks through the espeak-ng
    fallback when SPEECH.available() is False.

    The contract worth pinning: it depends on `server` (its installer
    pip-installs piper-tts into the server venv and fails outright without
    one — a regression once shipped: `needs=[]` was meant to mean "not
    dependent on `satellites`", not "no dependencies at all"), but not on
    `satellites` itself (guardian and Winlink want this feature too, and
    neither installs satellites); and not privileged (a subprocess piped to a
    cached WAV needs no root, unlike the /etc/speech-dispatcher config the
    old feature wrote).
    """

    def setUp(self):
        self.reg = R.build_registry("/tmp/oasis-test-repo")

    def test_speech_depends_on_server_but_not_satellites(self):
        self.assertEqual(self.reg["speech"].dependencies, ["server"])

    def test_speech_is_not_privileged_or_allowlisted(self):
        self.assertFalse(self.reg["speech"].privileged)
        self.assertNotIn("speech", R.PRIVILEGED_FEATURES)

    def test_speech_removal_record_uninstalls_rather_than_deleting_paths(self):
        """Removal must run the script's own --uninstall, not rm a path list —
        matches the honesty contract in features/speech/install.py."""
        rec = self.reg["speech"].removal_record_fn()
        self.assertIn("script", rec)
        self.assertIn("--uninstall", rec["script"])
        self.assertTrue(rec["script"][0].endswith("features/speech/install.py"))

    def test_verify_reports_ok_on_an_unsupported_platform(self):
        """The regression this pins: an unsupported platform (32-bit ARM, no
        onnxruntime wheel) used to verify as ok:False, which setup_engine.py
        turns into STATUS_VERIFY_FAILED — a red "verify failed" shown to an
        operator right after the installer explained that nothing was
        changed, because it correctly declined rather than failed."""
        from unittest import mock
        with mock.patch("platform.machine", return_value="armv7l"):
            res = self.reg["speech"].verify_fn()
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("reason_text"))

    def test_verify_falls_through_to_available_on_a_supported_platform(self):
        from unittest import mock
        with mock.patch.object(sys, "version_info", (3, 11, 0)), \
             mock.patch("platform.machine", return_value="x86_64"), \
             mock.patch.object(R.SPEECH, "available", return_value=False):
            res = self.reg["speech"].verify_fn()
        self.assertFalse(res["ok"])
