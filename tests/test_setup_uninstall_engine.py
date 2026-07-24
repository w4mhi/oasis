#!/usr/bin/env python3
"""
test_setup_uninstall_engine.py — self-tests for SE.resolve_uninstall().

Pure planner (no I/O): given the features to uninstall, the currently-installed
set, and the registry, produce a reverse-dependency-ordered removal list and
block any removal that would orphan a still-installed dependant.

Run directly:  python3 tests/test_setup_uninstall_engine.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

from common import setup_engine as SE


def _reg(deps):
    # deps: {key: [dependencies]} -> {key: FeatureSpec}
    return {k: SE.FeatureSpec(key=k, dependencies=v) for k, v in deps.items()}


class ResolveUninstallTest(unittest.TestCase):
    def setUp(self):
        # wikipedia depends on kiwix; graywolf standalone.
        self.reg = _reg({"server": [], "kiwix": ["server"],
                         "wikipedia": ["kiwix"], "graywolf": ["server"]})

    def test_only_installed_features_are_planned(self):
        p = SE.resolve_uninstall(["kiwix", "not-installed"], {"kiwix"}, self.reg)
        self.assertEqual(p.ordered, ["kiwix"])

    def test_blocks_when_dependant_stays_installed(self):
        # Remove kiwix while wikipedia stays installed -> blocked.
        p = SE.resolve_uninstall(["kiwix"], {"kiwix", "wikipedia"}, self.reg)
        self.assertEqual(p.ordered, [])
        self.assertEqual(len(p.blocked), 1)
        self.assertEqual(p.blocked[0]["feature"], "kiwix")
        self.assertEqual(p.blocked[0]["reason_code"], "DEPENDANT_INSTALLED")

    def test_reverse_dependency_order(self):
        # Removing both: wikipedia (dependant) before kiwix (dependency).
        p = SE.resolve_uninstall(["kiwix", "wikipedia"], {"kiwix", "wikipedia"}, self.reg)
        self.assertEqual(p.ordered, ["wikipedia", "kiwix"])
        self.assertEqual(p.blocked, [])

    def test_independent_features_unaffected(self):
        p = SE.resolve_uninstall(["graywolf"], {"graywolf", "kiwix"}, self.reg)
        self.assertEqual(p.ordered, ["graywolf"])
        self.assertEqual(p.blocked, [])


if __name__ == "__main__":
    unittest.main()
