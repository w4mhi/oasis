"""The freshness vocabulary must be identical in Python and JavaScript.

The same condition is named in two places: _UPDATE_BADGE in common/diagnostics.py
(the Diagnostics Data Updates section) and LABEL in common/js/freshness.js (the
dashboard pill and the kiosk pill). Nothing at runtime connects them, so they
drift silently — which is exactly what happened: the kiosk read "DATA STALE"
while Diagnostics read "STALE" for one identical fact, and an operator comparing
the two screens had no way to know they meant the same thing.

Cross-language, so it is parsed rather than imported. Kept deliberately dumb: a
regex over the JS literal beats a JS runtime dependency in a Python suite.
"""
import os
import re
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from common import diagnostics as D  # noqa: E402

_JS = os.path.join(_REPO, "common", "js", "freshness.js")


def _js_labels():
    """Pull the LABEL object literal out of freshness.js."""
    with open(_JS, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"var LABEL\s*=\s*\{(.*?)\}\s*;", src, re.S)
    if not m:
        raise AssertionError("could not find `var LABEL = {...}` in "
                             "common/js/freshness.js — was it renamed?")
    return dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", m.group(1)))


class VocabularyMatchesTest(unittest.TestCase):
    def setUp(self):
        self.js = _js_labels()
        self.py = D._UPDATE_BADGE

    def test_the_same_states_are_named_in_both(self):
        self.assertEqual(set(self.js), set(self.py),
                         "one side knows a state the other does not")

    def test_every_state_uses_the_same_word(self):
        for state in sorted(self.py):
            self.assertEqual(
                self.js[state], self.py[state],
                f"state {state!r} is called {self.js[state]!r} in the browser "
                f"and {self.py[state]!r} on the Diagnostics page — an operator "
                f"comparing the two screens cannot tell they mean the same "
                f"thing")

    def test_labels_are_similar_width(self):
        """Not a style rule — a layout one.

        The kiosk pill sits between the stats bar and IMPERIAL. A label that
        swings from 3 to 13 characters reflows that whole row every time the
        state changes. (An earlier version of this test demanded SINGLE words,
        which bought nothing: CURRENT and DATA OK are both 7 characters. Width
        is what matters, not word count.)
        """
        widths = [len(w) for s, w in self.js.items() if s != "unconfigured"]
        self.assertLessEqual(
            max(widths) - min(widths), 3,
            f"label widths {sorted(widths)} vary too much; the kiosk row will "
            f"reflow on every state change")

    def test_stale_does_not_claim_the_updater_stopped(self):
        # "STALLED" reads as "the mechanism jammed" and sends the operator
        # hunting a fault that does not exist. The data is old; the loop is fine.
        self.assertNotIn("STALL", self.js["stale"].upper())
        self.assertNotIn("STALL", self.py["stale"].upper())

    def test_deferred_does_not_promise_a_tap_will_fetch_it(self):
        # A kiosk tap runs an ordinary pass, which deliberately does NOT fetch
        # a held-back large source. A "tap to update" label would lie.
        for word in (self.js["deferred"], self.py["deferred"]):
            self.assertNotIn("TAP", word.upper())

    def test_labels_are_upper_case(self):
        for state, word in self.js.items():
            self.assertEqual(word, word.upper(), state)

    def test_no_emoji_in_labels(self):
        # Pi OS Lite has no emoji font; these render on the kiosk.
        for state, word in list(self.js.items()) + list(self.py.items()):
            self.assertEqual([c for c in word if ord(c) >= 0x1F000], [], state)


if __name__ == "__main__":
    unittest.main()
