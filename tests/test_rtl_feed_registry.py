#!/usr/bin/env python3
"""The rtl-feed/rtl-sdr split in the Setup Orchestrator registry: two features,
the feed depends on the tools, and the feed resolves before adsb (mirroring the
setup.html checkbox order)."""
import unittest

from common import setup_registry as SR
from common import setup_engine as SE


class RtlFeedRegistryTest(unittest.TestCase):
    def setUp(self):
        self.reg = SR.build_registry("/tmp/oasis-test-root")

    def test_both_features_present(self):
        self.assertIn("rtl-sdr", self.reg)
        self.assertIn("rtl-sdr-feed", self.reg)

    def test_feed_depends_on_tools(self):
        self.assertIn("rtl-sdr", self.reg["rtl-sdr-feed"].dependencies)

    def test_tools_is_privileged(self):
        self.assertIn("rtl-sdr", SR.PRIVILEGED_FEATURES)

    def test_selecting_feed_pulls_tools_first(self):
        plan = SE.resolve_plan(["rtl-sdr-feed"], self.reg)
        o = plan.ordered_features
        self.assertIn("rtl-sdr", o)
        self.assertLess(o.index("rtl-sdr"), o.index("rtl-sdr-feed"))

    def test_feed_orders_before_adsb_in_dom_order(self):
        # setup.html lists rtl-feed before adsb; resolve_plan preserves selection
        # order, so the feed must land ahead of adsb.
        plan = SE.resolve_plan(["rtl-sdr-feed", "adsb"], self.reg)
        o = plan.ordered_features
        self.assertLess(o.index("rtl-sdr-feed"), o.index("adsb"))


if __name__ == "__main__":
    unittest.main()
