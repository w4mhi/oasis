#!/usr/bin/env python3
"""
test_remove_oasis.py — self-tests for scripts/remove-oasis.py (manifest-driven).

remove-oasis.py now reads its plan from installed-services.json instead of a
static inventory. These tests exercise the pure plan() builder (platform-
independent): whole-suite vs single-feature, and legacy-manifest self-heal via
backfill. The config.txt surgery and dry-run safety are covered where they now
live — common/removal.py (see tests/test_removal_runner.py).

Run directly:  python3 tests/test_remove_oasis.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from common import installed_services as ISVC
from common import config_paths

# Import the hyphenated module by path.
_MOD_PATH = os.path.join(_REPO, "scripts", "remove-oasis.py")
_spec = importlib.util.spec_from_file_location("remove_oasis", _MOD_PATH)
remove_oasis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(remove_oasis)


class RemoveOasisPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(self.tmp), exist_ok=True)
        ISVC.write(self.tmp, {"webssh", "kiwix"},
                   {"webssh": {"services": ["webssh"]},
                    "kiwix": {"services": ["kiwix"], "data_paths": ["/home/pi/zim"]}})

    def test_plan_all_installed(self):
        keys = [k for k, _rec in remove_oasis.plan(self.tmp)]
        self.assertEqual(set(keys), {"webssh", "kiwix"})

    def test_plan_single_feature(self):
        items = remove_oasis.plan(self.tmp, only="webssh")
        self.assertEqual([k for k, _ in items], ["webssh"])
        self.assertEqual(items[0][1]["services"], ["webssh"])

    def test_plan_single_feature_not_installed_is_empty(self):
        self.assertEqual(remove_oasis.plan(self.tmp, only="graywolf"), [])

    def test_plan_backfills_legacy_manifest(self):
        # A legacy manifest: feature present, but no removal record stored.
        legacy = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(legacy), exist_ok=True)
        ISVC.write(legacy, {"webssh"}, {})
        items = dict(remove_oasis.plan(legacy, only="webssh"))
        # webssh's record was regenerated from the installer (self-heal).
        self.assertIn("webssh", items["webssh"].get("services", []))


if __name__ == "__main__":
    unittest.main()
