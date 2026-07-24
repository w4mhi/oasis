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


def _reg(deps, nonremovable=("server", "wikipedia"), priorities=None):
    # deps: {key: [dependencies]} -> {key: FeatureSpec}
    # Features are removable by default (a dummy removal_record_fn); keys named in
    # `nonremovable` get None, mirroring real specs excluded from teardown
    # (server, and wikipedia — whose ZIM is kept data, not a service).
    priorities = priorities or {}
    def _spec(k, v):
        rr = None if k in nonremovable else (lambda: {"services": []})
        return SE.FeatureSpec(key=k, dependencies=v, removal_record_fn=rr,
                              teardown_priority=priorities.get(k, 0))
    return {k: _spec(k, v) for k, v in deps.items()}


class ResolveUninstallTest(unittest.TestCase):
    def setUp(self):
        # wikipedia (non-removable) depends on kiwix; graywolf-api (removable)
        # depends on graywolf.
        self.reg = _reg({"server": [], "kiwix": ["server"],
                         "wikipedia": ["kiwix"], "graywolf": ["server"],
                         "graywolf-api": ["graywolf"]})

    def test_only_installed_features_are_planned(self):
        p = SE.resolve_uninstall(["kiwix", "not-installed"], {"kiwix"}, self.reg)
        self.assertEqual(p.ordered, ["kiwix"])

    def test_removable_dependant_blocks(self):
        # Remove graywolf while its removable dependant graywolf-api stays -> blocked.
        p = SE.resolve_uninstall(["graywolf"], {"graywolf", "graywolf-api"}, self.reg)
        self.assertEqual(p.ordered, [])
        self.assertEqual(len(p.blocked), 1)
        self.assertEqual(p.blocked[0]["feature"], "graywolf")
        self.assertEqual(p.blocked[0]["reason_code"], "DEPENDANT_INSTALLED")

    def test_nonremovable_dependant_does_not_block(self):
        # wikipedia is kept ZIM data (non-removable), so it must NOT wedge kiwix
        # removal — removing kiwix just leaves the ZIM as inert data on disk.
        p = SE.resolve_uninstall(["kiwix"], {"kiwix", "wikipedia"}, self.reg)
        self.assertEqual(p.ordered, ["kiwix"])
        self.assertEqual(p.blocked, [])

    def test_reverse_dependency_order(self):
        # Removing both: graywolf-api (dependant) before graywolf (dependency).
        p = SE.resolve_uninstall(["graywolf", "graywolf-api"],
                                 {"graywolf", "graywolf-api"}, self.reg)
        self.assertEqual(p.ordered, ["graywolf-api", "graywolf"])
        self.assertEqual(p.blocked, [])

    def test_independent_features_unaffected(self):
        p = SE.resolve_uninstall(["graywolf"], {"graywolf", "kiwix"}, self.reg)
        self.assertEqual(p.ordered, ["graywolf"])
        self.assertEqual(p.blocked, [])

    def test_lifeline_features_torn_down_last(self):
        # Lifeline features (positive teardown_priority) are removed after the
        # ordinary ones, ascending — pi-headless (30) dead last.
        reg = _reg({"server": [], "graywolf": ["server"], "adsb": ["server"],
                    "service-controls": ["server"], "webssh": ["server"],
                    "pi-headless": ["server"]},
                   priorities={"service-controls": 10, "webssh": 20, "pi-headless": 30})
        sel = ["graywolf", "adsb", "service-controls", "webssh", "pi-headless"]
        p = SE.resolve_uninstall(sel, set(sel), reg)
        self.assertEqual(p.ordered[-3:], ["service-controls", "webssh", "pi-headless"])
        self.assertEqual(set(p.ordered[:2]), {"graywolf", "adsb"})  # ordinary ones first

    def test_real_registry_orders_pi_headless_last(self):
        from common import setup_registry
        reg = setup_registry.build_registry(".")
        installed = {"server", "graywolf", "winlink", "adsb", "ai",
                     "service-controls", "webssh", "pi-headless"}
        p = SE.resolve_uninstall(sorted(installed - {"server"}), installed, reg)
        self.assertEqual(p.ordered[-1], "pi-headless")                   # dead last
        self.assertLess(p.ordered.index("service-controls"), p.ordered.index("webssh"))
        self.assertLess(p.ordered.index("webssh"), p.ordered.index("pi-headless"))


if __name__ == "__main__":
    unittest.main()
