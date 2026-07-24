#!/usr/bin/env python3
"""
test_installed_services.py — self-tests for common/installed_services.py

The schema module owns installed-services.json: the `features` list the dashboard
reads and the per-feature `removal` map written by installers. Runs off-Pi.

Run directly:  python3 tests/test_installed_services.py
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

from common import installed_services as ISVC
from common import config_paths


class InstalledServicesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(self.tmp), exist_ok=True)

    def _seed(self, obj):
        with open(config_paths.installed_services_json(self.tmp), "w") as fh:
            json.dump(obj, fh)

    def test_read_missing_returns_empty(self):
        self.assertEqual(ISVC.read(self.tmp), {})

    def test_installed_features_reads_list(self):
        self._seed({"features": ["kiwix", "graywolf"]})
        self.assertEqual(ISVC.installed_features(self.tmp), {"kiwix", "graywolf"})

    def test_add_installed_unions_and_stores_records(self):
        self._seed({"features": ["kiwix"]})
        ISVC.add_installed(self.tmp, {"webssh"}, {"webssh": {"services": ["webssh"]}})
        self.assertEqual(ISVC.installed_features(self.tmp), {"kiwix", "webssh"})
        self.assertEqual(ISVC.removal_map(self.tmp)["webssh"], {"services": ["webssh"]})

    def test_add_installed_legacy_list_gains_removal_map(self):
        self._seed({"features": ["kiwix"]})  # legacy: no removal key
        ISVC.add_installed(self.tmp, {"kiwix"}, {"kiwix": {"services": ["kiwix"]}})
        self.assertIn("kiwix", ISVC.removal_map(self.tmp))

    def test_remove_installed_drops_key_and_record(self):
        self._seed({"features": ["kiwix", "webssh"],
                    "removal": {"kiwix": {"services": ["kiwix"]},
                                "webssh": {"services": ["webssh"]}}})
        ISVC.remove_installed(self.tmp, {"webssh"})
        self.assertEqual(ISVC.installed_features(self.tmp), {"kiwix"})
        self.assertNotIn("webssh", ISVC.removal_map(self.tmp))

    def test_write_is_atomic_and_sorted(self):
        ISVC.write(self.tmp, {"webssh", "kiwix"}, {})
        with open(config_paths.installed_services_json(self.tmp)) as fh:
            data = json.load(fh)
        self.assertEqual(data["features"], ["kiwix", "webssh"])  # sorted
        self.assertIn("updated", data)

    def test_add_installed_never_shrinks(self):
        self._seed({"features": ["kiwix", "graywolf"]})
        ISVC.add_installed(self.tmp, {"webssh"}, {})   # webssh added, none dropped
        self.assertEqual(ISVC.installed_features(self.tmp),
                         {"kiwix", "graywolf", "webssh"})


if __name__ == "__main__":
    unittest.main()
