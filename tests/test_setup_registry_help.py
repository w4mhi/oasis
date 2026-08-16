"""Setup tooltips must cover every feature, and only real features.

The point of keeping help text in the registry rather than in setup.html is
that it cannot silently drift. This test is what makes that true: add a feature
without help text and the suite fails, so the Setup page can never show a
checkbox nobody can explain.
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import setup_registry as SR

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestHelpCoverage(unittest.TestCase):
    def setUp(self):
        self.reg = SR.build_registry(_REPO)

    def test_every_feature_has_help(self):
        missing = sorted(set(self.reg) - set(SR.FEATURE_HELP))
        self.assertEqual(
            missing, [],
            "features with no tooltip in SR.FEATURE_HELP:\n  "
            + "\n  ".join(missing)
            + "\n\nAdd one sentence on what it is and when to install it.")

    def test_no_help_for_features_that_do_not_exist(self):
        extra = sorted(set(SR.FEATURE_HELP) - set(self.reg))
        self.assertEqual(
            extra, [],
            "tooltips for features not in the registry (renamed or removed?):"
            "\n  " + "\n  ".join(extra))

    def test_help_is_non_empty_prose(self):
        for key, text in SR.FEATURE_HELP.items():
            self.assertTrue(text.strip(), key)
            self.assertGreater(len(text), 40,
                               f"{key}: too short to help anyone decide")

    def test_help_does_not_restate_derived_facts(self):
        # Dependencies, reboot, and privilege come from the FeatureSpec and are
        # appended by the UI. Restating them here is exactly the drift this
        # design avoids.
        for key, text in SR.FEATURE_HELP.items():
            low = text.lower()
            self.assertNotIn("requires reboot", low, key)
            self.assertNotIn("requires a reboot", low, key)
            self.assertNotIn("needs root", low, key)

    def test_help_has_no_emoji(self):
        # Pi OS Lite ships no emoji font; these render on the Setup page.
        for key, text in SR.FEATURE_HELP.items():
            bad = [c for c in text if ord(c) >= 0x1F000]
            self.assertEqual(bad, [], f"{key}: emoji would render as tofu")

    def test_help_has_no_curly_quotes(self):
        # They land in an HTML title attribute; smart quotes break attributes
        # and node --check does not catch it.
        for key, text in SR.FEATURE_HELP.items():
            bad = [c for c in text if c in "‘’“”"]
            self.assertEqual(bad, [], f"{key}: curly quote in tooltip text")


if __name__ == "__main__":
    unittest.main()
