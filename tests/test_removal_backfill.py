#!/usr/bin/env python3
"""
test_removal_backfill.py — self-tests for common/removal_backfill.py.

Because removal_record() is derivable from installer constants, a box whose
manifest predates the removal map (legacy flat list) self-heals: the backfill
regenerates each installed feature's record on demand. This keeps
installed-services.json the single source of truth without forcing a reinstall.

Run directly:  python3 tests/test_removal_backfill.py
"""

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from common import installed_services as ISVC
from common import removal_backfill
from common import config_paths


class BackfillTest(unittest.TestCase):
    def setUp(self):
        # Use the real repo root so path-based removal_record refs resolve, but a
        # temp config dir so we never touch the real manifest.
        self.cfgtmp = tempfile.mkdtemp()

    def _repo_with_tmp_config(self):
        # removal_backfill/ISVC resolve the manifest via config_paths from repo_root;
        # point them at a repo whose configuration/ is our temp dir by symlinking.
        return _REPO

    def test_record_for_known_service(self):
        rec = removal_backfill.record_for(_REPO, "webssh")
        self.assertIn("webssh", rec.get("services", []))

    def test_record_for_excluded_is_none(self):
        self.assertIsNone(removal_backfill.record_for(_REPO, "server"))
        self.assertIsNone(removal_backfill.record_for(_REPO, "wikipedia"))

    def test_record_for_unknown_is_none(self):
        self.assertIsNone(removal_backfill.record_for(_REPO, "not-a-feature"))

    def test_ensure_populates_missing_records(self):
        # A legacy manifest (features list, no removal map) in an isolated repo root.
        root = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(root), exist_ok=True)
        ISVC.write(root, {"webssh"}, {})            # legacy: no record
        # webssh's record comes from a dotted import (common.webssh), so it resolves
        # regardless of root — exercising the self-heal path.
        out = removal_backfill.ensure(root, {"webssh"})
        self.assertIn("webssh", out)
        self.assertIn("webssh", ISVC.removal_map(root))

    def test_ensure_skips_already_recorded(self):
        root = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(root), exist_ok=True)
        ISVC.write(root, {"webssh"}, {"webssh": {"services": ["webssh"], "custom": True}})
        removal_backfill.ensure(root, {"webssh"})
        # Existing record preserved (not overwritten by a fresh generate).
        self.assertTrue(ISVC.removal_map(root)["webssh"].get("custom"))


if __name__ == "__main__":
    unittest.main()
