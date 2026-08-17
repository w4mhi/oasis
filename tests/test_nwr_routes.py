import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import settings  # noqa: E402


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "configuration"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_defaults_on_a_fresh_box(self):
        s = settings.load(self.root)
        self.assertEqual(s["channel_hz"], settings.DEFAULTS["channel_hz"])
        self.assertEqual(s["watch_fips"], [])
        self.assertTrue(s["speak"])

    def test_save_and_reload(self):
        settings.save(self.root, {"channel_hz": 162400000,
                                  "watch_fips": ["53033"], "speak": False})
        s = settings.load(self.root)
        self.assertEqual(s["channel_hz"], 162400000)
        self.assertEqual(s["watch_fips"], ["53033"])
        self.assertFalse(s["speak"])

    def test_rejects_a_frequency_that_is_not_an_nwr_channel(self):
        with self.assertRaises(ValueError):
            settings.save(self.root, {"channel_hz": 145825000})

    def test_watch_fips_normalised_to_five_digits(self):
        settings.save(self.root, {"watch_fips": ["053033", "53053"]})
        self.assertEqual(settings.load(self.root)["watch_fips"],
                         ["53033", "53053"])

    def test_watch_fips_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            settings.save(self.root, {"watch_fips": ["not-a-fips"]})

    def test_unknown_keys_are_ignored_not_persisted(self):
        settings.save(self.root, {"channel_hz": 162400000, "evil": "yes"})
        self.assertNotIn("evil", settings.load(self.root))


if __name__ == "__main__":
    unittest.main()
