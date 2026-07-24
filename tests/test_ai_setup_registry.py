import unittest

from common import setup_registry


class TestAiFeatureRegistered(unittest.TestCase):
    def test_ai_feature_present_and_privileged(self):
        reg = setup_registry.build_registry("/tmp/repo")
        self.assertIn("ai", reg)
        self.assertEqual(reg["ai"].key, "ai")
        self.assertIn("ai", setup_registry.PRIVILEGED_FEATURES)


if __name__ == "__main__":
    unittest.main()
