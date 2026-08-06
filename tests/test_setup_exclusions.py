#!/usr/bin/env python3
"""The Setup page's mutually-exclusive feature groups.

Parsed straight out of setup.html because the table is inline JS with no import
seam. It has drifted twice (missing 'draws' entirely, then missing draws-gps
from the HAT group), and each drift let an operator select a combination that
breaks the box — so it is worth pinning even through a regex.
"""
import ast
import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
SETUP_HTML = os.path.join(os.path.dirname(_HERE), "server", "system", "setup.html")


def _groups():
    body = open(SETUP_HTML, encoding="utf-8").read()
    m = re.search(r"const SETUP_EXCLUSIVE_GROUPS = (\[.*?\]);", body, re.S)
    assert m, "SETUP_EXCLUSIVE_GROUPS not found in setup.html"
    # strip JS // comments, then it is valid Python list-of-lists
    src = re.sub(r"//[^\n]*", "", m.group(1))
    return [set(g) for g in ast.literal_eval(src)]


class ExclusiveGroupsTest(unittest.TestCase):
    def setUp(self):
        self.groups = _groups()

    def _group_with(self, *keys):
        return [g for g in self.groups if set(keys) <= g]

    def test_all_gpsd_sources_are_exclusive(self):
        """gps, gps-l76x and draws-gps each repoint gpsd at a different device."""
        self.assertTrue(self._group_with("gps", "gps-l76x", "draws-gps"))

    def test_dra_pi_excludes_every_draws_feature(self):
        """Two radio HATs cannot share the 40-pin header or the I2S bus.
        Installing DRA-Pi on a DRAWS box left the Pi with no sound cards at
        all (bench 2026-08-06)."""
        for draws_feature in ("draws-audio", "draws-gps"):
            self.assertTrue(self._group_with("dra-pi-rx-led", draws_feature),
                            f"dra-pi-rx-led must exclude {draws_feature}")

    def test_draws_features_are_not_exclusive_with_each_other(self):
        """draws-gps and draws-audio are the SAME board — both should be
        installable together."""
        for g in self.groups:
            self.assertFalse({"draws-gps", "draws-audio"} <= g,
                             "draws-gps and draws-audio must not exclude each other")

    def test_every_key_is_a_real_checkbox(self):
        """A typo'd key silently disables the exclusion it was meant to enforce."""
        body = open(SETUP_HTML, encoding="utf-8").read()
        present = set(re.findall(r'data-feature="([^"]+)"', body))
        for g in self.groups:
            for key in g:
                self.assertIn(key, present, f"{key!r} has no checkbox in setup.html")


if __name__ == "__main__":
    unittest.main()
